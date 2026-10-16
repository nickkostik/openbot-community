"""Recover temporal bots as unregistered local copies, preserving source state."""
import argparse
import base64
import contextlib
import copy
import fcntl
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time
import urllib.request
import uuid

SD = pathlib.Path('/home/box/sand-data')
PROC = pathlib.Path('/proc')
JOURNAL = SD / 'openbot-local-migration.json'
routing = None
H = None
UNSPECIFIED = object()


def read(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return default


def checked_bytes(path):
    path = pathlib.Path(path)
    if path.is_symlink():
        raise RuntimeError('Refusing a symlink: ' + str(path))
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def expect_bytes(path, expected):
    if checked_bytes(path) != expected:
        raise RuntimeError('Room recovery destination changed concurrently: ' + str(path))


def atomic_bytes(path, data, *, expected=UNSPECIFIED, pre_commit=None):
    """Check the destination again after fsync, immediately before replacing it."""
    path = pathlib.Path(path)
    if expected is UNSPECIFIED:
        expected = checked_bytes(path)
    expect_bytes(path, expected)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.migration.tmp', dir=path.parent)
    temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if previous is not None:
                os.fchmod(stream.fileno(), previous.st_mode & 0o777)
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (previous.st_uid, previous.st_gid):
                    os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if pre_commit is not None:
            pre_commit()
        expect_bytes(path, expected)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def write(path, data):
    atomic_bytes(path, json_bytes(data))


@contextlib.contextmanager
def migration_lock():
    """Coordinate this helper's commands; the native host does not take this lock."""
    SD.mkdir(parents=True, exist_ok=True)
    with (SD / '.openbot-local-migration.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another local migration command is running') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def require_quiescent_room_writer():
    """Do not mutate room files while the native host can write them independently."""
    if not PROC.is_dir():
        raise RuntimeError('Cannot inspect native room writers; room repair requires a quiescent host')
    active = []
    for process in PROC.iterdir():
        if not process.name.isdigit():
            continue
        try:
            argv = [value.decode('utf-8', errors='replace') for value in
                    (process / 'cmdline').read_bytes().split(b'\0') if value]
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # Other users' processes are not room writers for this box account.
            try:
                owned = process.stat().st_uid == os.getuid()
            except FileNotFoundError:
                continue
            if owned:
                raise RuntimeError('Cannot inspect a process owned by this account; refuse room writes') from None
            continue
        if not argv or pathlib.Path(argv[0]).name not in ('node', 'nodejs'):
            continue
        # A guard's --host-main value is configuration, not its executable script.
        # Be conservative about all other Node arguments, including preload flags.
        if any(not value.startswith('-') and pathlib.Path(value).name == 'host-main.cjs' and
               argv[index - 1] != '--host-main' for index, value in enumerate(argv) if index > 0):
            active.append(process.name)
    if active:
        raise RuntimeError('Native host is running (pid ' + ', '.join(active) +
                           '); room membership repair requires a quiescent host')


def migration_effects_finalized(journal):
    return journal.get('migrationEffectsFinalized') is True or journal.get('finalized') is True or (
        bool(journal.get('entries')) and all(entry.get('finalized') for entry in journal['entries']))


def rpc(method, body):
    global H, routing
    if routing is None:
        spec = importlib.util.spec_from_file_location('routing', SD / 'openbot-hooks/diagnose-routing.py')
        routing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(routing)
    if H is None:
        H = routing.headers()
    result = routing.rpc(method, body, H)
    if 'http' in result:
        raise RuntimeError(f'{method}: {result}')
    return result


def gateway(method, body):
    token = read(SD / 'gateway.json')['token']
    req = urllib.request.Request('http://127.0.0.1:1340/api/' + method,
        data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + token,
                                                'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def remap_text(text, mapping):
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def remap_json(data, mapping):
    return json.loads(remap_text(json.dumps(data, ensure_ascii=False), mapping))


def plan():
    if JOURNAL.exists():
        return read(JOURNAL)
    if not (SD / 'openbot-local-only.enable').exists():
        raise RuntimeError('Enable and verify persistent local mode first')
    rows = rpc('ListGrokBotAgents', {}).get('agents', [])
    backup = SD / 'local-migration-backups' / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True)
    write(backup / 'server-roster.json', rows)
    settings = rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
    write(backup / 'server-settings.json', settings)
    for name in ('openbot-bot-models.json', 'settings.json', 'openbot-pause-bots.json'):
        if (SD / name).exists():
            shutil.copy2(SD / name, backup / name)
    write(backup / 'automations.json', gateway('listAllAutomations', {}))
    entries = []
    for row in rows:
        old = row.get('legacyAgentId')
        if row.get('harness') != 'temporal':
            continue
        if row.get('name', '').startswith('OpenBot Local Routing Verification'):
            continue
        if row.get('name', '').startswith('OpenBot Atomic Local Verification'):
            continue
        if not (SD / 'agents' / old / 'profile.json').exists():
            raise RuntimeError('Local source missing: ' + old)
        if str(row.get('kind', '')).endswith('ROOM'):
            source_group = SD / 'agents' / old / 'group.json'
            if source_group.exists():
                # Save explicit membership before retired mirrors can erase it.
                write(backup / 'room-groups' / (old + '.json'), read(source_group))
        entries.append({'oldId': old, 'newId': str(uuid.uuid4()), 'rowId': row['id'],
                        'name': row.get('name', ''), 'kind': row.get('kind'), 'staged': False})
    journal = {'startedAt': time.strftime('%FT%TZ', time.gmtime()), 'backup': str(backup),
               'protectedProfiles': {row['agentId']: read(SD / 'agents' / row['agentId'] / 'profile.json')
                   for row in rows if row.get('harness') == 'box' and row.get('viewerIsOwner') is True
                   and row.get('agentId') == row.get('legacyAgentId')},
               'entries': entries, 'finalized': False}
    write(JOURNAL, journal)
    return journal


def fetch_transcript(old, backup):
    target = pathlib.Path(backup) / 'cloud-transcripts' / (old + '.json')
    if target.exists():
        return read(target)['entries']
    entries = {}
    before = None
    generation = None
    while True:
        body = {'agentId': old, 'limit': 500}
        if before is not None:
            body['beforeSeq'] = str(before)
        if generation is not None:
            body['generation'] = generation
        result = rpc('ListGrokBotTranscriptEntries', body)
        rows = result.get('entries', [])
        if generation is not None and result.get('generation',generation) != generation:
            raise RuntimeError('Transcript generation changed during preservation')
        generation = result.get('generation', generation)
        if not rows:
            break
        for entry in rows:
            if int(entry['seq']) in entries and entries[int(entry['seq'])] != entry:
                raise RuntimeError('Transcript payload changed during pagination')
            entries[int(entry['seq'])] = entry
        earliest = min(int(entry['seq']) for entry in rows)
        if before is not None and earliest >= before:
            raise RuntimeError('Transcript pagination did not advance')
        if earliest <= 1:
            break
        before = earliest
    rows = [entries[k] for k in sorted(entries)]
    write(target, {'generation': generation, 'entries': rows})
    return rows


def copy_agent(old, new, mapping, journal):
    spec = importlib.util.spec_from_file_location('fleet_state_merge',
        pathlib.Path(__file__).with_name('fleet-state-merge.py'))
    merger = importlib.util.module_from_spec(spec); spec.loader.exec_module(merger)
    src, dst = SD / 'agents' / old, SD / 'agents' / new
    temp = SD / ('local-copy-' + new)
    if temp.exists():
        raise RuntimeError('Incomplete copy already exists: ' + str(temp))
    if dst.exists():
        raise RuntimeError('Destination already exists without a staged journal: ' + str(dst))
    shutil.copytree(src, temp, symlinks=True,
        ignore=shutil.ignore_patterns('*.db', '*-wal', '*-shm', '*.migration.tmp'))
    for db in src.rglob('*.db'):
        relative = db.relative_to(src)
        target = temp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True)
        conn = sqlite3.connect(target)
        source_conn.backup(conn)
        source_conn.close()
        # Transform only TEXT columns; encrypted or binary blob data stays intact.
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            # These records are merged below with owner-only normalization.
            # SQL replace() on their serialized JSON would rewrite history.
            if table == 'transcript_entries':
                continue
            qt = '"' + table.replace('"', '""') + '"'
            columns = conn.execute('PRAGMA table_info(' + qt + ')').fetchall()
            for column in columns:
                if column[2].upper() != 'TEXT':
                    continue
                qc = '"' + column[1].replace('"', '""') + '"'
                for old_id, new_id in mapping.items():
                    conn.execute('UPDATE ' + qt + ' SET ' + qc + '=replace(' + qc + ',?,?) WHERE instr(' + qc + ',?)>0',
                                 (old_id, new_id, old_id))
                    # The kv store encodes structured metadata as hexadecimal JSON.
                    conn.execute('UPDATE ' + qt + ' SET ' + qc + '=replace(' + qc + ',?,?) WHERE instr(' + qc + ',?)>0',
                                 (old_id.encode().hex(), new_id.encode().hex(), old_id.encode().hex()))
        conn.commit()
        if relative.name == 'store.db':
            row = conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
            if row:
                metadata = json.loads(bytes.fromhex(row[0]))
                metadata['agentId'] = new
                conn.execute("UPDATE kv SET value=? WHERE key='metadata'",
                             (json.dumps(metadata, separators=(',', ':')).encode().hex(),))
                conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed for ' + str(target))
        conn.close()
    for file in temp.rglob('*'):
        if file.is_symlink() or not file.is_file() or file.suffix not in ('.json', '.jsonl', '.md', '.txt', '.yaml', '.yml'):
            continue
        with file.open('r', newline='') as handle:
            original = handle.read()
        updated = merger.remap_transcript_jsonl(original, mapping) if file.suffix == '.jsonl' \
            else remap_text(original, mapping)
        if updated != original:
            with file.open('w', newline='') as handle:
                handle.write(updated)
    profile = read(temp / 'profile.json')
    profile.pop('serverId', None)
    profile['harness'] = 'box'
    write(temp / 'profile.json', profile)
    settings = read(temp / 'settings.json', {})
    settings['hiddenFromSidebar'] = False
    write(temp / 'settings.json', settings)
    automation_states = {}
    for file in (temp / 'automations').rglob('automation.json'):
        automation = read(file)
        automation_states[str(file.relative_to(temp))] = automation.get('enabled', False)
        automation['enabled'] = False
        write(file, automation)
    remote = fetch_transcript(old, journal['backup'])
    conn = sqlite3.connect(temp / 'store.db')
    local = [json.loads(row[0]) for row in conn.execute('SELECT entry FROM transcript_entries ORDER BY seq')]
    cloud = [json.loads(base64.b64decode(row['body'], validate=True)) for row in remote]
    merged, collisions = merger.merge_entries({'local': local, 'platform': cloud}, mapping)
    ordered = [(record['entry']['id'], json.dumps(record['entry'], ensure_ascii=False, separators=(',', ':')))
               for record in merged]
    # Same-ID divergent payloads are preserved, but their opaque SDK roots and
    # references need an explicit review before this staged copy becomes live.
    write(pathlib.Path(journal['backup']) / 'history-merge' / (new + '.json'),
          {'entries': merged, 'idConflicts': collisions, 'liveApplied': False})
    conn.execute('DELETE FROM transcript_entries')
    conn.executemany('INSERT INTO transcript_entries(seq,id,entry) VALUES(?,?,?)',
                     [(i + 1, entry_id, entry) for i, (entry_id, entry) in enumerate(ordered)])
    conn.commit()
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise RuntimeError('Transcript store integrity check failed')
    conn.close()
    # A new id does not share the old cloud state, identity, or running turn.
    (temp / '.openbot-local-copy').write_text(old + '\n')
    temp.rename(dst)
    transcript_src = SD / 'agent-transcripts' / old
    transcript_dst = SD / 'agent-transcripts' / new
    if transcript_src.exists() and not transcript_dst.exists():
        shutil.copytree(transcript_src, transcript_dst)
        for file in list(transcript_dst.rglob('*')):
            if file.is_file() and file.suffix == '.jsonl':
                with file.open('r', newline='') as handle:
                    original = handle.read()
                updated = merger.remap_transcript_jsonl(original, mapping)
                if updated != original:
                    with file.open('w', newline='') as handle:
                        handle.write(updated)
                if old in file.name:
                    file.rename(file.with_name(file.name.replace(old, new)))
    return {'transcriptEntries': len(ordered), 'cloudEntries': len(remote),
            'historyIdConflicts': collisions, 'automationStates': automation_states}


