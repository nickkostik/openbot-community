#!/usr/bin/env python3
"""Release only idle hidden desktops belonging to recorded retired bot IDs.

The primary desktop, current bots, browser profiles and all bot data remain.
The existing native window lock and owner token protect each closure. This is
not a general process killer or a bot migration tool.
"""
import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import re
import socket
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid

SD = pathlib.Path('/home/box/sand-data')
SEATS = pathlib.Path('/home/box/.sand-window-assignments.json')
LOCKS = pathlib.Path('/tmp/sand-window-locks.d')
TOKENS = pathlib.Path('/tmp/sand-window-tokens.d')
STOP = pathlib.Path('/usr/local/bin/stop-window')
LOW_MEMORY_BYTES = 1024 * 1024 * 1024
MIN_FREE_WINDOWS = 12


def owned_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024:
        raise RuntimeError('Expected a bounded owned regular metadata file')
    return json.loads(path.read_bytes())


def uuid_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise RuntimeError('Invalid bot identity in migration journal')
    return value


def identities(journal):
    current = set()
    retired = set()
    for entry in journal['entries']:
        if entry.get('newId'):
            current.add(uuid_id(entry['newId']))
        if entry.get('phase') == 'active':
            retired.add(uuid_id(entry['oldId']))
    for generation in journal.get('previousGenerations', []):
        for entry in generation['entries']:
            for key in ('newId', 'oldId'):
                if entry.get(key):
                    retired.add(uuid_id(entry[key]))
    return current, retired - current


def candidates(journal, table, rows):
    current, retired = identities(journal)
    assignments = table['assignments']
    tokens = table['tokens']
    native = {uuid_id(row['id']): row for row in rows}
    if len(native) != len(rows):
        raise RuntimeError('Duplicate native bot identity')
    counts = {}
    for index in assignments.values():
        if type(index) is not int:
            raise RuntimeError('Invalid native desktop index')
        counts[index] = counts.get(index, 0) + 1
    result = []
    for aid, index in assignments.items():
        row = native.get(aid)
        token = tokens.get(aid)
        if (aid in retired and aid not in current and index > 1 and counts[index] == 1
                and row is not None and row.get('isHiddenFromSidebar') is True
                and row.get('isRunning') is False
                and isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]+', token)):
            result.append({'id': aid, 'index': index, 'token': token})
    return sorted(result, key=lambda item: item['index'])


def native_rows():
    token = owned_json(SD / 'gateway.json')['token']
    request = urllib.request.Request('http://127.0.0.1:1340/api/listAgents',
        data=b'{}', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=12) as response:
        result = json.load(response)
    return result if isinstance(result, list) else result['agents']


