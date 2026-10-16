#!/usr/bin/env python3
"""Prepare a reproducible BOX engine from verified public vendor packages.

Preparation writes only its owned staging tree. It does not select an engine,
reload a host, remint bots, enable routines or change provider/model settings.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BASE = 'https://public-asphr-vm-daemon-bucket.s3.us-east-1.amazonaws.com/sand-host-bundle'
PREFIX = 'sand-host-bundle'
BASELINE = {'version': '861ef07', 'clientVersion': '0.67.0-pre.9',
            'archiveSha256': '451fb48f9485a780c15317895126f2e578f667a1351b5148a8fd447c808f3785',
            'sourceSha256': 'b538955ae208ed0c2e539b5701e8d1ab7accfafef9843ecb884fd55c31ae6c64'}


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def directory(path, allow_shared_parent=False):
    path = pathlib.Path(path)
    info = path.lstat()
    shared = (allow_shared_parent and info.st_uid == 0 and
              info.st_mode & stat.S_ISVTX and info.st_mode & stat.S_IWOTH and info.st_mode & stat.S_IXOTH)
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid != os.getuid() and not shared):
        raise RuntimeError('Preparation directory must be owned and not symlinked')
    return path


def validate_baseline(sync, engine, folder, metadata):
    if any(metadata.get(key) != value for key, value in BASELINE.items()):
        raise RuntimeError('Public BOX baseline differs from its reviewed identity')
    raw = engine.owned_bytes(folder / 'host-main.cjs', engine.MAX_BYTES)
    if hashlib.sha256(raw).hexdigest() != BASELINE['sourceSha256']:
        raise RuntimeError('Public BOX baseline source changed')
    engine.require_runner(raw.decode('utf8'))
    return raw


def wrap_source(package_root, source, destination, node):
    result = subprocess.run([str(node), '--experimental-strip-types', str(HERE / 'prepare-local-engine-wrap.mjs'),
                             str(package_root), str(source), str(destination)],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Prepared engine failed canonical payload wrapping')
    proof = json.loads(result.stdout)
    if (not isinstance(proof, dict) or proof.get('runtimePath') != str(package_root / 'payload/runtime.cjs') or
            not isinstance(proof.get('payloadFingerprint'), str) or
            not re.fullmatch('[0-9a-f]{16}', proof['payloadFingerprint'])):
        raise RuntimeError('Prepared payload wrapper proof differs from the installed package')
    return proof


def prepare(stage_root, package_root, host_main, node, version=None):
    """Return a completed pair/report; never publish an active engine policy."""
    stage_root = pathlib.Path(stage_root)
    if not stage_root.is_absolute():
        raise RuntimeError('Preparation staging path must be absolute')
    # The caller selects an existing owned parent. Do not traverse/create an
    # arbitrary chain or resolve a symlink into somebody else's staging tree.
    directory(stage_root.parent, allow_shared_parent=True)
    stage_root.mkdir(mode=0o700, exist_ok=True)
    directory(stage_root)
    package_root = directory(pathlib.Path(package_root))
    if not package_root.is_absolute() or not pathlib.Path(host_main).is_absolute():
        raise RuntimeError('Package and native host paths must be absolute')
    sync, engine, recovery = load('local-upstream-sync'), load('local-engine'), load('computer-recovery')
    engine.owned_bytes(package_root / 'package.json', 65536)
    engine.owned_bytes(host_main, engine.MAX_BYTES)
    if version is not None and (not isinstance(version, str) or not sync.VERSION.fullmatch(version)):
        raise RuntimeError('Invalid vendor version')
    lock = os.open(stage_root / 'prepare.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted engine preparation lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return prepare_locked(stage_root, package_root, pathlib.Path(host_main), node, version,
                              sync, engine, recovery)
    finally:
        os.close(lock)


def prepare_locked(root, package_root, host, node, version, sync, engine, recovery):
    if version is None:
        version = sync.fetch_text(BASE + '/sand-host-bundle-latest.version', 128)
    if not sync.VERSION.fullmatch(version):
        raise RuntimeError('Invalid latest vendor version')
    upstream, vendor = sync.stage_vendor(root, version, BASE, PREFIX)
    if sync.compatibility_recipe(vendor) is None:
        raise RuntimeError('Latest vendor host has no reviewed BOX compatibility recipe; no engine selected')
    baseline, metadata = sync.stage_vendor(root, BASELINE['version'], BASE, PREFIX)
    raw = validate_baseline(sync, engine, baseline, metadata)
    work = pathlib.Path(tempfile.mkdtemp(prefix='prepared-', dir=root))
    # The original installed baseline already contained these transforms. A
    # fresh vendor source must receive them BEFORE linking; otherwise migration
    # guards disappear from the retained modules and cannot be patched later.
    canonical = recovery.patched(raw.decode('utf8'))
    if recovery.patched(canonical) != canonical:
        raise RuntimeError('Prepared BOX baseline is not a canonical fixed point')
    canonical_path = work / 'baseline.cjs'
    recovery.atomic_bytes(canonical_path, canonical.encode('utf8'))
    engine.require_runner(canonical)
    baseline_wrapper = wrap_source(package_root, canonical_path, work / 'baseline-wrapped.cjs', node)
    baseline_wrapped = engine.owned_bytes(work / 'baseline-wrapped.cjs', engine.MAX_BYTES)
    sync.syntax(baseline_wrapped, node)
    if recovery.patched(baseline_wrapped.decode('utf8')).encode('utf8') != baseline_wrapped:
        raise RuntimeError('Prepared baseline wrapper is not a canonical fixed point')
    source, report = sync.compose(root, canonical_path, upstream / 'host-main.cjs', work / 'composition', node,
                                  package_root=package_root, host_main=host)
    stock = recovery.patched(source.decode('utf8'))
    if recovery.patched(stock) != stock:
        raise RuntimeError('Prepared latest BOX engine is not a canonical fixed point')
    engine.require_runner(stock)
    stock_path = work / 'stock.cjs'
    recovery.atomic_bytes(stock_path, stock.encode('utf8'))
    sync.syntax(stock.encode('utf8'), node)
    wrapper = wrap_source(package_root, stock_path, work / 'wrapped.cjs', node)
    if baseline_wrapper != wrapper:
        raise RuntimeError('Installed payload changed during engine preparation')
    wrapped = engine.owned_bytes(work / 'wrapped.cjs', engine.MAX_BYTES)
    sync.syntax(wrapped, node)
    if recovery.patched(wrapped.decode('utf8')).encode('utf8') != wrapped:
        raise RuntimeError('Prepared wrapped engine is not a canonical fixed point')
    digest = hashlib.sha256(stock.encode('utf8')).hexdigest()
    # Check the FINAL transformed source against the owner's installed native
    # dependencies, without executing the host boot boundary or sending a turn.
    check = subprocess.run([str(node), str(HERE / 'verify-latest-local-engine.cjs'),
                            str(stock_path), digest, str(host)], capture_output=True, text=True, timeout=60)
    if check.returncode:
        raise RuntimeError('Final prepared engine failed native offline contracts')
    contracts = json.loads(check.stdout)
    if (not isinstance(contracts, dict) or contracts.get('loaded') is not True or
            contracts.get('latestDatabasePersistence') is not True):
        raise RuntimeError('Final prepared engine has incomplete native offline proof')
    result = {'schemaVersion': 1, 'state': 'prepared', 'activated': False,
              'nativeDeliveryVerified': False, 'liveHostChanged': False,
              'baseline': metadata, 'upstream': vendor, 'directory': str(work),
              'baselineCanonicalSha256': hashlib.sha256(canonical.encode('utf8')).hexdigest(),
              'baselineWrappedSha256': hashlib.sha256(baseline_wrapped).hexdigest(),
              'stockSha256': digest, 'wrappedSha256': hashlib.sha256(wrapped).hexdigest(),
              'payload': wrapper, 'nativeOfflineContracts': contracts,
              'retainedCompatibilityModules': len(report['retainedModules'])}
    stock_path.chmod(0o400)
    (work / 'wrapped.cjs').chmod(0o400)
    canonical_path.chmod(0o400)
    (work / 'baseline-wrapped.cjs').chmod(0o400)
    sync.write_json(work / 'preparation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-root', type=pathlib.Path,
                        default=pathlib.Path('/home/box/sand-data/openbot-engine-preparation'))
    parser.add_argument('--package-root', type=pathlib.Path, default=HERE.parent.parent)
    parser.add_argument('--host-main', type=pathlib.Path, default=pathlib.Path('/opt/sand/sand-host/host-main.cjs'))
    parser.add_argument('--node', default='/exec-daemon/node')
    parser.add_argument('--upstream-version')
    args = parser.parse_args()
    try:
        result = prepare(args.stage_root, args.package_root, args.host_main, args.node, args.upstream_version)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'failed', 'activated': False, 'error': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