def stage(only=None):
    journal = plan()
    mapping = {e['oldId']: e['newId'] for e in journal['entries']}
    for entry in journal['entries']:
        if entry['staged'] or (only and entry['oldId'] != only):
            continue
        stats = copy_agent(entry['oldId'], entry['newId'], mapping, journal)
        entry.update(stats)
        entry['staged'] = True
        write(JOURNAL, journal)
        print('staged', entry['name'], entry['newId'], 'entries', entry['transcriptEntries'], flush=True)


def canonical_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Member and migration IDs must be canonical UUIDs')
    return value


def room_recovery_plan(journal):
    """Use explicit membership IDs only; message authors and room names prove nothing."""
    updated = copy.deepcopy(journal)
    if migration_effects_finalized(journal):
        # Room debt must never make old settings/automation restoration runnable again.
        updated['migrationEffectsFinalized'] = True
    watched = {}

    def snapshot(path, default=None):
        path = pathlib.Path(path)
        if path.is_symlink():
            raise RuntimeError('Refusing room recovery through a symlink: ' + str(path))
        raw = path.read_bytes() if path.exists() else None
        watched[path] = raw
        return json.loads(raw) if raw is not None else default

    on_disk = snapshot(JOURNAL)
    if on_disk is not None and on_disk != journal:
        raise RuntimeError('Migration journal changed; reload before room recovery')
    mapping = {}
    destinations = set()
    for entry in journal['entries']:
        old, new = canonical_id(entry['oldId']), canonical_id(entry['newId'])
        if old == new or old in mapping or new in destinations:
            raise ValueError('Migration identity map is not one-to-one')
        mapping[old] = new
        destinations.add(new)
    if set(mapping) & destinations:
        raise ValueError('Migration identity map contains overlapping generations')
    room_ids = {entry['newId'] for entry in journal['entries'] if str(entry.get('kind', '')).endswith('ROOM')}
    backup = pathlib.Path(journal['backup'])
    roster = snapshot(backup / 'server-roster.json', [])
    if not isinstance(roster, list):
        raise ValueError('Saved server roster is not a list')

    def member_ids(values, room_id):
        if not isinstance(values, list):
            raise ValueError('Explicit member IDs must be a list')
        members = []
        for value in values:
            ident = mapping.get(canonical_id(value), value)
            if ident == room_id or ident in room_ids:
                raise ValueError('Room membership contains a room or itself')
            if ident in members:
                raise ValueError('Room membership contains duplicate identities')
            profile = snapshot(SD / 'agents' / ident / 'profile.json')
            if not isinstance(profile, dict) or profile.get('harness') != 'box':
                raise ValueError('Member has no known BOX profile: ' + ident)
            if any(profile.get(key) not in (None, '', ident) for key in ('id', 'agentId')):
                raise ValueError('Member profile identity differs from its directory: ' + ident)
            members.append(ident)
        return members

    changes, results = {}, []
    for entry in updated['entries']:
        if not str(entry.get('kind', '')).endswith('ROOM'):
            continue
        old, new = entry['oldId'], entry['newId']
        group_file = SD / 'agents' / new / 'group.json'
        try:
            group = snapshot(group_file)
            settings = snapshot(group_file.with_name('settings.json'), {})
            if not isinstance(settings, dict):
                raise ValueError('Room settings are not an object')
            if entry.get('retired') is True and settings.get('hiddenFromSidebar') is True and (
                    group is None or isinstance(group, dict) and group.get('memberIds') == []):
                result = {'agentId': new, 'status': 'retired-shell'}
            else:
                profile = snapshot(group_file.with_name('profile.json'))
                if not isinstance(profile, dict) or profile.get('harness') != 'box':
                    raise ValueError('Current room has no known BOX profile')
                if any(profile.get(key) not in (None, '', new) for key in ('id', 'agentId')):
                    raise ValueError('Room profile identity differs from its directory')
                original_rows = [row for row in roster if isinstance(row, dict) and
                                 old in (row.get('agentId'), row.get('legacyAgentId'))]
                if len(original_rows) != 1 or not str(original_rows[0].get('kind', '')).endswith('ROOM'):
                    raise ValueError('Saved original server kind does not confirm this room')
                if group is not None and (not isinstance(group, dict) or 'memberIds' not in group):
                    raise ValueError('Current group has no explicit memberIds list')
                current = member_ids(group['memberIds'], new) if group is not None else []
                if current:
                    # Preserve current membership even if older snapshots disagree.
                    result = {'agentId': new, 'status': 'preserved-current', 'memberIds': current}
                    if current != group['memberIds']:
                        changes[group_file] = {**group, 'memberIds': current}
                        result['status'] = 'remapped-current'
                else:
                    authorities = []
                    for source, path in (
                            ('original-group-backup', backup / 'room-groups' / (old + '.json')),
                            ('original-group', SD / 'agents' / old / 'group.json')):
                        original = snapshot(path)
                        if original is not None:
                            if not isinstance(original, dict) or 'memberIds' not in original:
                                raise ValueError('Original group has no explicit memberIds list')
                            members = member_ids(original['memberIds'], new)
                            if members:
                                authorities.append((source, members))
                    for row in original_rows:
                        if 'memberAgentIds' in row:
                            members = member_ids(row['memberAgentIds'], new)
                            if members:
                                authorities.append(('original-server-roster', members))
                    if not authorities:
                        raise ValueError('No nonempty authoritative membership IDs; transcript names are not authority')
                    source, members = authorities[0]
                    if any(set(other) != set(members) for _, other in authorities[1:]):
                        raise ValueError('Original membership sources disagree')
                    changes[group_file] = {**(group or {'version': 1}), 'memberIds': members}
                    result = {'agentId': new, 'status': 'restored-authority', 'source': source, 'memberIds': members}
                entry['roomMembers'] = result['memberIds']
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            result = {'agentId': new, 'status': 'unresolved', 'reason': str(error)}
        entry['roomRecovery'] = result
        results.append(result)
    updated['roomRecoveryComplete'] = not any(result['status'] == 'unresolved' for result in results)
    return {'journal': updated, 'changes': changes, 'watched': watched, 'rooms': results,
            'unresolved': [result for result in results if result['status'] == 'unresolved']}


