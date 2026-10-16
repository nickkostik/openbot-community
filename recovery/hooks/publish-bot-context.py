#!/usr/bin/env python3
"""Publish verified peer identities in the native model-readable workflow tree.

The host's private-store guard remains intact. This exports only bot identities,
not profiles, databases, credentials, configuration, or transcript contents.
Background publication never sends messages or changes a bot's instructions.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import tempfile
import time
import urllib.request
import uuid

SD = pathlib.Path('/home/box/sand-data')
PUBLIC_RELATIVE = pathlib.Path('workflows/openbot-fleet/roster.json')
PUBLIC_PATH = '/home/box/agent-data/workflows/openbot-fleet/roster.json'
OLD_PATHS = ('/home/box/agent-data/openbot-recovery/roster.json',
             '/home/box/sand-data/openbot-recovery/roster.json')
START = '[OpenBot shared context]'
END = '[/OpenBot shared context]'
NOTICE = (f'{START}\nRead the current colleague and room identities from {PUBLIC_PATH}. '
          'This is the published bot-readable roster. Use current IDs and explicit room members; '
          'old IDs in recovered conversations are historical. Routine definitions are under '
          '/home/box/agent-data/agents/<your current bot ID>/automations/. '
          'Use normal bot tools for conversations and routines, rather than opening private SQLite stores.\n'
          f'{END}')
MAX_JSON_BYTES = 2 * 1024 * 1024


def canonical(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise RuntimeError('Expected a canonical bot UUID')
    return value


def owned_bytes(path):
    path = pathlib.Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Context input/output must be an owned regular file')
    if info.st_size > MAX_JSON_BYTES:
        raise RuntimeError('Context file exceeds its size limit')
    return path.read_bytes()


def owned_json(path):
    return json.loads(owned_bytes(path))


def native(method, body=None):
    token = owned_json(SD / 'gateway.json')['token']
    request = urllib.request.Request('http://127.0.0.1:1340/api/' + method,
        data=json.dumps(body or {}).encode(), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def roster_projection(records, entries, native_rows):
    """Preserve existing peers and add active copies only after native validation."""
    if not isinstance(records, list) or not isinstance(entries, list) or not isinstance(native_rows, list):
        raise RuntimeError('Unknown roster schema')
    native_by_id = {}
    for row in native_rows:
        aid = row.get('id') if isinstance(row, dict) else None
        if aid is None:
            raise RuntimeError('Native roster entry lacks an ID')
        canonical(aid)
        if aid in native_by_id:
            raise RuntimeError('Ambiguous native bot identity')
        native_by_id[aid] = row
    desired = []
    for row in records:
        if not isinstance(row, dict):
            raise RuntimeError('Unknown colleague record')
        desired.append(canonical(row.get('id')))
    if len(set(desired)) != len(desired):
        raise RuntimeError('Colleague roster contains duplicate identities')
    active = set()
    for entry in entries:
        if entry.get('phase') != 'active':
            continue
        aid = canonical(entry.get('newId'))
        if aid in active:
            raise RuntimeError('Active journal contains duplicate identities')
        active.add(aid)
        if aid not in desired:
            desired.append(aid)
    result, omitted = [], []
    for aid in desired:
        row = native_by_id.get(aid)
        usable = row is not None and row.get('harness') == 'box' and row.get('isHiddenFromSidebar') is not True
        if not usable:
            if aid in active:
                raise RuntimeError('An active copy is missing, hidden, or not BOX')
            omitted.append(aid)
            continue
        name = row.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise RuntimeError('Native bot name is missing or invalid')
        group = row.get('isGroup') is True
        members = row.get('memberIds', []) if group else []
        if not isinstance(members, list) or len(set(members)) != len(members) or group and not members:
            raise RuntimeError('Room requires explicit unique members')
        for member in members:
            member = canonical(member)
            peer = native_by_id.get(member)
            if peer is None or peer.get('harness') != 'box' or peer.get('isHiddenFromSidebar') is True:
                raise RuntimeError('Room member is missing, hidden, or not BOX')
        result.append({'id': aid, 'name': name, 'harness': 'box',
                       'isGroup': group, 'memberIds': members})
    return result, omitted


def ensure_owned_directory(path):
    path = pathlib.Path(path)
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o755)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Context directory must be owned and not symlinked')


def atomic_owned_file(path, value, expected):
    path = pathlib.Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.bot-context-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        current = owned_bytes(path) if path.exists() or path.is_symlink() else None
        if current != expected:
            raise RuntimeError('Context changed concurrently; no replacement applied')
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


def plan():
    journal = SD / 'openbot-box-remint.json'
    source = SD / 'openbot-recovery/roster.json'
    if not journal.exists() or not source.exists():
        return {'status': 'not-configured'}, [], []
    for directory in (SD, source.parent, SD / 'agents'):
        ensure_existing = directory.lstat()
        if not stat.S_ISDIR(ensure_existing.st_mode) or ensure_existing.st_uid != os.getuid():
            raise RuntimeError('Context source directory must be owned and not symlinked')
    entries = owned_json(journal)['entries']
    rows = native('listAgents')
    if isinstance(rows, dict):
        rows = rows.get('agents')
    projection, omitted = roster_projection(owned_json(source), entries, rows)
    targets = [e['newId'] for e in entries if e.get('phase') == 'active']
    return {'status': 'planned', 'entries': len(projection), 'omitted': omitted,
            'instructionTargets': len(targets), 'path': PUBLIC_PATH}, projection, targets


def publish():
    result, projection, _ = plan()
    if result['status'] == 'not-configured':
        return result
    ensure_owned_directory(SD / 'workflows')
    ensure_owned_directory(SD / PUBLIC_RELATIVE.parent)
    destination = SD / PUBLIC_RELATIVE
    before = owned_bytes(destination) if destination.exists() or destination.is_symlink() else None
    content = (json.dumps(projection, ensure_ascii=False, indent=2) + '\n').encode()
    if before != content:
        atomic_owned_file(destination, content, before)
    return {**result, 'status': 'published' if before != content else 'unchanged',
            'sha256': hashlib.sha256(content).hexdigest()}


def with_notice(description):
    if not isinstance(description, str):
        raise RuntimeError('Unknown bot description schema')
    for old in OLD_PATHS:
        description = description.replace(old, PUBLIC_PATH)
    description = re.sub(re.escape(START) + r'[\s\S]*?' + re.escape(END), '', description).rstrip()
    return (description + '\n\n' if description else '') + NOTICE


def update_instructions():
    """Explicit one-time update; never called by background publication."""
    result = publish()
    if result['status'] == 'not-configured':
        return result
    _, _, targets = plan()
    ensure_owned_directory(SD / 'recovery-snapshots')
    backup = SD / 'recovery-snapshots' / f'bot-context-{time.time_ns()}'
    backup.mkdir(mode=0o700)
    changed = []
    for aid in targets:
        directory = SD / 'agents' / canonical(aid)
        ensure = directory.lstat()
        if not stat.S_ISDIR(ensure.st_mode) or ensure.st_uid != os.getuid():
            raise RuntimeError('Agent directory must be owned and not symlinked')
        path = directory / 'profile.json'
        before = owned_bytes(path)
        profile = json.loads(before)
        if profile.get('harness') != 'box':
            raise RuntimeError('Refusing to update a non-BOX profile')
        new_description = with_notice(profile.get('description', ''))
        if new_description == profile.get('description', ''):
            continue
        saved = backup / (aid + '.json')
        saved.write_bytes(before)
        saved.chmod(0o600)
        # The native profile RPC requires a name as well as a description.
        # Keep its existing visual fields; only the description changes.
        # Identity, visibility and provider/reasoning/routine fields are not
        # part of this metadata request.
        if owned_bytes(path) != before:
            raise RuntimeError('Bot profile changed concurrently')
        request_profile = {key: profile[key] for key in ('name', 'title', 'avatarShape', 'avatarColor')
                           if key in profile}
        request_profile['description'] = new_description
        native('updateAgent', {'id': aid, 'profile': request_profile})
        after = owned_json(path)
        expected = {**profile, 'description': new_description}
        if after != expected:
            raise RuntimeError('Native description update changed unrelated profile fields')
        changed.append(aid)
    return {**result, 'updatedInstructions': changed, 'privateBackup': str(backup)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'publish', 'update-instructions'))
    args = parser.parse_args()
    action = {'plan': lambda: plan()[0], 'publish': publish,
              'update-instructions': update_instructions}[args.command]
    print(json.dumps(action(), ensure_ascii=False))