def available_memory():
    fields = dict(line.split(':', 1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
    return int(fields['MemAvailable'].split()[0]) * 1024


def capacity(journal, table, max_windows=None):
    """Reserve room for current bots, even when RAM is healthy."""
    current, _ = identities(journal)
    maximum = int(os.environ.get('SAND_BOX_MAX_WINDOWS', '100')) if max_windows is None else max_windows
    if type(maximum) is not int or maximum < 2:
        raise RuntimeError('Invalid native maximum desktop count')
    occupied = {index for index in table['assignments'].values()
                if type(index) is int and 2 <= index <= maximum}
    free = maximum - 1 - len(occupied)
    wanted = min(maximum - 1, max(MIN_FREE_WINDOWS, len(current - table['assignments'].keys())))
    return {'freeWindows': free, 'requiredFreeWindows': wanted, 'pressured': free < wanted}


def window_alive(index):
    """Absent owner files alone do not establish that a display is stopped."""
    endpoints = [(socket.AF_UNIX, '/tmp/.X11-unix/X' + str(index)),
                 (socket.AF_UNIX, '\0/tmp/.X11-unix/X' + str(index))]
    endpoints.extend((socket.AF_INET, ('127.0.0.1', base + index)) for base in (14000, 13600, 5900))
    for family, address in endpoints:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            try:
                probe.connect(address)
            except (FileNotFoundError, ConnectionRefusedError):
                continue
            except OSError:
                # An ambiguous, inaccessible or slow endpoint is not safe to release.
                return True
            return True
    return False


def release_seat(item):
    """CAS one retired reservation under the same lock as native seat writes.

    The caller holds the display lock. Stop first, then remove the row: a new
    claimant cannot start on this display while its old desktop is stopping.
    """
    path = LOCKS / ('file' + str(SEATS).replace('/', '_'))
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Native seat-table lock is not owned')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'table-busy'
        table = owned_json(SEATS)
        original = SEATS.read_bytes()
        if json.loads(original) != table:
            return 'table-changed'
        if item not in candidates(owned_json(SD / 'openbot-box-remint.json'), table, native_rows()):
            return 'changed'
        if any(aid != item['id'] and token == item['token'] for aid, token in table['tokens'].items()):
            return 'shared-token'
        backup = SD / 'recovery-snapshots'
        if backup.is_symlink() or not backup.is_dir() or backup.stat().st_uid != os.getuid():
            raise RuntimeError('Seat backup directory is not owned')
        receipt = backup / ('retired-seat-before-' + str(time.time_ns()) + '.json')
        with receipt.open('xb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        del table['assignments'][item['id']]
        del table['tokens'][item['id']]
        table.get('unstarted', {}).pop(item['token'], None)
        fd, temporary = tempfile.mkstemp(prefix='.retired-seat-cas-', dir=SEATS.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write((json.dumps(table, separators=(',', ':')) + '\n').encode())
                handle.flush()
                os.fsync(handle.fileno())
            if owned_json(SEATS) != json.loads(original) or SEATS.read_bytes() != original:
                return 'table-changed'
            os.replace(temporary, SEATS)
            parent_fd = os.open(SEATS.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            pathlib.Path(temporary).unlink(missing_ok=True)
        return 'released'


def preserving_stop(source):
    """Use native desktop shutdown, excluding its browser-profile deletion."""
    deletion = '\t"${CHROME_PROFILE}" \\\n'
    if (source.count(deletion) != 1 or 'refusing to stop primary desktop' not in source
            or 'sand_desktop_unregister_group' not in source or 'WINDOW_TOKEN_DIR=' not in source):
        raise RuntimeError('Unrecognized native stop script; no desktop closed')
    result = source.replace(deletion, '')
    if '"${CHROME_PROFILE}"' in result:
        raise RuntimeError('Unexpected remaining browser-profile operation')
    return result


def close_candidate(item, script):
    # Native launch/stop operations use this exact per-display lock. Keep it
    # held across metadata/owner revalidation and graceful native shutdown.
    info = LOCKS.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Native window lock directory is not owned')
    descriptor = os.open(LOCKS / str(item['index']), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        lock_info = os.fstat(lock.fileno())
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid():
            raise RuntimeError('Native window lock is not owned')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'busy'
        refreshed = candidates(owned_json(SD / 'openbot-box-remint.json'), owned_json(SEATS), native_rows())
        if item not in refreshed:
            return 'changed'
        owner = TOKENS / str(item['index'])
        try:
            owner_info = owner.lstat()
        except FileNotFoundError:
            if window_alive(item['index']):
                return 'unowned-live-window'
            return release_seat(item)
        if (not stat.S_ISREG(owner_info.st_mode) or owner_info.st_uid != os.getuid()
                or owner.read_text().strip() != item['token']):
            return 'owner-changed'
        result = subprocess.run(['/bin/bash', str(script), str(item['index'])],
                                capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError('Native desktop shutdown failed')
        if window_alive(item['index']):
            return 'shutdown-pending'
        state = release_seat(item)
        return 'closed' if state == 'released' else state


def reap(limit=2, pressure_only=True):
    before = available_memory()
    if not (SD / 'openbot-box-remint.json').exists():
        return {'state': 'not-configured'}
    journal = owned_json(SD / 'openbot-box-remint.json')
    table = owned_json(SEATS)
    slots = capacity(journal, table)
    if pressure_only and before >= LOW_MEMORY_BYTES and not slots['pressured']:
        return {'state': 'sufficient-capacity', 'availableBytes': before, **slots}
    plan = candidates(journal, table, native_rows())
    info = STOP.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
        raise RuntimeError('Native stop script is not trusted')
    original = STOP.read_text()
    source = preserving_stop(original)
    backup = SD / 'recovery-snapshots'
    if backup.is_symlink() or not backup.is_dir() or backup.stat().st_uid != os.getuid():
        raise RuntimeError('Shutdown receipts directory is not owned')
    fd, temporary = tempfile.mkstemp(prefix='.retired-window-stop-', dir=backup)
    result = {'state': 'checked', 'availableBeforeBytes': before, 'capacityBefore': slots,
              'closed': [], 'released': [], 'skipped': [],
              'nativeStopSha256': hashlib.sha256(original.encode()).hexdigest()}
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        syntax = subprocess.run(['/bin/bash', '-n', temporary], capture_output=True, timeout=3)
        if syntax.returncode:
            raise RuntimeError('Profile-preserving shutdown syntax failed')
        for item in plan:
            if len(result['released']) >= limit:
                break
            if (pressure_only and available_memory() >= LOW_MEMORY_BYTES
                    and not capacity(journal, owned_json(SEATS))['pressured']):
                break
            state = close_candidate(item, pathlib.Path(temporary))
            record = {'id': item['id'], 'index': item['index'], 'state': state}
            if state in ('closed', 'released'):
                result['released'].append(record)
                if state == 'closed':
                    result['closed'].append(record)
            else:
                result['skipped'].append(record)
        result['availableAfterBytes'] = available_memory()
        result['capacityAfter'] = capacity(journal, owned_json(SEATS))
        if result['released']:
            receipt = backup / ('retired-window-closure-' + str(time.time_ns()) + '.json')
            # No seat/owner tokens or browser contents appear in receipts.
            with receipt.open('x') as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(result, handle, indent=2)
        return result
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=2, choices=range(1, 51))
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(reap(args.limit, pressure_only=False)))
    else:
        items = candidates(owned_json(SD / 'openbot-box-remint.json'), owned_json(SEATS), native_rows())
        print(json.dumps({'state': 'plan', 'candidates': [{k: x[k] for k in ('id', 'index')} for x in items]}))
