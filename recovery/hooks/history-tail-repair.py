"""One bounded, native-codec late-tail append before hidden BOX cutover.

The inherited dispatch flock is mandatory. Immutable source captures and the
prior receipt remain untouched. Two SQLite transactions are journaled so a
crash can resume the exact reviewed plan, never rebuild it over partial writes.
"""
import base64
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
MAX_ROWS = 128
MAX_TEXT = 4 * 1024 * 1024
REVIEWED_HOST_SHA = '74278c1ec791b38a235bf68e386dbbe86428bd2f9966baceb29a1173bc574dc3'
FRESHNESS_ERROR = 'Source history changed after reviewed merge; incremental preservation required before activation'


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def reference(path):
    return {'path': str(path), 'sha256': sha(path.read_bytes())}


def owned(path, limit=128 * 1024 * 1024):
    path = pathlib.Path(path)
    for ancestor in [*reversed(path.parents), path]:
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode): raise RuntimeError('Tail evidence path is symlinked')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
        raise RuntimeError('Tail evidence must be a bounded owned regular file')
    return path.read_bytes()


def write(path, value, immutable=False):
    raw = encoded(value)
    if immutable and path.exists():
        if owned(path) != raw: raise RuntimeError('Immutable tail evidence already differs')
        return
    module('migrate-local-bots').atomic_bytes(path, raw)
    path.chmod(0o600)


def db(path, immutable=False):
    return contextlib.closing(sqlite3.connect('file:' + str(path) + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True))


def rows(path):
    with db(path) as connection:
        result = [list(row) for row in connection.execute('SELECT seq,id,entry FROM transcript_entries ORDER BY seq LIMIT 200001')]
        if len(result) > 200000: raise RuntimeError('Tail transcript exceeds bounded evidence')
        return result


def kv(path):
    with db(path) as connection:
        return [list(row) for row in connection.execute('SELECT key,value FROM kv ORDER BY key')]


def graph(path):
    with db(path) as connection:
        return {row[0]: sha(row[1]) for row in connection.execute('SELECT id,data FROM blobs')}


def database_identity(path):
    # A hidden native store must keep its inode; never follow a swapped link.
    for parent in path.parents:
        if parent.is_symlink(): raise RuntimeError('Live tail database parent is symlinked')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(fd)
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1 or
                value.st_size > 128 * 1024 * 1024):
            raise RuntimeError('Invalid live tail database')
        return [value.st_dev, value.st_ino]
    finally:
        os.close(fd)


def snapshot(source, destination):
    owned(source)
    with db(source) as src, contextlib.closing(sqlite3.connect(destination)) as dst:
        src.backup(dst); dst.execute('PRAGMA journal_mode=DELETE'); dst.commit()
    destination.chmod(0o600)


def cloud_tail(rpc, source, capture):
    """Fetch at most 128 newer rows and prove the captured append boundary."""
    old = capture['entries']; expected = int(old[-1]['seq']) if old else 0
    previous = None; found = {}; generation = source['generation']
    while True:
        request = {'agentId': source['agentId'], 'limit': MAX_ROWS + 1}
        if previous is not None: request['beforeSeq'] = str(previous)
        reply = rpc('ListGrokBotTranscriptEntries', request)
        if reply.get('generation') != generation: raise RuntimeError('Tail source generation changed')
        page = reply.get('entries', [])
        if not isinstance(page, list): raise RuntimeError('Unknown cloud tail response')
        for item in page:
            seq = int(item['seq'])
            if seq <= 0 or seq in found and found[seq] != item: raise RuntimeError('Tail source fork')
            found[seq] = item
        if not page or min(int(item['seq']) for item in page) <= expected: break
        if len([seq for seq in found if seq > expected]) > MAX_ROWS: raise RuntimeError('Tail exceeds bounded import')
        lowest = min(int(item['seq']) for item in page)
        if previous is not None and lowest >= previous: raise RuntimeError('Cloud tail paging stalled')
        previous = lowest
    if expected and (expected not in found or found[expected] != old[-1]):
        raise RuntimeError('Tail source immutable boundary changed')
    added = [found[seq] for seq in sorted(found) if seq > expected]
    if len(added) > MAX_ROWS or [int(item['seq']) for item in added] != list(range(expected + 1, expected + len(added) + 1)):
        raise RuntimeError('Cloud tail is not a bounded contiguous append')
    return {'generation': generation, 'entries': old + added}, added