def _restore_room_members(journal):
    recovery = room_recovery_plan(journal)
    updated = recovery['journal']
    replacements = {path: json_bytes(data) for path, data in recovery['changes'].items()}
    if updated != journal:
        replacements[JOURNAL] = json_bytes(updated)
    if recovery['changes']:
        require_quiescent_room_writer()
    # Check every authority/profile as well as destinations before the first write.
    for path, before in recovery['watched'].items():
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before:
            raise RuntimeError('Room recovery input changed concurrently: ' + str(path))
    applied = []
    try:
        for path, after in replacements.items():
            before = recovery['watched'].get(path)
            if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before:
                raise RuntimeError('Room recovery destination changed concurrently: ' + str(path))
            guard = require_quiescent_room_writer if path in recovery['changes'] else None
            atomic_bytes(path, after, expected=before, pre_commit=guard)
            applied.append((path, before, after))
    except Exception as failure:
        conflicts = []
        for path, before, after in reversed(applied):
            try:
                guard = require_quiescent_room_writer if path in recovery['changes'] else None
                if before is None:
                    if guard is not None:
                        guard()
                    expect_bytes(path, after)
                    path.unlink()
                else:
                    atomic_bytes(path, before, expected=after, pre_commit=guard)
            except Exception as conflict:
                # A conflict on one path must not prevent safe rollback of other paths.
                conflicts.append(str(path) + ': ' + str(conflict))
        if conflicts:
            raise RuntimeError('Room recovery failed: ' + str(failure) + '; rollback conflicts: ' +
                               '; '.join(conflicts)) from failure
        raise
    journal.clear()
    journal.update(updated)
    return recovery['rooms']


