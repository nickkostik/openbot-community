#!/usr/bin/env python3
"""Move user messaging context into a supported model-readable store.

Keep a compatibility alias for existing host readers/writers. There is one
authoritative directory: arming, receipts and reading positions are not copied
or reset. No bot turns, outgoing messages or host reloads are started here.
"""
import argparse
import ctypes
import fcntl
import json
import os
import pathlib
import re
import stat
import tempfile
import time
import uuid

SD = pathlib.Path('/home/box/sand-data')
TARGET = pathlib.Path('workflows/openbot-fleet/imessage-context')
MODEL_PATH = '/home/box/agent-data/' + str(TARGET)
MAX_FILES = 50000
MAX_BYTES = 512 * 1024 * 1024
PRIVATE_NAME = re.compile(r'(?i)(?:^|[_.-])(?:auth|credentials?|secrets?|password|token|env)(?:$|[_.-])')


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Context directory must be owned and not symlinked')
    return info


def inventory(root):
    directory(root)
    count = total = 0
    for path in root.rglob('*'):
        info = path.lstat()
        if info.st_uid != os.getuid():
            raise RuntimeError('Context contains a foreign owner')
        if path.name.startswith('.') or PRIVATE_NAME.search(path.name):
            raise RuntimeError('Context contains an unsupported or credential path')
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError('Context contains a symlink or special file')
        if (path.suffix not in ('.json', '.md', '.vcf') or path.name.startswith('.')
                or PRIVATE_NAME.search(path.name)):
            raise RuntimeError('Context contains an unsupported or credential file')
        count += 1
        total += info.st_size
        if count > MAX_FILES or total > MAX_BYTES:
            raise RuntimeError('Context exceeds migration bounds')
    return {'files': count, 'bytes': total}


def marker_bytes(path):
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 16384):
        raise RuntimeError('Migration marker must be a private owned regular file')
    return path.read_bytes()


def save_marker(path, value, expected):
    data = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.context-move-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        if marker_bytes(path) != expected:
            raise RuntimeError('Migration marker changed concurrently')
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    return data


