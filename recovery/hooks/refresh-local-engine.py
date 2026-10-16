#!/usr/bin/env python3
"""Upgrade a trusted engine pair, committing both hashes through one manifest."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import subprocess
import tempfile
import uuid

HERE = pathlib.Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def atomic(path, data, expected):
    descriptor, name = tempfile.mkstemp(prefix='.engine-manifest-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno()); os.fchmod(output.fileno(), 0o600)
        if path.read_bytes() != expected:
            raise RuntimeError('Engine policy changed concurrently; generation was not activated')
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def refresh(root, stock_backup=None, wrapped_backup=None, apply=False, transform=None, validate=None):
    engine = load('local-engine')
    path = root / 'openbot-local-engine.json'
    before = engine.owned_bytes(path, 16384)
    policy = engine.policy(root)
    if policy is None:
        raise RuntimeError('No active pin selected for an engine upgrade')
    transform = transform or load('computer-recovery').patched
    if validate is None:
        def validate(source):
            result = subprocess.run(['/exec-daemon/node', '--check'], input=source,
                                    text=True, capture_output=True, timeout=45)
            if result.returncode:
                raise RuntimeError('Canonical engine syntax check failed')
    directory = engine.artifact_directory(root, policy)
    candidates = {}
    for kind, backup in [('stock', stock_backup), ('wrapped', wrapped_backup)]:
        source_path = pathlib.Path(backup) if backup else directory / (kind + '.cjs')
        raw = engine.owned_bytes(source_path, engine.MAX_BYTES)
        if backup and source_path.suffix == '.gz':
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as archive:
                raw = archive.read(engine.MAX_BYTES + 1)
        if len(raw) > engine.MAX_BYTES or hashlib.sha256(raw).hexdigest() != policy[kind + 'Sha256']:
            raise RuntimeError('Engine input differs from the trusted manifest; no upgrade activated')
        source = transform(raw.decode('utf8'))
        if transform(source) != source:
            raise RuntimeError('Canonical engine transformation is not a fixed point')
        engine.require_runner(source)
        if source.count('/* openbot-stock-wrap */') != (1 if kind == 'wrapped' else 0):
            raise RuntimeError('Canonical engine wrap kind changed')
        validate(source)
        candidates[kind] = source.encode('utf8')
    report = {'state': 'validated', 'hashes': {k: hashlib.sha256(v).hexdigest() for k, v in candidates.items()},
              'activated': False, 'hostReloadRequested': False}
    if not apply:
        return report
    if 'generationId' in policy and all(report['hashes'][k] == policy[k + 'Sha256'] for k in candidates):
        return {**report, 'state': 'already-current', 'generationId': policy['generationId']}
    return activate_pair(root, candidates, before)


def activate_pair(root, candidates, expected, metadata=None, before_activate=None, *, select=True):
    """Activate an already checked pair; callers supply its exact provenance."""
    engine = load('local-engine')
    path = root / 'openbot-local-engine.json'
    policy = engine.policy(root)
    if policy is None or engine.owned_bytes(path, 16384) != expected:
        raise RuntimeError('Engine policy changed before the pair was staged')
    if set(candidates) != {'stock', 'wrapped'}:
        raise RuntimeError('An engine generation requires both complete sources')
    if metadata and set(metadata) & {'schemaVersion', 'enabled', 'stockSha256', 'wrappedSha256', 'generationId'}:
        raise RuntimeError('Provenance cannot replace engine authority fields')
    for kind, raw in candidates.items():
        if not isinstance(raw, bytes) or not 0 < len(raw) <= engine.MAX_BYTES:
            raise RuntimeError('Engine candidate exceeds its bounds')
        source = raw.decode('utf8')
        engine.require_runner(source)
        if source.count('/* openbot-stock-wrap */') != (1 if kind == 'wrapped' else 0):
            raise RuntimeError('Engine candidate wrap kind differs')
    hashes = {key: hashlib.sha256(raw).hexdigest() for key, raw in candidates.items()}
    report = {'hashes': hashes, 'hostReloadRequested': False}
    # Existing generation directories remain immutable. Fully stage the new
    # pair before an atomic manifest points readers to both files and hashes.
    generations = root / 'openbot-local-engine/generations'
    generations.mkdir(mode=0o700, exist_ok=True)
    generation = uuid.uuid4().hex
    info = generations.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Engine generations root must be an owned regular directory')
    folder = generations / generation; folder.mkdir(mode=0o700)
    for kind, data in candidates.items():
        fd = os.open(folder / (kind + '.cjs'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
    fd = os.open(folder / 'previous-policy.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(expected); output.flush(); os.fsync(output.fileno())
    for directory in [folder, generations, generations.parent]:
        fd = os.open(directory, os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    updated = {**policy, **(metadata or {}), 'generationId': generation,
               **{k + 'Sha256': report['hashes'][k] for k in candidates}}
    manifest = (json.dumps(updated, indent=2) + '\n').encode()
    if not select:
        # Official updates save a complete immutable local release without
        # selecting it, changing the live host/version, or sending a BOX turn.
        if engine.owned_bytes(path, 16384) != expected:
            raise RuntimeError('Engine policy changed while the pair was staged')
        return {**report, 'state': 'saved', 'generationId': generation,
                'manifest': updated, 'activated': False}
    if before_activate is not None:
        before_activate(manifest)
    atomic(path, manifest, expected)
    return {**report, 'state': 'activated', 'generationId': generation, 'activated': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    parser.add_argument('--trusted-stock-backup', type=pathlib.Path)
    parser.add_argument('--trusted-wrapped-backup', type=pathlib.Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true', help='Validate the upgrade without writing files (default)')
    action.add_argument('--apply', action='store_true', help='Activate the validated generation through the manifest')
    args = parser.parse_args()
    result = refresh(args.root, args.trusted_stock_backup, args.trusted_wrapped_backup, args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
