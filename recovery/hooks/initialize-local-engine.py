#!/usr/bin/env python3
"""Select a first local engine from a freshly reproduced prepared pair.

Defaults to a plan. Applying creates one complete manifest exclusively; it
never replaces an existing policy or reloads the host. The owned recovery
integration can load the selected engine separately when idle.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import uuid

HERE = pathlib.Path(__file__).resolve().parent
FILES = {'stock.cjs': 'stockSha256', 'wrapped.cjs': 'wrappedSha256',
         'baseline.cjs': 'baselineCanonicalSha256', 'baseline-wrapped.cjs': 'baselineWrappedSha256'}


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_directory(path):
    path = pathlib.Path(path)
    info = path.lstat()
    if not path.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Engine binding requires an absolute owned nonsymlinked directory')
    return path


def require_absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise RuntimeError('Engine policy already exists; use the reviewed upgrade path instead')


def prepared(directory):
    directory = owned_directory(directory)
    engine, preparation, sync = load('local-engine'), load('prepare-local-engine'), load('local-upstream-sync')
    raw = engine.owned_bytes(directory / 'preparation.json', 2 * 1024 * 1024)
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1 or value.get('state') != 'prepared' or
            value.get('directory') != str(directory) or
            any(value.get(key) is not False for key in ('activated', 'liveHostChanged', 'nativeDeliveryVerified'))):
        raise RuntimeError('Incomplete or unsupported engine preparation report')
    baseline, vendor = value.get('baseline'), value.get('upstream')
    contracts = value.get('nativeOfflineContracts')
    if (not isinstance(contracts, dict) or contracts.get('loaded') is not True or
            contracts.get('latestDatabasePersistence') is not True):
        raise RuntimeError('Engine preparation has incomplete native contract proof')
    if (not isinstance(baseline, dict) or
            any(baseline.get(k) != v for k, v in preparation.BASELINE.items()) or
            not isinstance(vendor, dict) or not isinstance(vendor.get('sourceSha256'), str) or
            sync.compatibility_recipe(vendor) is None):
        raise RuntimeError('Engine preparation has unreviewed provenance')
    sources = {}
    for name, key in FILES.items():
        source = engine.owned_bytes(directory / name, engine.MAX_BYTES)
        if hashlib.sha256(source).hexdigest() != value.get(key):
            raise RuntimeError('Prepared engine differs from its report')
        engine.require_runner(source.decode('utf8'))
        if source.count(b'/* openbot-stock-wrap */') != int('wrapped' in name):
            raise RuntimeError('Prepared engine wrap kind differs')
        sources[name] = source
    return value, sources, raw


def durable_directory(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_exclusive(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), mode)


def publish_initial(path, raw):
    """Atomic creation rather than replacement protects a concurrent owner."""
    fd, temporary = tempfile.mkstemp(prefix='.initial-engine-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), 0o600)
        os.link(temporary, path, follow_symlinks=False)
        durable_directory(path.parent)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


def select_pair(root, report, sources):
    path = root / 'openbot-local-engine.json'
    require_absent(path)
    engine_root = root / 'openbot-local-engine'
    engine_root.mkdir(mode=0o700, exist_ok=True)
    owned_directory(engine_root)
    generations = engine_root / 'generations'
    generations.mkdir(mode=0o700, exist_ok=True)
    owned_directory(generations)
    policies = []
    for stock, wrapped, client in [('baseline.cjs', 'baseline-wrapped.cjs', report['baseline']['clientVersion']),
                                    ('stock.cjs', 'wrapped.cjs', report['upstream']['clientVersion'])]:
        generation = uuid.uuid4().hex
        folder = generations / generation
        folder.mkdir(mode=0o700)
        for name, data in [('stock.cjs', sources[stock]), ('wrapped.cjs', sources[wrapped])]:
            write_exclusive(folder / name, data, 0o400)
        policy = {'schemaVersion': 1, 'enabled': True, 'generationId': generation,
                  'clientVersion': client, 'stockSha256': hashlib.sha256(sources[stock]).hexdigest(),
                  'wrappedSha256': hashlib.sha256(sources[wrapped]).hexdigest(), 'nativeSendVerified': False}
        if policies:
            policy.update(upstreamSyncStrategy='latest-host-with-box-ports',
                          upstreamVersion=report['upstream']['version'],
                          hostVersion=report['upstream']['version'],
                          upstreamSourceSha256=report['upstream']['sourceSha256'],
                          boxCompatibilityBase=policies[0], origin='verified-public-preparation')
            write_exclusive(folder / 'previous-policy.json', (json.dumps({'schemaVersion': 1, 'enabled': False}) + '\n').encode(), 0o600)
        durable_directory(folder)
        policies.append(policy)
    durable_directory(generations)
    durable_directory(engine_root)
    encoded = (json.dumps(policies[-1], indent=2) + '\n').encode()
    publish_initial(path, encoded)
    return policies[-1]


def initialize(root, directory, package_root, host_main, node, apply=False):
    root = owned_directory(root)
    path = root / 'openbot-local-engine.json'
    require_absent(path)  # Includes disabled policies and dangling symlinks.
    fd = os.open(root / 'computer-recovery.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted Computer recovery lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_absent(path)
        report, sources, before = prepared(directory)
        # A recorded Boolean is not proof. Reconstruct from digest-verified
        # vendor archives and this installation's CURRENT payload/dependencies.
        fresh = load('prepare-local-engine').prepare(pathlib.Path(directory).parent, pathlib.Path(package_root),
                pathlib.Path(host_main), node, report['upstream']['version'])
        if (not isinstance(fresh, dict) or fresh.get('state') != 'prepared' or
                any(fresh.get(key) != report.get(key) for key in [*FILES.values(), 'baseline', 'upstream', 'payload'])):
            raise RuntimeError('Prepared engine cannot be reproduced with this installed package')
        reproduced, _, _ = prepared(fresh['directory'])
        if reproduced != fresh:
            raise RuntimeError('Reproduced preparation differs from its completed report')
        again, checked, current = prepared(directory)
        if current != before or checked != sources:
            raise RuntimeError('Engine preparation changed concurrently')
        result = {'state': 'validated', 'activated': False, 'hostReloadRequested': False,
                  'nativeDeliveryVerified': False, 'upstreamVersion': again['upstream']['version'],
                  'stockSha256': again['stockSha256'], 'wrappedSha256': again['wrappedSha256'],
                  'reproducedDirectory': fresh['directory']}
        if apply:
            policy = select_pair(root, again, checked)
            result.update(state='selected', activated=True, generationId=policy['generationId'])
        return result
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    parser.add_argument('--prepared-directory', type=pathlib.Path, required=True)
    parser.add_argument('--package-root', type=pathlib.Path, default=HERE.parent.parent)
    parser.add_argument('--host-main', type=pathlib.Path, default=pathlib.Path('/opt/sand/sand-host/host-main.cjs'))
    parser.add_argument('--node', default='/exec-daemon/node')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true', help='Reproduce and validate without selecting a pin (default)')
    action.add_argument('--apply', action='store_true', help='Select the first complete engine manifest exclusively')
    args = parser.parse_args()
    try:
        result = initialize(args.root, args.prepared_directory, args.package_root, args.host_main, args.node, args.apply)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'failed', 'activated': False, 'error': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