def literal(event):
    forbidden = {'toolCalls','tool_calls','toolCall','toolResults','tool_results','attachment','attachments','pendingToolCalls','execute','command'}
    if not isinstance(event,dict) or forbidden.intersection(event):
        raise RuntimeError('Tail executable or nontext envelope requires explicit review')
    if isinstance(event.get('message'),dict) and forbidden.intersection(event['message']):
        raise RuntimeError('Tail executable or nontext envelope requires explicit review')
    if event.get('kind') == 'message' and event.get('role') in ('user', 'assistant') and isinstance(event.get('content'), str):
        return event['role'], event['content']
    if event.get('kind') == 'send-message' and event.get('message', {}).get('type') == 'text' and isinstance(event['message'].get('content'), str):
        return 'assistant', event['message']['content']
    raise RuntimeError('Tail format needs explicit preservation review')


def append_rows(r, mapping, store, additions):
    projections = []; records = []; text_bytes = 0
    with contextlib.closing(sqlite3.connect(store)) as connection:
        occupied = {row[0] for row in connection.execute('SELECT id FROM transcript_entries')}
        sequence = connection.execute('SELECT COALESCE(MAX(seq),0) FROM transcript_entries').fetchone()[0]
        for source, item in additions:
            event = json.loads(base64.b64decode(item['body'], validate=True)); role, text = literal(event)
            value = r.merger.remap_transcript_owner(event, mapping); original = value['id']
            if not isinstance(original, str) or not original: raise RuntimeError('Tail identifier missing')
            identifier = original
            if identifier in occupied or len(identifier.encode()) > 128:
                identifier = r.merger.compact_branch_identifier(original + ':' + sha(encoded(value)), occupied)
            value['id'] = identifier; occupied.add(identifier); sequence += 1
            connection.execute('INSERT INTO transcript_entries(seq,id,entry)VALUES(?,?,?)', (sequence, identifier, encoded(value).decode()))
            projections.append({'sourceId': source['agentId'], 'sourceKind': 'platform', 'sourceEntryId': event['id'], 'targetEntryId': identifier})
            records.append({'id': identifier, 'originalId': event['id'], 'role': role, 'text': text,
                            'kind': event['kind'], 'timestampMs': event['timestampMs'], 'sourcePayloadSha256': sha(encoded(event)), 'literalText': True})
            text_bytes += len(text.encode())
        if len(records) > MAX_ROWS or text_bytes > MAX_TEXT: raise RuntimeError('Tail exceeds bounded import')
        connection.commit()
    return projections, records


def pending(journal, job_id):
    uuid.UUID(job_id)
    path = pathlib.Path(journal['backup']) / ('bounded-tail-job-' + job_id + '.json')
    return path.exists() and json.loads(owned(path)).get('phase') == 'prepared'