def sync_directory(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_no_replace(source, target):
    """Claim an unoccupied destination atomically on the Linux box."""
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as error:
        raise RuntimeError('Context migration requires atomic no-replace rename support') from error
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    # AT_FDCWD=-100, RENAME_NOREPLACE=1. Never fall back to an overwriting rename.
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def layout(sd):
    sd = pathlib.Path(sd)
    directory(sd)
    source, target = sd / 'imessage-context', sd / TARGET
    for parent in (sd / 'workflows', target.parent):
        if parent.exists() or parent.is_symlink():
            directory(parent)
    marker = sd / 'recovery-snapshots/imessage-context-migration.json'
    saved = marker_bytes(marker)
    record = json.loads(saved) if saved is not None else None
    if source.is_symlink():
        if source.lstat().st_uid != os.getuid():
            raise RuntimeError('Legacy context alias has a foreign owner')
        link = pathlib.Path(os.path.abspath(source.parent / os.readlink(source)))
        if link != target:
            raise RuntimeError('Legacy context points at an unexpected destination')
        directory(target)
        return {'status': 'unchanged', **inventory(target), 'path': MODEL_PATH}, source, target, marker, record
    if source.exists():
        if target.exists() or target.is_symlink():
            raise RuntimeError('Two context directories exist; do not merge or overwrite them')
        info = directory(source)
        return {'status': 'planned', **inventory(source), 'path': MODEL_PATH,
                'sourceDevice': info.st_dev, 'sourceInode': info.st_ino}, source, target, marker, record
    if target.exists() or target.is_symlink():
        info = directory(target)
        if (not isinstance(record, dict) or record.get('version') != 1
                or record.get('source') != str(source) or record.get('target') != str(target)
                or record.get('sourceDevice') != info.st_dev or record.get('sourceInode') != info.st_ino
                or record.get('phase') not in ('prepared', 'complete')):
            raise RuntimeError('Context destination exists without migration provenance')
        return {'status': 'repair-alias', **inventory(target), 'path': MODEL_PATH}, source, target, marker, record
    return {'status': 'not-configured'}, source, target, marker, record


def migrate(sd=SD, apply=False):
    sd = pathlib.Path(sd)
    result, source, target, marker, record = layout(sd)
    if not apply or result['status'] in ('unchanged', 'not-configured'):
        return result
    snapshots = sd / 'recovery-snapshots'
    if not snapshots.exists() and not snapshots.is_symlink():
        snapshots.mkdir(mode=0o700)
    directory(snapshots)
    lock_path = snapshots / 'imessage-context-migration.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Migration lock is not owned')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, source, target, marker, record = layout(sd)
        if result['status'] in ('unchanged', 'not-configured'):
            return result
        for parent in (sd / 'workflows', target.parent):
            if not parent.exists() and not parent.is_symlink():
                parent.mkdir(mode=0o755)
            directory(parent)
        if result['status'] == 'planned' and source.stat().st_dev != target.parent.stat().st_dev:
            raise RuntimeError('Context move must stay on the same filesystem')
        expected = marker_bytes(marker)
        if result['status'] == 'planned':
            record = {'version': 1, 'source': str(source), 'target': str(target),
                      'sourceDevice': result['sourceDevice'], 'sourceInode': result['sourceInode'],
                      'phase': 'prepared', 'atMs': int(time.time() * 1000)}
        alias = source.parent / ('.imessage-context-alias-' + uuid.uuid4().hex)
        moved = False
        try:
            os.symlink(os.path.relpath(target, source.parent), alias)
            if result['status'] == 'planned':
                expected = save_marker(marker, record, expected)
                if target.exists() or target.is_symlink():
                    raise RuntimeError('Context destination changed concurrently')
                current = directory(source)
                if (current.st_dev, current.st_ino) != (record['sourceDevice'], record['sourceInode']):
                    raise RuntimeError('Context source changed concurrently')
                rename_no_replace(source, target)
                moved = True
                sync_directory(target.parent)
            if source.exists() or source.is_symlink():
                raise RuntimeError('Legacy context location changed concurrently')
            # symlink creation refuses an occupied name, unlike replace().
            # A concurrent writer must never be overwritten to install the alias.
            os.symlink(os.readlink(alias), source)
            sync_directory(source.parent)
            info = directory(target)
            if (info.st_dev, info.st_ino) != (record['sourceDevice'], record['sourceInode']):
                raise RuntimeError('Context directory identity changed')
            save_marker(marker, {**record, 'phase': 'complete'}, expected)
        except Exception:
            if moved and not source.exists() and not source.is_symlink():
                rename_no_replace(target, source)
                sync_directory(source.parent)
                sync_directory(target.parent)
            raise
        finally:
            alias.unlink(missing_ok=True)
        return {'status': 'migrated' if moved else 'alias-repaired', **inventory(target),
                'path': MODEL_PATH, 'legacyHostAlias': str(source), 'dataCopied': False}
    finally:
        os.close(fd)


def snapshot_compatibility_state(sd, destination):
    """Retain the checked legacy alias and private provenance after context copy."""
    sd, destination = pathlib.Path(sd), pathlib.Path(destination)
    if not (sd / 'imessage-context').is_symlink():
        return False
    result, source, target, marker, record = layout(sd)
    if result['status'] != 'unchanged':
        raise RuntimeError('Messaging context must have a checked alias before snapshot')
    directory(destination)
    directory(destination / TARGET)
    directory(destination / TARGET.parent)
    directory(destination / 'workflows')
    saved = marker_bytes(marker)
    snapshots = destination / 'recovery-snapshots'
    if saved is not None:
        if not snapshots.exists() and not snapshots.is_symlink():
            snapshots.mkdir(mode=0o700)
        directory(snapshots)
        fd = os.open(snapshots / marker.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(saved)
            handle.flush()
            os.fsync(handle.fileno())
    os.symlink(os.path.relpath(target, sd), destination / source.name)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Perform the verified move; default is read-only')
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply)))
