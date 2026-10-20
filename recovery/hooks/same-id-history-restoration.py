#!/usr/bin/env python3
"""Pure validation for a same-owner union, including already-published identities.

This module reads immutable evidence only. It neither accepts a receipt nor owns
live dispatch, database replacement, publication acknowledgements or registration.
Those remain the canonical controller's guarded responsibilities.
"""
import base64
import contextlib
import importlib.util
import json
import math
import os
import stat
import struct
import pathlib
import re
import sqlite3

SCHEMA = 'openbot-history-merge-receipt/live-restoration-v1'
PROOF_SCHEMA = 'openbot-same-id-history-restoration/v1'
_UUID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
ROOT_SLOT = b'sand-live-conversation-root-v1__'.hex()
REVIEWED_ENGINE = '74278c1ec791b38a235bf68e386dbbe86428bd2f9966baceb29a1173bc574dc3'
REVIEWED_WORKER = 'e7d54d24d6ce6e82fb0c1b76d7572dbbb51b9437f958f04e6057b59e673a80fb'
REVIEWED_ALLOCATOR = '4c1ff1a360b58c6010695fb531638efe147a493ea2da9c16f35fced1f8a2ccaa'
REVIEWED_ALLOCATOR_HOST = '1908e381a6a6fc01d6d872694537775134cb5edb7a4eb2e6cebe9eb4ce286f6e'