def restore_room_members(journal):
    with migration_lock():
        return _restore_room_members(journal)


def finalize():
    with migration_lock():
        return _finalize()


def _finalize():
    journal = plan()
    effects_completed = migration_effects_finalized(journal)
    if not all(entry['staged'] for entry in journal['entries']):
        raise RuntimeError('All copies must be staged and verified before finalizing')
    if not journal.get('deliveryVerified'):
        raise RuntimeError('Record successful local gateway delivery before finalizing')
    rooms = _restore_room_members(journal)
    unresolved = [room['agentId'] for room in rooms if room['status'] == 'unresolved']
    if unresolved:
        raise RuntimeError('Room membership unresolved; finalization stopped: ' + ', '.join(unresolved))
    if effects_completed:
        return
    mapping = {e['oldId']: e['newId'] for e in journal['entries']}
    model_file = SD / 'openbot-bot-models.json'
    models = read(model_file, {'assignments': {}})
    for old, new in mapping.items():
        if old in models.get('assignments', {}):
            models['assignments'][new] = models['assignments'][old]
    write(model_file, models)
    settings = rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
    sections = remap_json(settings.get('sidebarSections', {}).get('sections', []), mapping)
    sections = [{**section, 'agentIds': section.get('agentIds', [])} for section in sections]
    pins = remap_json(settings.get('pinnedAgents', {}).get('agentIds', []), mapping)
    gateway('setHostSettings', {'sidebarSections': sections, 'pinnedAgentIds': pins})
    rpc('UpdateGrokBotUserRuntimeSettings', {
        'pinnedAgents': {'agentIds': pins}, 'sidebarSections': {'sections': sections}})
    for entry in journal['entries']:
        if entry.get('finalized'):
            continue
        old, new = entry['oldId'], entry['newId']
        # Disable cloud routines before enabling their local copies.
        cloud = rpc('ListGrokBotAgentAutomations', {'agentId': old})
        write(pathlib.Path(journal['backup']) / 'cloud-automations' / (old + '.json'), cloud)
        for automation in cloud.get('automations', []):
            rpc('SetGrokBotAgentAutomationEnabled', {'agentId': old,
                'automationId': automation['automationId'], 'isEnabled': False})
        for file in (SD / 'agents' / old / 'automations').rglob('automation.json'):
            automation = read(file)
            automation['enabled'] = False
            write(file, automation)
        rpc('SetGrokBotAgentClientState', {'agentId': old, 'hiddenFromSidebar': True})
        old_settings = read(SD / 'agents' / old / 'settings.json', {})
        old_settings['hiddenFromSidebar'] = True
        write(SD / 'agents' / old / 'settings.json', old_settings)
        for relative, enabled in entry['automationStates'].items():
            file = SD / 'agents' / new / relative
            automation = read(file)
            automation['enabled'] = enabled
            write(file, automation)
        entry['finalized'] = True
        write(JOURNAL, journal)
        print('local active', entry['name'], new, flush=True)
    journal['finalized'] = True
    journal['migrationEffectsFinalized'] = True
    write(JOURNAL, journal)