class TailRepair:
    def __init__(self, recovery, worker_lock_fd, codec=None, fault=None):
        self.recovery, self.r, self.root = recovery, recovery.r, recovery.root
        self.lock_fd = worker_lock_fd; self.codec = codec or self.run_codec
        self.fault = fault or (lambda phase: None)
        self.validator = module('history-merge-receipt')

    def require(self):
        self.recovery.require()
        if self.lock_fd is None: raise RuntimeError('Inherited dispatch writer lock required for tail repair')
        held = os.fstat(self.lock_fd); expected = (self.root / 'openbot-custom-dispatch.lock').lstat()
        if not stat.S_ISREG(held.st_mode) or (held.st_dev, held.st_ino, held.st_uid) != (expected.st_dev, expected.st_ino, os.getuid()):
            raise RuntimeError('Invalid inherited tail repair lock')
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def identity(self, journal, entry):
        self.require()
        if entry['phase'] != 'registered-box': raise RuntimeError('Tail repair cannot overwrite an active copy')
        directory = self.root / 'agents' / entry['newId']
        settings = json.loads(owned(directory / 'settings.json')); profile = json.loads(owned(directory / 'profile.json'))
        if settings.get('hiddenFromSidebar') is not True or profile.get('harness') != 'box':
            raise RuntimeError('Tail repair requires a hidden BOX copy')
        automations = list((directory / 'automations').rglob('automation.json'))
        if any(json.loads(owned(path)).get('enabled') is not False for path in automations):
            raise RuntimeError('Tail repair requires all copied routines paused')
        found = [row for row in self.r.rows() if row.get('agentId') == entry['newId']]
        if len(found) != 1: raise RuntimeError('Tail BOX binding is ambiguous')
        sid = self.r.publisher.require_box_row(found[0], entry['newId'])
        if sid != entry.get('serverId') or sid != profile.get('serverId'): raise RuntimeError('Tail BOX binding changed')
        return {str(path.relative_to(directory)): sha(owned(path)) for path in
                [directory / 'profile.json', directory / 'settings.json', *automations]}

    def local_sources(self, journal, entry, receipt):
        # Cloud heads are separately fetched and checked below. The existing
        # gate still verifies every local UI head and selected SDK root.
        known = {}
        for source in receipt['sources']:
            if source['kind'] != 'platform': continue
            cap = json.loads(self.validator.referenced(journal['backup'], {key: source[key] for key in ('path', 'sha256')}))
            if not isinstance(cap, dict): raise RuntimeError('Legacy cloud capture requires review')
            last = cap['entries'][-1] if cap['entries'] else None
            known[('platform', source['agentId'])] = {'generation': source['generation'], 'head': None if last is None else
                (int(last['seq']), sha(base64.b64decode(last['body'], validate=True)))}
        self.validator.source_freshness(self.r, journal, entry, known)

    def route(self, entry):
        plan = json.loads(owned(self.root / 'openbot-plan.json', 16 * 1024 * 1024))
        pins_path = self.root / 'openbot-bot-models.json'
        pins = json.loads(owned(pins_path)) if pins_path.exists() else {}
        identifier = pins.get('assignments', {}).get(entry['newId']) or pins.get('assignments', {}).get(entry.get('selectionSourceId',entry['oldId']))
        wildcard = plan.get('agents', {}).get('*', {})
        matches = [model for model in plan.get('catalog', {}).get('models', []) if
                   model.get('id') == identifier] if identifier else [model for model in plan.get('catalog', {}).get('models', []) if
                   model.get('providerId') == wildcard.get('providerId') and model.get('slug') == wildcard.get('modelId')]
        if len(matches) != 1: raise RuntimeError('Tail context route is ambiguous')
        result = {'modelId': matches[0]['id'], 'contextTokens': matches[0].get('contextTokens'), 'maxOutputTokens': matches[0].get('maxOutputTokens')}
        if any(type(result[key]) is not int or result[key] <= 0 for key in ('contextTokens', 'maxOutputTokens')) or result['maxOutputTokens'] >= result['contextTokens']:
            raise RuntimeError('Tail context capacity is unknown')
        return result

    def run_codec(self, folder, plan):
        engine = module('local-engine')
        if plan.get('engineSha256') != REVIEWED_HOST_SHA: raise RuntimeError('SDK receipt requires an unreviewed codec')
        # The execution engine can update independently. Decode this archived
        # SDK with its exact reviewed engine, never the newest arbitrary bundle.
        generations = self.root / 'openbot-local-engine' / 'generations'
        if generations.is_symlink(): raise RuntimeError('Codec generation directory is symlinked')
        names = sorted(generations.iterdir())
        if len(names) > 64: raise RuntimeError('Retained codec search exceeds its bound')
        host = None
        for candidate in names:
            if len(candidate.name) != 32 or any(c not in '0123456789abcdef' for c in candidate.name): continue
            path = engine.artifact_directory(self.root, {'generationId': candidate.name}) / 'stock.cjs'
            if sha(engine.owned_bytes(path, engine.MAX_BYTES)) == REVIEWED_HOST_SHA:
                host = path; break
        if host is None: raise RuntimeError('Reviewed retained SDK codec is unavailable')
        native = module('computer-recovery').HOST
        command = ['/exec-daemon/node', '--max-old-space-size=768', str(HERE / 'history-tail-codec.mjs'),
                   '--host', str(host), '--worker', str(native.parent / 'agent-isolation/agent-store-worker.cjs'),
                   '--allocator-host', str(native), '--candidate-dir', str(folder), '--plan-file', str(folder / 'plan.json')]
        result = subprocess.run(command, capture_output=True, timeout=90)
        write(folder / 'codec-private-result.json', {'returnCode': result.returncode, 'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace')}, True)
        if result.returncode: raise RuntimeError('Reviewed native tail codec refused the candidate')
        return json.loads(owned(folder / 'proof.json'))

    def prepare(self, journal, entry, parent):
        self.require(); self.validator.validate(self.r, journal, entry)
        receipt = json.loads(self.validator.referenced(journal['backup'], parent))
        self.local_sources(journal, entry, receipt)
        changes = []; captures = []
        for source in receipt['sources']:
            if source['kind'] != 'platform': continue
            old = json.loads(self.validator.referenced(journal['backup'], {key: source[key] for key in ('path', 'sha256')}))
            fresh, additions = cloud_tail(self.r.m.rpc, source, old)
            captures.append((source, fresh)); changes.extend((source, item) for item in additions)
        if not changes: return None
        guards = self.identity(journal, entry)
        folder = pathlib.Path(tempfile.mkdtemp(prefix='openbot-history-tail-', dir=journal['backup'])); folder.chmod(0o700)
        write(folder / '.openbot-detached-history-tail.json', {'schemaVersion': 1, 'detached': True}, True)
        target = self.root / 'agents' / entry['newId']
        for filename in ('store.db', 'conversation-blobs.db'):
            snapshot(target / filename, folder / ('before-' + filename)); snapshot(target / filename, folder / filename)
        if [json.loads(row[2]) for row in rows(folder / 'store.db')] != self.validator.db_entries(receipt['baseline']['store']['path']):
            raise RuntimeError('Hidden copy acquired new messages; no active-copy import')
        sdk = json.loads(self.validator.referenced(journal['backup'], receipt['sdkProof']))
        if graph(folder / 'conversation-blobs.db') != sdk['blobs']: raise RuntimeError('Hidden SDK changed after receipt')
        if sdk['rootSha256'] is None: raise RuntimeError('Tail import requires an existing reviewed typed SDK root')
        projections, records = append_rows(self.r, module('remint-box-fleet').identity_mapping(journal), folder / 'store.db', changes)
        selected = self.route(entry)
        plan = {'schemaVersion': 1, 'engineSha256': sdk['engineSha256'], 'records': records, 'sourceSnapshots': [source['sha256'] for source, _ in captures],
                'provenance': projections, 'selectedRoute': selected, 'expectedRootSHA': sdk['rootSha256'], 'expectedStoreSHA': sha(owned(folder / 'store.db'))}
        write(folder / 'plan.json', plan, True); self.require(); native = self.codec(folder, plan)
        actual_graph = graph(folder / 'conversation-blobs.db')
        context = native.get('context', {})
        if (native.get('schema') != 'openbot-history-tail-codec/v1' or native.get('providerCalls') != 0 or
                native.get('historicalActionsReplayed') is not False or native.get('networkAttempts') != 0 or native.get('toolAttempts') != 0 or
                native.get('engineSha256') != sdk['engineSha256'] or native.get('oldRootSha256') != sdk['rootSha256'] or
                native.get('workerSHA') != self.validator.IDENTIFIER_WORKER_SHA or native.get('rootSlot') != sdk['rootSlot'] or
                native.get('blobs') != actual_graph or native.get('rootSha256') != actual_graph.get(sdk['rootSlot']) or
                native.get('bridgeSha256') != sha(owned(HERE / 'history-tail-codec.mjs')) or native.get('sourcePlanSHA') != sha(encoded(plan)) or
                native.get('capacity', {}).get('fits') is not True or
                any(native.get('files', {}).get(name, {}).get('sha256') != sha(owned(folder / name)) for name in ('store.db','conversation-blobs.db')) or
                any(context.get(key) is not True for key in ('roundtrip','literalEquality','originalPrefixPreserved','otherRootFieldsPreserved'))):
            raise RuntimeError('Invalid detached native tail proof')
        updated = copy.deepcopy(receipt); updated.update(schema=self.validator.DELTA_SCHEMA, previousReceipt=parent,
            baseline={'store': reference(folder / 'store.db'), 'blobs': reference(folder / 'conversation-blobs.db')})
        for source, fresh in captures:
            path = folder / ('cloud-' + source['agentId'] + '.json'); write(path, fresh, True)
            for item in updated['sources']:
                if (item['agentId'], item['kind']) == (source['agentId'], 'platform'): item.update(reference(path))
            for item in updated['sourceOrders']:
                if (item['sourceId'], item['sourceKind']) == (source['agentId'], 'platform'):
                    item['entryIds'] = [json.loads(base64.b64decode(row['body'], validate=True))['id'] for row in fresh['entries']]
        updated['projections'] += projections
        sdk.update(rootSha256=native['rootSha256'], blobs=graph(folder / 'conversation-blobs.db'), nativeReport=reference(folder / 'proof.json'))
        write(folder / 'sdk-proof.json', sdk, True); updated['sdkProof'] = reference(folder / 'sdk-proof.json')
        write(folder / 'receipt.json', updated, True)
        # Canonical validation against an independent mirror, never the live UI.
        mirror = folder / 'validation'; (mirror / 'agents' / entry['newId']).mkdir(parents=True, mode=0o700)
        snapshot(folder / 'store.db', mirror / 'agents' / entry['newId'] / 'store.db')
        saved = self.r.m.SD; self.r.m.SD = mirror
        try: self.validator.validate(self.r, journal, entry, reference(folder / 'receipt.json'))
        finally: self.r.m.SD = saved
        intent = {'schemaVersion': 1, 'newId': entry['newId'], 'oldReceipt': parent, 'newReceipt': reference(folder / 'receipt.json'),
                  'guards': guards, 'databaseInodes': {name: database_identity(target / name) for name in ('store.db','conversation-blobs.db')}, 'serverId': entry['serverId'], 'sourceHeads': [source for source in updated['sources']],
                  'beforeStore': reference(folder / 'before-store.db'), 'beforeSDK': reference(folder / 'before-conversation-blobs.db'),
                  'plannedStore': reference(folder / 'store.db'), 'plannedSDK': reference(folder / 'conversation-blobs.db'), 'selectedRoute': selected,
                  'addedMessages': len(records), 'textBytes': sum(len(record['text'].encode()) for record in records)}
        write(folder / 'intent.json', intent, True)
        return reference(folder / 'intent.json')

    def fresh_cloud(self, sources):
        for source in sources:
            if source['kind'] != 'platform': continue
            raw = owned(source['path']); captured = json.loads(raw)
            if sha(raw) != source['sha256']: raise RuntimeError('Prepared cloud evidence changed')
            latest = self.r.m.rpc('ListGrokBotTranscriptEntries', {'agentId': source['agentId'], 'limit': 1})
            expected = captured['entries'][-1:] or []
            if latest.get('generation') != source['generation'] or latest.get('entries', []) != expected:
                raise RuntimeError('Source history advanced again; bounded attempt requires review')

    def apply(self, journal, intent_ref):
        self.require(); intent = json.loads(self.validator.referenced(journal['backup'], intent_ref))
        folder = pathlib.Path(intent_ref['path']).parent; marker = folder / 'apply-progress.json'
        for field in ('beforeStore', 'beforeSDK', 'plannedStore', 'plannedSDK', 'newReceipt'):
            self.validator.referenced(journal['backup'], intent[field])
        journal = self.r.read(); entry = self.r.entry(journal, intent['newId'])
        if entry.get('historyMergeReceipt') == intent['newReceipt']:
            self.validator.validate(self.r, journal, entry); return {'added': len(rows(intent['plannedStore']['path'])) - len(rows(intent['beforeStore']['path'])), 'resumed': True}
        if entry.get('historyMergeReceipt') != intent['oldReceipt']: raise RuntimeError('Tail accepted parent changed concurrently')
        if self.identity(journal, entry) != intent['guards'] or self.route(entry) != intent['selectedRoute']:
            raise RuntimeError('Tail target settings or selected route changed')
        receipt = json.loads(self.validator.referenced(journal['backup'], intent['oldReceipt']))
        self.local_sources(journal, entry, receipt); self.fresh_cloud(intent['sourceHeads']); self.require()
        # Legitimate idempotent hide ACK, never claimed as server readback.
        self.r.m.rpc('SetGrokBotAgentClientState', {'agentId': entry['newId'], 'hiddenFromSidebar': True})
        write(marker, {'phase': 'prepared', 'intent': intent_ref, 'serverHiddenAcknowledgedAtMs': int(time.time() * 1000)})
        self.fault('intent-durable')
        target = self.root / 'agents' / entry['newId']
        if {name: database_identity(target / name) for name in ('store.db','conversation-blobs.db')} != intent['databaseInodes']:
            raise RuntimeError('Live tail database inode changed')
        before_rows, planned_rows = rows(intent['beforeStore']['path']), rows(intent['plannedStore']['path'])
        before_kv, planned_kv = kv(intent['beforeStore']['path']), kv(intent['plannedStore']['path'])
        before_graph, planned_graph = graph(intent['beforeSDK']['path']), graph(intent['plannedSDK']['path'])
        with self.r.m.migration_lock():
            if graph(target / 'conversation-blobs.db') not in (before_graph, planned_graph): raise RuntimeError('Tail SDK CAS failed')
            with contextlib.closing(sqlite3.connect(target / 'store.db')) as sql:
                sql.execute('BEGIN IMMEDIATE'); self.require()
                actual_rows = [list(row) for row in sql.execute('SELECT seq,id,entry FROM transcript_entries ORDER BY seq')]
                actual_kv = [list(row) for row in sql.execute('SELECT key,value FROM kv ORDER BY key')]
                if (actual_rows, actual_kv) not in ((before_rows, before_kv), (planned_rows, planned_kv)):
                    raise RuntimeError('Tail UI CAS failed')
                if actual_rows == before_rows:
                    if planned_rows[:len(before_rows)] != before_rows: raise RuntimeError('Prepared tail rewrites published prefix')
                    sql.executemany('INSERT INTO transcript_entries(seq,id,entry)VALUES(?,?,?)', planned_rows[len(before_rows):])
                    changes = [(value, key) for key, value in planned_kv if dict(before_kv).get(key) != value]
                    if any(key != 'unreadState' for _, key in changes): raise RuntimeError('Tail changes unrelated UI state')
                    sql.executemany('UPDATE kv SET value=? WHERE key=?', changes)
                self.require(); sql.commit()
            write(marker, {'phase': 'ui-appended', 'intent': intent_ref}); self.fault('ui-appended')
            with contextlib.closing(sqlite3.connect(target / 'conversation-blobs.db')) as sdk, db(intent['plannedSDK']['path']) as planned:
                sdk.execute('BEGIN IMMEDIATE'); self.require()
                current = {row[0]: sha(row[1]) for row in sdk.execute('SELECT id,data FROM blobs')}
                if current not in (before_graph, planned_graph): raise RuntimeError('Tail SDK CAS failed')
                if current == before_graph:
                    root_slot = json.loads(self.validator.referenced(journal['backup'], json.loads(owned(intent['newReceipt']['path']))['sdkProof']))['rootSlot']
                    for identifier, data in planned.execute('SELECT id,data FROM blobs'):
                        if identifier not in current: sdk.execute('INSERT INTO blobs(id,data)VALUES(?,?)', (identifier, data))
                        elif current[identifier] != sha(data):
                            if identifier != root_slot: raise RuntimeError('Tail changes immutable SDK blob')
                            sdk.execute('UPDATE blobs SET data=?WHERE id=?', (data, identifier))
                self.require(); sdk.commit()
            write(marker, {'phase': 'sdk-appended', 'intent': intent_ref}); self.fault('sdk-appended')
        self.fresh_cloud(intent['sourceHeads']); self.require()
        self.r.accept_history_merge(entry['newId'], intent['newReceipt']['path'])
        write(marker, {'phase': 'accepted', 'intent': intent_ref}); self.fault('accepted')
        return {'added': len(planned_rows) - len(before_rows), 'resumed': False}

    def run(self):
        self.require(); journal = self.r.read()
        job = self.recovery.progress['jobId']; uuid.UUID(job)
        path = pathlib.Path(journal['backup']) / ('bounded-tail-job-' + job + '.json')
        if path.exists():
            state = json.loads(owned(path))
            if state['phase'] == 'preparing': raise RuntimeError('Interrupted tail preparation requires review')
            if state['phase'] == 'completed': raise RuntimeError('One bounded tail attempt already completed for this job')
            if state['phase'] != 'prepared': raise RuntimeError('Bounded tail attempt requires review')
        else:
            write(path, {'phase': 'preparing', 'jobId': job}, True)
            intents = []
            try:
                for entry in journal['entries']:
                    self.require()
                    if not entry.get('historyMergeReceipt'): continue
                    result = self.prepare(journal, entry, entry['historyMergeReceipt'])
                    if result:
                        intents.append(result)
                        values = [json.loads(self.validator.referenced(journal['backup'], item)) for item in intents]
                        if sum(item['addedMessages'] for item in values) > MAX_ROWS or sum(item['textBytes'] for item in values) > MAX_TEXT:
                            raise RuntimeError('Combined tail import exceeds one bounded attempt')
                if not intents: raise RuntimeError('No bounded cloud tail was found')
                state = {'phase': 'prepared', 'jobId': job, 'intents': intents}
                write(path, state)
            except BaseException:
                write(path, {'phase': 'review-needed', 'jobId': job}); raise
        reports = [self.apply(journal, intent) for intent in state['intents']]
        write(path, {**state, 'phase': 'completed', 'reports': reports})
        return {'phase': 'waiting-for-publication', 'repairedAgents': len(reports), 'importedMessages': sum(row['added'] for row in reports),
                'historicalActionsReplayed': False, 'nativeAppDeliveryVerified': False}