def _helper():
    spec = importlib.util.spec_from_file_location('_same_id_receipt_primitives', pathlib.Path(__file__).with_name('history-merge-receipt.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _capture(h, root, ref, owner=None):
    value = json.loads(h.referenced(root, ref))
    _require(isinstance(value, dict) and value.get('readOnly') is True and
             isinstance(value.get('capturedAtMs'), (int, float)) and not isinstance(value['capturedAtMs'], bool) and
             math.isfinite(value['capturedAtMs']) and value['capturedAtMs'] >= 0 and isinstance(value.get('agentId'), str) and
             isinstance(value.get('entries'), list) and len(value['entries']) <= h.MAX_RECORDS,
             'Restoration requires a complete immutable API capture')
    _require(owner is None or value['agentId'] == owner, 'Restoration capture belongs to another owner')
    generation = value.get('generation')
    _require(type(generation) is int and generation >= 0 or isinstance(generation, str) and
             re.fullmatch(r'0|[1-9][0-9]*', generation) is not None, 'Invalid restoration capture generation')
    rows = []
    for index, item in enumerate(value['entries'], 1):
        seq = item.get('seq') if isinstance(item, dict) else None
        _require(type(seq) is int and seq == index or isinstance(seq, str) and seq == str(index),
                 'Restoration capture must contain the full sequence from one')
        try:
            raw = base64.b64decode(item['body'], validate=True)
            row = json.loads(raw)
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError('Invalid restoration capture body') from error
        _require(isinstance(row, dict) and row.get('id') == item.get('entryId') and
                 isinstance(row.get('id'), str) and bool(row['id']), 'Restoration capture identity mismatch')
        _require(item.get('entryKind', row.get('kind')) == row.get('kind'), 'Restoration capture kind mismatch')
        rows.append(row)
    _require(len({row['id'] for row in rows}) == len(rows), 'Restoration capture has duplicate occurrences')
    return value, rows


def _graph(h, root, ref):
    h.referenced(root, ref)
    with contextlib.closing(sqlite3.connect('file:' + ref['path'] + '?mode=ro&immutable=1', uri=True)) as db:
        _require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Restoration graph integrity failed')
        rows = db.execute('SELECT id,data FROM blobs LIMIT ?', (h.MAX_RECORDS + 1,)).fetchall()
    _require(len(rows) <= h.MAX_RECORDS, 'Restoration graph exceeds bound')
    graph = {identifier: h.digest(raw) for identifier, raw in rows}
    _require(len(graph) == len(rows) and all(isinstance(key, str) for key in graph), 'Restoration graph identities are ambiguous')
    return graph


def _preserved_graph(old, new, slot, label):
    for identifier, digest in old.items():
        # Mutable root slots must be retained under their actual content hash.
        _require(new.get(digest if identifier == slot else identifier) == digest,
                 'Restoration lost ' + label + ' SDK root or blob')


def _subsequence(before, after, renamed, label):
    cursor = 0
    for candidate in after:
        if cursor >= len(before):
            break
        wanted = dict(before[cursor])
        wanted['id'] = renamed.get(wanted['id'], wanted['id'])
        if _canonical(wanted) == _canonical(candidate):
            cursor += 1
    _require(cursor == len(before), 'Restoration lost, edited or reordered ' + label + ' occurrences')


def _authored(h, root, ref, target):
    proof = json.loads(h.referenced(root, ref))
    h.exact(proof, 'schema targetId before after')
    _require(proof['schema'] == 'openbot-same-id-authored-preservation/v1' and proof['targetId'] == target,
             'Restoration authored proof has a different owner')
    sides = []
    for side in ('before', 'after'):
        _require(isinstance(proof[side], list) and 3 <= len(proof[side]) <= h.MAX_RECORDS,
                 'Restoration authored inventory is absent')
        indexed = {}
        for item in proof[side]:
            h.exact(item, 'logicalPath evidence')
            logical = item['logicalPath']
            _require(isinstance(logical, str) and logical and not logical.startswith('/') and
                     '..' not in pathlib.PurePosixPath(logical).parts and logical not in indexed,
                     'Restoration authored inventory has an invalid path')
            raw = h.referenced(root, item['evidence'])
            indexed[logical] = (item['evidence']['sha256'], raw)
        _require({'profile.json', 'settings.json', '@inventory'}.issubset(indexed),
                 'Restoration authored profile, settings or recursive inventory is absent')
        inventory = json.loads(indexed['@inventory'][1])
        h.exact(inventory, 'schema targetId files directories')
        _require(inventory['schema'] == 'openbot-authored-tree-inventory/v1' and inventory['targetId'] == target and
                 isinstance(inventory['files'], list) and inventory['files'] == sorted(set(inventory['files'])) and
                 set(inventory['files']) == set(indexed) - {'@inventory'} and isinstance(inventory['directories'], list) and
                 inventory['directories'] == sorted(set(inventory['directories'])),
                 'Restoration authored inventory coverage differs')
        for directory in inventory['directories']:
            _require(isinstance(directory, str) and directory and not directory.startswith('/') and
                     '..' not in pathlib.PurePosixPath(directory).parts, 'Invalid authored directory inventory')
        profile = json.loads(indexed['profile.json'][1])
        _require(isinstance(profile, dict) and profile.get('id', profile.get('agentId', target)) == target,
                 'Restoration authored profile has a different owner')
        sides.append({key: value[0] for key, value in indexed.items()})
    _require(sides[0] == sides[1], 'Restoration changed authored files, settings, sessions, tools or routines')
    return len(sides[0]) - 1


def _typed(value):
    if value is None:
        return ['null', None]
    if type(value) is int:
        return ['integer', str(value)]
    if type(value) is float:
        return ['real', struct.pack('>d', value).hex()]
    if isinstance(value, str):
        return ['text', value]
    if isinstance(value, bytes):
        return ['blob', value.hex()]
    raise RuntimeError('Unrecognized protected SQLite value type')


def _row_fingerprint(h, rows):
    encoded = [json.dumps([_typed(cell) for cell in row], ensure_ascii=False, separators=(',', ':')) for row in rows]
    encoded.sort(key=lambda value: value.encode())
    return {'rows': len(encoded), 'sha256': h.digest(json.dumps(encoded, ensure_ascii=False, separators=(',', ':')).encode())}


def _protected_store(h, db):
    """Cover DDL, row identity, columns and every nonhistory value, including blobs."""
    schema = db.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
    _require(len(schema) <= h.MAX_RECORDS, 'Protected store schema exceeds bound')
    without_rowid = {row[1]: bool(row[4]) for row in db.execute('PRAGMA table_list') if row[0] == 'main'}
    tables = {}
    for kind, name, _owner, _page, _sql in schema:
        if kind != 'table' or name == 'transcript_entries':
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        column_info = db.execute('PRAGMA table_xinfo(' + quoted + ')').fetchall()
        columns = [row[1] for row in column_info]
        lower = {column.lower() for column in columns}
        rowid = None if without_rowid[name] else next((name for name in ('_rowid_', 'rowid', 'oid') if name not in lower), None)
        _require(without_rowid[name] or rowid is not None, 'Protected SQLite table shadows every rowid alias')
        selection = ','.join('"' + column.replace('"', '""') + '"' for column in columns)
        if rowid is not None:
            selection = '"' + rowid + '",' + selection
        rows = db.execute('SELECT ' + selection + ' FROM ' + quoted + ' LIMIT ?', (h.MAX_RECORDS + 1,)).fetchall()
        _require(len(rows) <= h.MAX_RECORDS, 'Protected SQLite table exceeds bound')
        tables[name] = {'columns': columns, 'rowidColumn': rowid,
                        'columnInfoSHA': h.digest(json.dumps(column_info, ensure_ascii=False, separators=(',', ':')).encode()),
                        'fingerprint': _row_fingerprint(h, rows), 'rows': rows}
    return {'schemaSHA': h.digest(json.dumps(schema, ensure_ascii=False, separators=(',', ':')).encode()), 'tables': tables}


def _store_snapshot(h, root, reference):
    h.referenced(root, reference)
    with contextlib.closing(sqlite3.connect('file:' + reference['path'] + '?mode=ro&immutable=1', uri=True)) as db:
        return _protected_store(h, db)


def _normalize_initialized_metadata(source, candidate, root_slot):
    offset = int(source['rowidColumn'] is not None)
    columns = source['columns']
    _require('key' in columns and 'value' in columns, 'Protected KV columns are invalid')
    key_index, value_index = columns.index('key') + offset, columns.index('value') + offset
    original = [row for row in source['rows'] if row[key_index] == 'metadata']
    updated = [row for row in candidate['rows'] if row[key_index] == 'metadata']
    _require(len(original) == len(updated) == 1, 'Protected root initialization metadata is absent or ambiguous')
    before = json.loads(bytes.fromhex(original[0][value_index]))
    after = json.loads(bytes.fromhex(updated[0][value_index]))
    _require(isinstance(before, dict) and isinstance(after, dict) and after.get('latestRootBlobId') == root_slot,
             'Protected root initialization selected a different root')
    before.pop('latestRootBlobId', None); after.pop('latestRootBlobId', None)
    _require(before == after, 'Protected root initialization changed other metadata fields')
    result = []
    for row in candidate['rows']:
        row = list(row)
        if row[key_index] == 'metadata':
            row[value_index] = original[0][value_index]
        result.append(row)
    return result


def _sequence_transition(h, source, candidate, maximum):
    offset = int(source['rowidColumn'] is not None)
    columns = source['columns']
    _require('name' in columns and 'seq' in columns, 'Protected SQLite sequence columns are invalid')
    name_index, seq_index = columns.index('name') + offset, columns.index('seq') + offset
    def split(table):
        chosen = [row for row in table['rows'] if row[name_index] == 'transcript_entries']
        _require(len(chosen) <= 1 and all(type(row[seq_index]) is int and row[seq_index] >= 0 for row in chosen),
                 'Protected transcript sequence is invalid')
        return (chosen[0] if chosen else None), [row for row in table['rows'] if row[name_index] != 'transcript_entries']
    before, old_rest = split(source); after, new_rest = split(candidate)
    _require(_row_fingerprint(h, old_rest) == _row_fingerprint(h, new_rest), 'Restoration changed another SQLite sequence')
    prior = before[seq_index] if before else 0
    expected = max(prior, maximum)
    _require(after[seq_index] == expected if after else expected == 0 and before is None, 'Restoration transcript sequence is not the exact monotonic maximum')
    if before is not None:
        normalized = list(after); normalized[seq_index] = before[seq_index]
        _require(tuple(normalized) == tuple(before), 'Restoration changed transcript sequence row identity')
    return {'source': str(prior) if before else None, 'candidate': str(after[seq_index]) if after else None,
            'expected': str(expected), 'otherRowsSHA': _row_fingerprint(h, old_rest)['sha256'], 'monotonic': True}


def _validate_store_transition(h, root, proof, current, native):
    source = _store_snapshot(h, root, proof['currentStoreSnapshot'])
    candidate = _store_snapshot(h, root, current['baseline']['store'])
    _require(source['schemaSHA'] == candidate['schemaSHA'] and set(source['tables']) == set(candidate['tables']),
             'Restoration changed full protected SQLite schema')
    state = native.get('storeState')
    _require(native.get('storeStatePreserved') is True and isinstance(state, dict) and
             state.get('sourceSchemaSHA') == source['schemaSHA'] and state.get('candidateSchemaSHA') == candidate['schemaSHA'] and
             state.get('schemaSHA') == candidate['schemaSHA'] and isinstance(state.get('tables'), dict) and
             set(state['tables']) == set(candidate['tables']), 'Restoration complete protected store proof is absent or differs')
    bootstrap = proof['currentRootSha256'] is None and native.get('ui', {}).get('metadataRootInitialized') is True
    _require(state.get('metadataRootInitialized') is bootstrap, 'Restoration protected metadata initialization proof differs')
    sequence = None
    for name, before in source['tables'].items():
        after = candidate['tables'][name]
        for field in ('columns', 'rowidColumn', 'columnInfoSHA'):
            _require(before[field] == after[field], 'Restoration changed protected SQLite columns or row identity')
        report = state['tables'][name]
        _require(all(report.get(key) == after[key] for key in ('columns', 'rowidColumn', 'columnInfoSHA')) and
                 report.get('source') == before['fingerprint'] and report.get('after') == after['fingerprint'] and
                 report.get('candidate') == after['fingerprint'],
                 'Restoration protected table fingerprint differs from actual SQLite bytes')
        expected_before = before['fingerprint'] if name == 'kv' and bootstrap else after['fingerprint']
        _require(report.get('before') == expected_before, 'Restoration pre-bridge protected table state differs')
        normalized = after['fingerprint']['sha256']; policy = 'exact'
        if name == 'kv' and bootstrap:
            normalized_rows = _normalize_initialized_metadata(before, after, ROOT_SLOT)
            normalized = _row_fingerprint(h, normalized_rows)['sha256']; policy = 'metadata-root-initialization'
            _require(normalized == before['fingerprint']['sha256'], 'Restoration bootstrap changed protected KV beyond root initialization')
        elif name == 'sqlite_sequence':
            with contextlib.closing(sqlite3.connect('file:' + current['baseline']['store']['path'] + '?mode=ro&immutable=1', uri=True)) as db:
                maximum = db.execute('SELECT MAX(seq) FROM transcript_entries').fetchone()[0] or 0
            sequence = _sequence_transition(h, before, after, maximum); policy = 'transcript-sequence-monotonic'
        else:
            _require(before['fingerprint'] == after['fingerprint'], 'Restoration changed a protected nonhistory SQLite table')
        _require(report.get('normalizedSHA') == normalized and report.get('policy') == policy,
                 'Restoration protected table preservation policy differs')
    _require(state.get('sqliteSequence') == sequence, 'Restoration protected SQLite sequence proof differs')
    return candidate


def validate(r, root, current, previous=None):
    """Validate a receipt transition after its actual parent was recursively checked.

    Returns evidence counts only. In particular, success is not an applied-copy,
    registration, publication or end-to-end delivery acknowledgement.
    """
    h = _helper()
    root = pathlib.Path(root)
    _require(current.get('schema') == SCHEMA, 'Unknown live restoration receipt schema')
    proof = json.loads(h.referenced(root, current['restorationProof']))
    h.exact(proof, 'schema targetId previousReceipt fullPublishedCapture currentStoreSnapshot currentBlobsSnapshot currentRootSha256 sourceCaptures nativeSdkProof authoredStateProof identifierChanges')
    target = current['newId']
    _require(isinstance(target, str) and _UUID.fullmatch(target), 'Invalid restoration target identity')
    _require(proof['schema'] == PROOF_SCHEMA and proof['targetId'] == target and
             proof['previousReceipt'] == current['previousReceipt'], 'Restoration proof belongs to another transition')
    _require((previous is None) == (current['previousReceipt'] is None), 'Restoration parent is absent or unlinked')
    if previous is not None:
        _require(json.loads(h.referenced(root, current['previousReceipt'])) == previous, 'Restoration parent bytes differ')
        for key in ('oldId', 'newId', 'selectionSourceId', 'backup'):
            _require(current[key] == previous[key], 'Restoration changes its original identity')
    h.exact(current['baseline'], 'store blobs')
    h.referenced(root, current['baseline']['store'])
    candidate = h.db_entries(current['baseline']['store']['path'])
    h.referenced(root, proof['currentStoreSnapshot'])
    existing = h.db_entries(proof['currentStoreSnapshot']['path'])
    published_capture, published = _capture(h, root, proof['fullPublishedCapture'], target)
    _require(candidate[:len(published)] == published, 'Restoration must preserve the full published prefix')
    original = h.db_entries(previous['baseline']['store']['path']) if previous is not None else []
    known_ids = {row['id'] for row in [*original, *existing]}
    changes = proof['identifierChanges']
    _require(isinstance(changes, list) and len(changes) <= h.MAX_RECORDS, 'Invalid restoration identifier changes')
    renamed = {}
    original_ids = {row['id'] for row in original}
    proposed_bounded = {item.get('newId') for item in changes if isinstance(item, dict) and isinstance(item.get('newId'), str)}
    occupied = original_ids | ({row['id'] for row in existing} - proposed_bounded)
    published_by_id = {row['id']: row for row in published}
    # The compact allocator sees the original identifiers, never the already-
    # published bounded replacements; otherwise collision suffixes would drift.
    for item in changes:
        h.exact(item, 'oldId newId')
        old, new = item['oldId'], item['newId']
        _require(isinstance(old, str) and old in known_ids and old not in renamed and
                 len(old.encode()) > 128 and re.fullmatch(r'.+~openbot-branch-[0-9a-f]{64}~*', old) and
                 isinstance(new, str) and new in published_by_id and len(new.encode()) <= 128,
                 'Only actually published bounded synthetic IDs can be restored')
        expected = r.merger.compact_branch_identifier(old, occupied)
        _require(new == expected and new not in renamed.values(), 'Restoration identifier is not canonical and injective')
        for row in [value for value in [*original, *existing] if value['id'] == old]:
            _require({**row, 'id': new} == published_by_id[new], 'Restoration identifier changed published payload')
        occupied.add(new)
        renamed[old] = new
    _subsequence(original, candidate, renamed, 'previous baseline')
    _subsequence(existing, candidate, renamed, 'current native')
    # A new suffix is genuine immutable source evidence or an exact occurrence
    # from the current native branch, never an invented self-source identity.
    # The common receipt validator checks each original-source projection.
    projected_ids = {item['targetEntryId'] for item in current['projections']}
    current_branch = {renamed.get(row['id'], row['id']): {**row, 'id': renamed.get(row['id'], row['id'])} for row in existing}
    _require(all(row['id'] in projected_ids or current_branch.get(row['id']) == row for row in candidate[len(published):]),
             'Restoration suffix is not covered by actual source projections or exact current native occurrences')
    sources = {(source['agentId'], source['kind']): source for source in current['sources']}
    _require(len(sources) == len(current['sources']), 'Duplicate restoration source identity')
    if previous is not None:
        prior_sources = {(source['agentId'], source['kind']): source for source in previous['sources']}
        _require(set(sources) == set(prior_sources), 'Restoration changed original source identities')
        for key, before in prior_sources.items():
            after = sources[key]
            for field in ('generation', 'sdkRootSha256', 'blobSnapshot'):
                _require(after[field] == before[field], 'Restoration changed original source generation or SDK')
            if before['kind'] == 'sqlite':
                _require(after == before, 'Restoration replaced original SQLite evidence')
            before_rows, after_rows = h.source_records(root, before), h.source_records(root, after)
            _require(after_rows[:len(before_rows)] == before_rows, 'Restoration changed original source prefix')
        old_projects = {(item['sourceId'], item['sourceKind'], item['sourceEntryId']): renamed.get(item['targetEntryId'], item['targetEntryId']) for item in previous['projections']}
        new_projects = {(item['sourceId'], item['sourceKind'], item['sourceEntryId']): item['targetEntryId'] for item in current['projections']}
        _require(all(new_projects.get(key) == value for key, value in old_projects.items()),
                 'Restoration changed earlier source variant coverage')
        _require(current['orderResolutions'] == previous['orderResolutions'], 'Restoration changed reviewed ordering resolutions')
    captures = proof['sourceCaptures']
    _require(isinstance(captures, list) and len(captures) <= 512, 'Invalid fresh restoration source captures')
    for ref in captures:
        _capture(h, root, ref)
    current_graph = _graph(h, root, proof['currentBlobsSnapshot'])
    _require(current_graph.get(ROOT_SLOT) == proof['currentRootSha256'] and
             (proof['currentRootSha256'] is not None or not current_graph), 'Restoration current selected root differs')
    if proof['currentRootSha256'] is not None:
        h.sha(proof['currentRootSha256'])
    final_graph = _graph(h, root, current['baseline']['blobs'])
    _preserved_graph(current_graph, final_graph, ROOT_SLOT, 'current')
    sdk = json.loads(h.referenced(root, current['sdkProof']))
    _require(sdk.get('engineSha256') == REVIEWED_ENGINE and sdk.get('rootSlot') == ROOT_SLOT and sdk.get('blobs') == final_graph and
             sdk.get('rootSha256') == final_graph.get(ROOT_SLOT), 'Restoration SDK proof differs from candidate graph')
    if previous is not None:
        prior_sdk = json.loads(h.referenced(root, previous['sdkProof']))
        _preserved_graph(prior_sdk['blobs'], final_graph, prior_sdk['rootSlot'], 'previous')
        _require(prior_sdk['sourceRoots'] == sdk['sourceRoots'], 'Restoration changed original SDK source roots')
    native = json.loads(h.referenced(root, proof['nativeSdkProof']))
    _require(sdk.get('nativeReport') == proof['nativeSdkProof'], 'Restoration SDK report link differs from native bridge proof')
    _require(native.get('schema') == 'openbot-same-id-sdk-restoration/v1' and native.get('targetId') == target and native.get('sourceId') == target,
             'Restoration requires an explicit same-ID native bridge')
    _require((native.get('nativeEngineSHA'), native.get('workerSHA'), native.get('allocatorSHA'), native.get('allocatorHostSHA')) ==
             (REVIEWED_ENGINE, REVIEWED_WORKER, REVIEWED_ALLOCATOR, REVIEWED_ALLOCATOR_HOST), 'Restoration native codec is not reviewed')
    bridge = pathlib.Path(__file__).with_name('same-id-sdk-restoration.mjs')
    _require(native.get('bridgeSHA') == h.digest(bridge.read_bytes()), 'Restoration bridge source differs')
    _require(native.get('currentRootSHA') == proof['currentRootSha256'] == native.get('beforeRootSHA') and
             native.get('afterRootSHA') == sdk['rootSha256'] and native.get('rootSlot') == ROOT_SLOT and
             native.get('blobs') == final_graph and native.get('currentGraph') == current_graph,
             'Restoration native graph or selected context differs')
    inputs = native.get('inputFiles')
    _require(isinstance(inputs, dict) and inputs, 'Restoration native input evidence is absent')
    for path, digest in inputs.items():
        h.referenced(root, {'path': path, 'sha256': digest})
    for ref in [proof['currentStoreSnapshot'], proof['currentBlobsSnapshot'], proof['fullPublishedCapture'], *captures]:
        _require(inputs.get(ref['path']) == ref['sha256'], 'Restoration native inputs omit a reviewed source')
    plan_ref = native.get('planFile')
    if plan_ref is None:
        matches = [{'path': path, 'sha256': value} for path, value in inputs.items() if value == native.get('planFileSHA')]
        _require(len(matches) == 1, 'Restoration native plan reference is ambiguous')
        plan_ref = matches[0]
    plan = json.loads(h.referenced(root, plan_ref))
    _require(plan_ref['sha256'] == native.get('planFileSHA') and plan.get('targetId') == target and
             plan.get('currentStoreSnapshot') == proof['currentStoreSnapshot'] and
             plan.get('fullPublishedCapture') == proof['fullPublishedCapture'] and
             plan.get('originalParentReceipt') == current['previousReceipt'] and
             plan.get('expectedCurrentRootSHA') == proof['currentRootSha256'], 'Restoration native plan binding differs')
    context = native.get('context', {})
    _require(all(context.get(key) is True for key in ('currentPrefixPreserved', 'otherRootFieldsPreserved', 'noPendingTools', 'completeNativeContextExact')) and
             context.get('unresolvedProtoRefs') == 0 and type(context.get('nativeReachableRefs')) is int and context['nativeReachableRefs'] >= 0,
             'Restoration native context or graph is incomplete')
    current_ids = {row['id'] for row in existing}
    current_changes = [change for change in changes if change['oldId'] in current_ids]
    _require(native.get('identifierChanges') == current_changes and plan.get('identifierChanges') == current_changes,
             'Restoration native identifier proof differs from current UI projection')
    reference_proof = native.get('identifierReferenceProof')
    if current_changes:
        expected_graph_hash = h.digest(json.dumps(sorted(current_graph.items()), separators=(',', ':'), ensure_ascii=False).encode())
        _require(isinstance(reference_proof, dict) and reference_proof.get('checkedIds') == sorted(item['oldId'] for item in current_changes) and
                 reference_proof.get('rootSha256') == proof['currentRootSha256'] and reference_proof.get('structuralReferences') == [] and
                 reference_proof.get('graphUnchanged') is True and reference_proof.get('unresolvedProtoRefs') == 0 and
                 reference_proof.get('graphSha256') == expected_graph_hash and reference_proof.get('graphBlobs') == len(current_graph),
                 'Restoration current SDK still references a projected identity')
    else:
        _require(reference_proof is None, 'Unexpected restoration identifier reference proof')
    ui = native.get('ui', {})
    _require(ui.get('originalCurrentRecords') == len(existing) and ui.get('currentRowsByteExact') is (not current_changes) and
             ui.get('currentRowsPayloadExact') is True and ui.get('currentOrderPreserved') is True,
             'Restoration native current transcript preservation is not proven')
    _require(isinstance(ui.get('paging'), list) and {page.get('limit') for page in ui['paging']} == {37, 500} and
             all(page.get('exact') is True and type(page.get('pages')) is int and page['pages'] >= 1 for page in ui['paging']),
             'Restoration native pagination is incomplete')
    _require(ui.get('records') == len(candidate) and all(ui.get(key) is True for key in
             ('previewUnchanged', 'unreadUnchanged', 'transcriptUnchanged', 'otherKVPreserved')),
             'Restoration changed native UI or nonhistory state')
    capacity = native.get('capacity', {})
    _require(capacity.get('fits') is True and all(type(capacity.get(key)) is int and capacity[key] >= 0 for key in
             ('usedTokens', 'contextTokens', 'maxOutputTokens')) and
             capacity['usedTokens'] + capacity['maxOutputTokens'] <= capacity['contextTokens'], 'Restoration context capacity is not proven')
    _require(all(native.get(key) is False for key in ('historicalActionsReplayed', 'liveApplied', 'sourceUpdated')) and
             all(type(native.get(key)) is int and native[key] == 0 for key in ('providerCalls', 'networkAttempts', 'toolAttempts')),
             'Restoration bridge performed a live side effect')
    _require(native.get('files', {}).get('store.db', {}).get('sha256') == current['baseline']['store']['sha256'] and
             native.get('files', {}).get('conversation-blobs.db', {}).get('sha256') == current['baseline']['blobs']['sha256'],
             'Restoration native output differs from candidate bytes')
    source_graphs = native.get('sourceGraphs')
    _require(isinstance(source_graphs, list) and 1 <= len(source_graphs) <= 256 and isinstance(plan.get('sourceGraphs'), list), 'Restoration source graph inventory is absent')
    expected_graphs = [{key: graph[key] for key in ('agentId', 'path', 'sha256', 'rootSHA')} for graph in source_graphs]
    _require(expected_graphs == plan['sourceGraphs'], 'Restoration native graph inputs differ from plan')
    for source in current['sources']:
        if source['kind'] != 'sqlite':
            continue
        expected_original = {'agentId': source['agentId'], **source['blobSnapshot'], 'rootSHA': source['sdkRootSha256']}
        _require(expected_original in expected_graphs, 'Restoration omitted an original full SQLite source graph')
    for graph in source_graphs:
        _require(isinstance(graph, dict) and graph.get('wholeGraphArchived') is True and isinstance(graph.get('graph'), dict), 'Invalid restoration source graph proof')
        ref = {'path': graph['path'], 'sha256': graph['sha256']}
        _require(inputs.get(ref['path']) == ref['sha256'], 'Restoration native inputs omit a source graph')
        actual = _graph(h, root, ref)
        _require(actual == graph['graph'] and actual.get(ROOT_SLOT) == graph.get('rootSHA'), 'Restoration source graph proof differs')
        _preserved_graph(actual, final_graph, ROOT_SLOT, 'source')
    _validate_store_transition(h, root, proof, current, native)
    authored_count = _authored(h, root, proof['authoredStateProof'], target)
    return {'publishedEntries': len(published), 'currentEntries': len(existing), 'candidateEntries': len(candidate),
            'identifierChanges': len(renamed), 'authoredFiles': authored_count, 'liveApplied': False}


_HISTORY_FILES = {'store.db', 'conversation-blobs.db'}
_HISTORY_EXCLUDED = {name + suffix for name in _HISTORY_FILES for suffix in ('', '-wal', '-shm', '-journal')}


def validate_live_graph(r, journal, entry, candidate):
    """Read current native state under the controller's exclusive idle guards.

    This does not acquire dispatch ownership, signal the host or mutate SQLite.
    The caller must hold its normal dispatch lease and native-idle guard through
    acceptance. Authored coverage is the complete target directory only; global
    sidebar sections and pins require a separate controller CAS and are not
    claimed by this proof. WAL contents are deliberately included by ordinary read-only
    SQLite transactions, unlike the immutable detached evidence readers.
    """
    h = _helper()
    root = pathlib.Path(journal['backup'])
    target = entry['newId']
    _require(isinstance(target, str) and _UUID.fullmatch(target), 'Invalid live restoration target identity')
    directory = pathlib.Path(r.m.SD) / 'agents' / target
    for ancestor in [*directory.parents, directory]:
        info = ancestor.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not ancestor.is_symlink(), 'Live restoration target directory is symlinked')
    _require(directory.stat().st_uid == os.getuid(), 'Live restoration target is foreign')
    proof = json.loads(h.referenced(root, candidate['restorationProof']))
    sdk = json.loads(h.referenced(root, candidate['sdkProof']))
    for name in _HISTORY_FILES:
        path = directory / name
        info = path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_size <= h.MAX_FILE,
                 'Live restoration database is foreign, symlinked or oversized')
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = pathlib.Path(str(path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                state = sidecar.lstat()
                _require(stat.S_ISREG(state.st_mode) and state.st_uid == os.getuid(), 'Live restoration SQLite sidecar is unsafe')
    with contextlib.closing(sqlite3.connect('file:' + str(directory / 'store.db') + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        _require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Live restoration store is corrupt')
        metadata = db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
        _require(metadata is not None, 'Live restoration metadata is absent')
        baseline_state = _store_snapshot(h, root, candidate['baseline']['store'])
        live_state = _protected_store(h, db)
        _require(live_state['schemaSHA'] == baseline_state['schemaSHA'] and
                 {key: {field: value[field] for field in ('columns', 'rowidColumn', 'columnInfoSHA', 'fingerprint')} for key, value in live_state['tables'].items()} ==
                 {key: {field: value[field] for field in ('columns', 'rowidColumn', 'columnInfoSHA', 'fingerprint')} for key, value in baseline_state['tables'].items()},
                 'Live protected SQLite schema, KV, blobs, completion inbox or table state differs from candidate')
        parsed = json.loads(bytes.fromhex(metadata[0]))
        _require(parsed.get('agentId') == target and parsed.get('latestRootBlobId') == sdk['rootSlot'],
                 'Live restoration owner or selected root slot differs')
    with contextlib.closing(sqlite3.connect('file:' + str(directory / 'conversation-blobs.db') + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        _require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Live restoration blob graph is corrupt')
        rows = db.execute('SELECT id,data FROM blobs LIMIT ?', (h.MAX_RECORDS + 1,)).fetchall()
        _require(len(rows) <= h.MAX_RECORDS, 'Live restoration graph exceeds bound')
        actual_graph = {identifier: h.digest(raw) for identifier, raw in rows}
    _require(actual_graph == sdk['blobs'] and actual_graph.get(sdk['rootSlot']) == sdk['rootSha256'],
             'Live selected SDK graph differs from the accepted restoration candidate')
    authored = json.loads(h.referenced(root, proof['authoredStateProof']))
    after = {item['logicalPath']: item['evidence'] for item in authored['after']}
    inventory = json.loads(h.referenced(root, after['@inventory']))
    expected = set(inventory['files'])
    seen_files, seen_dirs = set(), set()
    for base, directories, files in os.walk(directory, followlinks=False):
        base = pathlib.Path(base)
        for name in directories:
            child = base / name; state = child.lstat()
            _require(stat.S_ISDIR(state.st_mode) and state.st_uid == os.getuid(), 'Live authored directory is foreign or symlinked')
            seen_dirs.add(child.relative_to(directory).as_posix())
        for name in files:
            child = base / name
            logical = child.relative_to(directory).as_posix()
            if base == directory and name in _HISTORY_EXCLUDED:
                continue
            seen_files.add(logical)
            _require(logical in expected and logical in after, 'Live authored tree contains an unreviewed file')
            raw = h.checked(directory, str(child))
            _require(h.digest(raw) == after[logical]['sha256'], 'Live authored settings, instructions, routines, sessions or tools changed')
    _require(seen_files == expected and seen_dirs == set(inventory['directories']), 'Live authored tree lost a reviewed file or directory')
    return {'selectedGraphExact': True, 'authoredFiles': len(seen_files), 'liveApplied': False}