def verify():
    journal = read(JOURNAL)
    if journal is None:
        raise RuntimeError('An existing migration journal is required; verification never creates one')
    bad = []
    profiles = [read(SD / 'agents' / entry['newId'] / 'profile.json') if entry.get('staged') else None
                for entry in journal['entries']]
    rows = None
    if any(isinstance(profile, dict) and profile.get('serverId') is not None for profile in profiles):
        try:
            result = rpc('ListGrokBotAgents', {})
            if not isinstance(result, dict) or 'http' in result or 'error' in result:
                raise ValueError('Roster lookup failed')
            rows = result.get('agents')
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('Malformed roster')
        except Exception:
            rows = None
            bad.append(('platform roster', 'BOX binding lookup failed'))
    for entry, profile in zip(journal['entries'], profiles):
        if not entry.get('staged'):
            bad.append((entry['name'], 'not staged'))
            continue
        if not isinstance(profile, dict) or profile.get('harness') != 'box':
            bad.append((entry['name'], 'non-local profile'))
        if isinstance(profile, dict):
            if any(key in profile and profile[key] != entry['newId'] for key in ('id', 'agentId', 'legacyAgentId')):
                bad.append((entry['name'], 'profile identity mismatch'))
            if profile.get('serverId') is not None and rows is not None:
                server_id = str(profile['serverId'])
                bound = [row for row in rows if row.get('id') == server_id]
                matching = [row for row in rows if entry['newId'] in (row.get('agentId'), row.get('legacyAgentId'))]
                if (not server_id.isascii() or not server_id.isdigit() or not server_id.lstrip('0') or
                        len(bound) != 1 or len(matching) != 1 or bound[0] != matching[0] or
                        bound[0].get('harness') != 'box' or bound[0].get('agentId') != entry['newId'] or
                        bound[0].get('legacyAgentId') != entry['newId']):
                    bad.append((entry['name'], 'BOX server binding mismatch'))
        try:
            with contextlib.closing(sqlite3.connect(
                    'file:' + str(SD / 'agents' / entry['newId'] / 'store.db') + '?mode=ro', uri=True)) as conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    bad.append((entry['name'], 'SQLite integrity'))
                metadata = conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
                if metadata and json.loads(bytes.fromhex(metadata[0])).get('agentId') != entry['newId']:
                    bad.append((entry['name'], 'metadata identity mismatch'))
        except (sqlite3.Error, ValueError, TypeError):
            bad.append((entry['name'], 'SQLite verification failed'))
    protected = journal.get('protectedProfiles', {})
    if 'workingProfile' in journal:
        # Legacy journals carry the saved profile, never a portable hardcoded ID.
        saved = journal['workingProfile']
        candidates = [p.parent.name for p in (SD / 'agents').glob('*/profile.json')
            if (saved.get('id') and p.parent.name == saved['id']) or
               (saved.get('serverId') and read(p).get('serverId') == saved['serverId'])]
        if len(set(candidates)) != 1:
            bad.append(('working bot', 'ambiguous saved identity'))
        else:
            protected = {**protected, candidates[0]: saved}
    for aid, profile in protected.items():
        if str(uuid.UUID(aid)) != aid or read(SD / 'agents' / aid / 'profile.json') != profile:
            bad.append(('working bot', 'profile changed'))
    print('local copies', len(journal['entries']), 'staged', sum(e['staged'] for e in journal['entries']),
          'finalized', sum(e.get('finalized', False) for e in journal['entries']), 'failures', bad)
    if bad:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('plan', 'stage', 'finalize', 'verify', 'rooms-plan', 'rooms-restore'))
    parser.add_argument('--only')
    args = parser.parse_args()
    if args.command == 'plan':
        journal = plan()
        print('targets', len(journal['entries']), 'backup', journal['backup'])
        for entry in journal['entries']:
            print(entry['name'], entry['oldId'], '->', entry['newId'])
    elif args.command == 'stage':
        stage(args.only)
    elif args.command == 'finalize':
        finalize()
    elif args.command in ('rooms-plan', 'rooms-restore'):
        journal = read(JOURNAL)
        if journal is None:
            raise RuntimeError('An existing migration journal is required; this command never creates one')
        rooms = room_recovery_plan(journal)['rooms'] if args.command == 'rooms-plan' else restore_room_members(journal)
        print(json.dumps(rooms, indent=2))
        if any(room['status'] == 'unresolved' for room in rooms):
            raise SystemExit(1)
    else:
        verify()
