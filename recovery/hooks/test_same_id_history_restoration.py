"""Published same-owner recovery preserves actual occurrences and every SDK graph."""
import base64
import copy
import contextlib
import hashlib
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import shutil
import types
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), pathlib.Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


R = load('same-id-history-restoration')
M = load('fleet-state-merge')
TARGET = '00000000-0000-4000-8000-000000000002'
OLD = '00000000-0000-4000-8000-000000000001'


class RestorationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.controller = types.SimpleNamespace(merger=M)
        self.long = 'legacy-' + 'x' * 100 + '~openbot-branch-' + 'a' * 64
        self.short = M.compact_branch_identifier(self.long, {self.long, 'second'})
        self.first = {'id': self.long, 'kind': 'message', 'role': 'user', 'content': 'literal first', 'agentId': TARGET, 'timestampMs': 1}
        self.second = {'id': 'second', 'kind': 'send-message', 'message': {'type': 'text', 'content': 'literal reply'}, 'agentId': TARGET, 'timestampMs': 2}
        self.third = {'id': 'third', 'kind': 'message', 'role': 'user', 'content': 'new published conversation', 'agentId': TARGET, 'timestampMs': 3}
        self.union = [{**self.first, 'id': self.short}, self.second, self.third]
        self.old_graph = {R.ROOT_SLOT: b'prior selected root', 'old-child': b'prior child'}
        self.current_graph = {R.ROOT_SLOT: b'advanced selected root', 'current-child': b'advanced child'}
        self.final_graph = {R.ROOT_SLOT: b'advanced selected root', 'old-child': b'prior child', 'current-child': b'advanced child',
                            self.sha(self.old_graph[R.ROOT_SLOT]): self.old_graph[R.ROOT_SLOT],
                            self.sha(self.current_graph[R.ROOT_SLOT]): self.current_graph[R.ROOT_SLOT]}
        old_store = self.store('old.db', [self.first, self.second])
        old_blobs = self.blobs('old-blobs.db', self.old_graph)
        self.current_store = self.store('current.db', [self.first, self.second])
        current_blobs = self.blobs('current-blobs.db', self.current_graph)
        union_store = self.store('union.db', self.union)
        union_blobs = self.blobs('union-blobs.db', self.final_graph)
        old_sdk = self.write('old-sdk.json', {'sourceRoots': {OLD: self.sha(self.old_graph[R.ROOT_SLOT])}, 'rootSlot': R.ROOT_SLOT,
                                             'blobs': self.hashes(self.old_graph)})
        self.previous = {'schema': 'openbot-history-merge-receipt/v1', 'oldId': OLD, 'newId': TARGET, 'selectionSourceId': OLD,
                         'backup': str(self.root), 'baseline': {'store': old_store, 'blobs': old_blobs}, 'sources': [],
                         'projections': [], 'sourceOrders': [], 'orderResolutions': [], 'sdkProof': old_sdk}
        previous_ref = self.write('previous.json', self.previous)
        self.sdk = {'engineSha256': R.REVIEWED_ENGINE, 'rootSlot': R.ROOT_SLOT, 'rootSha256': self.sha(self.current_graph[R.ROOT_SLOT]),
                    'blobs': self.hashes(self.final_graph), 'sourceRoots': {OLD: self.sha(self.old_graph[R.ROOT_SLOT])}}
        sdk_ref = self.write('sdk.json', self.sdk)
        self.capture = {'capturedAtMs': 1, 'readOnly': True, 'agentId': TARGET, 'generation': '3', 'entries': self.entries(self.union)}
        published_ref = self.write('published.json', self.capture)
        self.plan = {'schemaVersion': 1, 'targetId': TARGET, 'expectedCurrentRootSHA': self.sha(self.current_graph[R.ROOT_SLOT]),
                     'currentStoreSnapshot': self.current_store, 'fullPublishedCapture': published_ref,
                     'originalParentReceipt': previous_ref, 'identifierChanges': [{'oldId': self.long, 'newId': self.short}], 'sourceGraphs': [{'agentId': OLD, **old_blobs, 'rootSHA': self.sha(self.old_graph[R.ROOT_SLOT])}]}
        plan_ref = self.write('plan.json', self.plan)
        self.native = {'schema': 'openbot-same-id-sdk-restoration/v1', 'targetId': TARGET, 'sourceId': TARGET,
                       'nativeEngineSHA': R.REVIEWED_ENGINE, 'workerSHA': R.REVIEWED_WORKER, 'allocatorSHA': R.REVIEWED_ALLOCATOR,
                       'allocatorHostSHA': R.REVIEWED_ALLOCATOR_HOST,
                       'bridgeSHA': self.sha(pathlib.Path(R.__file__).with_name('same-id-sdk-restoration.mjs').read_bytes()),
                       'planFileSHA': plan_ref['sha256'], 'beforeRootSHA': self.plan['expectedCurrentRootSHA'],
                       'currentRootSHA': self.plan['expectedCurrentRootSHA'], 'afterRootSHA': self.sdk['rootSha256'], 'rootSlot': R.ROOT_SLOT,
                       'blobs': self.sdk['blobs'], 'currentGraph': self.hashes(self.current_graph),
                       'context': {'currentPrefixPreserved': True, 'otherRootFieldsPreserved': True, 'noPendingTools': True,
                                   'completeNativeContextExact': True, 'unresolvedProtoRefs': 0, 'nativeReachableRefs': 2},
                       'ui': {'records': len(self.union), 'originalCurrentRecords': 2, 'currentRowsByteExact': False, 'currentRowsPayloadExact': True, 'currentOrderPreserved': True, 'paging': [{'limit': 37, 'pages': 1, 'exact': True}, {'limit': 500, 'pages': 1, 'exact': True}], 'previewUnchanged': True, 'unreadUnchanged': True,
                              'transcriptUnchanged': True, 'otherKVPreserved': True},
                       'capacity': {'fits': True, 'usedTokens': 20, 'contextTokens': 100, 'maxOutputTokens': 20},
                       'historicalActionsReplayed': False, 'liveApplied': False, 'sourceUpdated': False,
                       'providerCalls': 0, 'networkAttempts': 0, 'toolAttempts': 0,
                       'identifierChanges': self.plan['identifierChanges'], 'identifierReferenceProof': {'checkedIds': [self.long], 'rootSha256': self.plan['expectedCurrentRootSHA'], 'structuralReferences': [], 'graphUnchanged': True, 'unresolvedProtoRefs': 0, 'graphSha256': self.sha(json.dumps(sorted(self.hashes(self.current_graph).items()), separators=(',', ':')).encode()), 'graphBlobs': len(self.current_graph)},
                       'sourceGraphs': [{'agentId': OLD, **old_blobs, 'rootSHA': self.sha(self.old_graph[R.ROOT_SLOT]),
                                         'wholeGraphArchived': True, 'graph': self.hashes(self.old_graph)}],
                       'files': {'store.db': {'sha256': union_store['sha256']}, 'conversation-blobs.db': {'sha256': union_blobs['sha256']}},
                       'inputFiles': {ref['path']: ref['sha256'] for ref in [self.current_store, current_blobs, published_ref, plan_ref, previous_ref, old_blobs]}}
        native_ref = self.write('native.json', self.native)
        self.sdk['nativeReport'] = native_ref
        sdk_ref = self.write('sdk.json', self.sdk)
        authored_before = []
        files = {'profile.json': {'agentId': TARGET, 'instructions': 'Keep instructions literally'},
                 'settings.json': {'notificationsEnabled': True},
                 'automations/morning/automation.json': {'enabled': False, 'prompt': 'keep routine'},
                 'sessions/one.json': {'sessionId': 'opaque-session'}, 'tools.json': {'servers': ['example']},
                 'sections.json': {'section': 'original-section'}}
        inventory = {'schema': 'openbot-authored-tree-inventory/v1', 'targetId': TARGET, 'files': sorted(files),
                     'directories': ['automations', 'automations/morning', 'sessions']}
        files['@inventory'] = inventory
        for logical, value in files.items():
            evidence = self.write('auth-before/' + logical.replace('@', '_'), value)
            authored_before.append({'logicalPath': logical, 'evidence': evidence})
        authored_after = [{'logicalPath': item['logicalPath'], 'evidence': self.write('auth-after/' + item['logicalPath'].replace('@', '_'),
                            json.loads(pathlib.Path(item['evidence']['path']).read_bytes()))} for item in authored_before]
        self.authored = {'schema': 'openbot-same-id-authored-preservation/v1', 'targetId': TARGET,
                         'before': authored_before, 'after': authored_after}
        authored_ref = self.write('authored.json', self.authored)
        self.proof = {'schema': R.PROOF_SCHEMA, 'targetId': TARGET, 'previousReceipt': previous_ref,
                      'fullPublishedCapture': published_ref, 'currentStoreSnapshot': self.current_store,
                      'currentBlobsSnapshot': current_blobs, 'currentRootSha256': self.plan['expectedCurrentRootSHA'],
                      'sourceCaptures': [], 'nativeSdkProof': native_ref, 'authoredStateProof': authored_ref,
                      'identifierChanges': [{'oldId': self.long, 'newId': self.short}]}
        self.receipt = {**self.previous, 'schema': R.SCHEMA, 'previousReceipt': previous_ref,
                        'baseline': {'store': union_store, 'blobs': union_blobs}, 'sdkProof': sdk_ref,
                        'restorationProof': self.write('proof.json', self.proof)}

    @staticmethod
    def sha(value):
        return hashlib.sha256(value).hexdigest()

    def hashes(self, graph):
        return {key: self.sha(value) for key, value in graph.items()}

    def write(self, name, value):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return {'path': str(path), 'sha256': self.sha(path.read_bytes())}

    def store(self, name, rows):
        path = self.root / name
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO kv VALUES(?,?)', ('metadata', json.dumps({'agentId': TARGET, 'latestRootBlobId': R.ROOT_SLOT}).encode().hex()))
            db.execute('INSERT INTO kv VALUES(?,?)', ('unreadState', '{"readThrough":2}'))
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)')
            db.execute('INSERT INTO blobs VALUES(?,?)', ('legacy', b'legacy SDK data preserved'))
            db.execute('CREATE TABLE automation_completion_inbox(id TEXT PRIMARY KEY,text TEXT,acknowledged INTEGER)')
            db.execute('INSERT INTO automation_completion_inbox VALUES(?,?,?)', ('receipt-one', 'unacknowledged completion', 0))
            db.execute('CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,entry TEXT)')
            db.executemany('INSERT INTO transcript_entries VALUES(?,?,?)', [(i, row['id'], json.dumps(row)) for i, row in enumerate(rows, 1)]); db.commit()
        return {'path': str(path), 'sha256': self.sha(path.read_bytes())}

    def blobs(self, name, graph):
        path = self.root / name
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)')
            db.executemany('INSERT INTO blobs VALUES(?,?)', graph.items()); db.commit()
        return {'path': str(path), 'sha256': self.sha(path.read_bytes())}

    @staticmethod
    def entries(rows):
        return [{'seq': str(i), 'entryId': row['id'], 'body': base64.b64encode(json.dumps(row).encode()).decode()} for i, row in enumerate(rows, 1)]

    def refresh(self):
        helper = R._helper()
        source = R._store_snapshot(helper, self.root, self.proof['currentStoreSnapshot'])
        target = R._store_snapshot(helper, self.root, self.receipt['baseline']['store'])
        self.native['storeStatePreserved'] = True
        self.native['storeState'] = {'schemaSHA': target['schemaSHA'], 'sourceSchemaSHA': source['schemaSHA'], 'candidateSchemaSHA': target['schemaSHA'],
                                     'metadataRootInitialized': False, 'sqliteSequence': None, 'tables': {}}
        for name, table in target['tables'].items():
            original = source['tables'].get(name, table)
            self.native['storeState']['tables'][name] = {**{key: table[key] for key in ('columns', 'rowidColumn', 'columnInfoSHA')},
                'source': original['fingerprint'], 'before': table['fingerprint'], 'after': table['fingerprint'], 'candidate': table['fingerprint'],
                'normalizedSHA': table['fingerprint']['sha256'], 'policy': 'exact'}
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        self.sdk['nativeReport'] = self.proof['nativeSdkProof']
        self.receipt['sdkProof'] = self.write('sdk.json', self.sdk)
        self.receipt['restorationProof'] = self.write('proof.json', self.proof)

    def check(self, previous=True):
        self.refresh()
        return R.validate(self.controller, self.root, self.receipt, self.previous if previous else None)

    def test_real_published_canonical_ids_and_advanced_selected_root_are_preserved(self):
        self.assertEqual(self.check(), {'publishedEntries': 3, 'currentEntries': 2, 'candidateEntries': 3,
                                        'identifierChanges': 1, 'authoredFiles': 6, 'liveApplied': False})

    def test_candidate_cannot_truncate_or_change_full_publication(self):
        self.receipt['baseline']['store'] = self.store('truncated.db', self.union[:2])
        with self.assertRaisesRegex(RuntimeError, 'full published prefix'): self.check()

    def test_capture_cannot_start_after_one(self):
        self.capture['entries'][0]['seq'] = '2'
        self.proof['fullPublishedCapture'] = self.write('bad-capture.json', self.capture)
        with self.assertRaisesRegex(RuntimeError, 'start at one'): self.check()

    def test_old_message_cannot_disappear_even_if_new_publication_omits_it(self):
        self.capture['entries'] = self.entries([self.union[0], self.third])
        self.proof['fullPublishedCapture'] = self.write('omitted-capture.json', self.capture)
        self.receipt['baseline']['store'] = self.store('omitted.db', [self.union[0], self.third])
        with self.assertRaisesRegex(RuntimeError, 'lost, edited or reordered'): self.check()

    def test_previous_order_cannot_be_rewritten(self):
        swapped = [self.union[1], self.union[0], self.union[2]]
        self.capture['entries'] = self.entries(swapped)
        self.proof['fullPublishedCapture'] = self.write('reordered-capture.json', self.capture)
        self.receipt['baseline']['store'] = self.store('reordered.db', swapped)
        with self.assertRaisesRegex(RuntimeError, 'lost, edited or reordered'): self.check()

    def test_arbitrary_or_missing_id_projection_is_rejected(self):
        self.proof['identifierChanges'][0]['newId'] = 'invented'
        with self.assertRaisesRegex(RuntimeError, 'actually published'): self.check()
        self.proof['identifierChanges'] = []
        with self.assertRaisesRegex(RuntimeError, 'lost, edited or reordered'): self.check()

    def test_changed_live_context_root_is_not_accepted(self):
        self.proof['currentRootSha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'current selected root'): self.check()

    def test_original_and_current_sdk_children_must_both_survive(self):
        graph = {key: value for key, value in self.final_graph.items() if key != 'old-child'}
        self.receipt['baseline']['blobs'] = self.blobs('lost-sdk.db', graph)
        self.sdk['blobs'] = self.hashes(graph)
        self.receipt['sdkProof'] = self.write('sdk.json', self.sdk)
        with self.assertRaisesRegex(RuntimeError, 'previous SDK'): self.check()

    def test_added_source_identity_is_rejected(self):
        self.receipt['sources'] = [{'agentId': 'invented', 'kind': 'sqlite'}]
        with self.assertRaisesRegex(RuntimeError, 'original source identities'): self.check()

    def test_arbitrary_tail_without_source_coverage_is_rejected(self):
        self.receipt['baseline']['store'] = self.store('fabricated.db', [*self.union, {**self.third, 'id': 'invented'}])
        with self.assertRaisesRegex(RuntimeError, 'actual source projections'): self.check()

    def test_native_proof_must_use_reviewed_bridge_and_no_side_effects(self):
        self.native['providerCalls'] = 1
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        with self.assertRaisesRegex(RuntimeError, 'live side effect'): self.check()
        self.native['providerCalls'] = 0; self.native['workerSHA'] = '0' * 64
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        with self.assertRaisesRegex(RuntimeError, 'not reviewed'): self.check()

    def test_authored_routine_session_tools_and_sections_are_real_byte_proofs(self):
        self.authored['after'][2]['evidence'] = self.write('changed-routine.json', {'enabled': True})
        self.proof['authoredStateProof'] = self.write('authored.json', self.authored)
        with self.assertRaisesRegex(RuntimeError, 'changed authored files'): self.check()

    def test_immutable_evidence_cannot_escape_root_or_be_modified(self):
        self.proof['currentStoreSnapshot'] = {**self.current_store, 'path': '/tmp/escape.db'}
        with self.assertRaisesRegex(RuntimeError, 'escapes'): self.check()
        self.proof['currentStoreSnapshot'] = self.current_store
        pathlib.Path(self.current_store['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Immutable'): self.check()

    def test_empty_local_ui_can_restore_nonempty_full_publication_without_parent(self):
        self.receipt['previousReceipt'] = None; self.proof['previousReceipt'] = None
        self.current_store = self.store('empty-ui.db', [])
        self.proof['currentStoreSnapshot'] = self.current_store
        self.proof['currentBlobsSnapshot'] = self.blobs('empty-blobs.db', {})
        self.proof['currentRootSha256'] = None; self.proof['identifierChanges'] = []
        self.native.update(currentRootSHA=None, beforeRootSHA=None, currentGraph={})
        self.plan.update(currentStoreSnapshot=self.current_store, expectedCurrentRootSHA=None, originalParentReceipt=None, identifierChanges=[])
        self.native.update(identifierChanges=[], identifierReferenceProof=None)
        self.native['ui'].update(originalCurrentRecords=0, currentRowsByteExact=True)
        plan_ref = self.write('empty-plan.json', self.plan); self.native['planFileSHA'] = plan_ref['sha256']
        self.native['inputFiles'] = {ref['path']: ref['sha256'] for ref in [self.current_store, self.proof['currentBlobsSnapshot'], self.proof['fullPublishedCapture'], plan_ref, self.previous['baseline']['blobs']]}
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        self.assertEqual(self.check(previous=False)['publishedEntries'], 3)

    def test_previous_legacy_mapping_remains_canonical_when_current_ui_already_bounded(self):
        self.proof['currentStoreSnapshot'] = self.store('already-bounded.db', self.union[:2])
        self.plan['currentStoreSnapshot'] = self.proof['currentStoreSnapshot']
        self.plan['identifierChanges'] = []
        plan_ref = self.write('plan-bounded.json', self.plan)
        self.native['planFileSHA'] = plan_ref['sha256']
        self.native['identifierChanges'] = []; self.native['identifierReferenceProof'] = None
        self.native['ui']['currentRowsByteExact'] = True
        self.native['inputFiles'][plan_ref['path']] = plan_ref['sha256']
        self.native['inputFiles'][self.proof['currentStoreSnapshot']['path']] = self.proof['currentStoreSnapshot']['sha256']
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        self.assertEqual(self.check()['identifierChanges'], 1)

    def test_native_alias_reference_or_incomplete_pagination_is_rejected(self):
        self.native['identifierReferenceProof']['structuralReferences'] = ['active turn user message id']
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        with self.assertRaisesRegex(RuntimeError, 'still references'): self.check()
        self.native['identifierReferenceProof']['structuralReferences'] = []
        self.native['ui']['paging'] = []
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)
        with self.assertRaisesRegex(RuntimeError, 'pagination'): self.check()

    def add_platform_source(self, include_delta=False):
        first = {**self.first, 'id': 'source-first', 'agentId': OLD}
        delta = {**self.third, 'id': 'source-tail', 'agentId': OLD, 'content': 'genuine new source delta'}
        before = self.write('source-before.json', {'generation': 7, 'entries': self.entries([first])})
        after = self.write('source-after.json', {'generation': 7, 'entries': self.entries([first, delta] if include_delta else [first])})
        source = {'agentId': OLD, 'kind': 'platform', **before, 'generation': 7, 'sdkRootSha256': None, 'blobSnapshot': None}
        self.previous['sources'] = [source]
        self.previous['projections'] = [{'sourceId': OLD, 'sourceKind': 'platform', 'sourceEntryId': 'source-first', 'targetEntryId': self.long}]
        self.previous['sourceOrders'] = [{'sourceId': OLD, 'sourceKind': 'platform', 'entryIds': ['source-first']}]
        previous_ref = self.write('previous.json', self.previous)
        self.receipt['previousReceipt'] = previous_ref; self.proof['previousReceipt'] = previous_ref
        self.plan['originalParentReceipt'] = previous_ref
        self.receipt['sources'] = [{**source, **after}]
        self.receipt['projections'] = [{**self.previous['projections'][0], 'targetEntryId': self.short}]
        self.receipt['sourceOrders'] = copy.deepcopy(self.previous['sourceOrders'])
        if include_delta:
            self.receipt['projections'].append({'sourceId': OLD, 'sourceKind': 'platform', 'sourceEntryId': 'source-tail', 'targetEntryId': 'source-tail'})
            self.receipt['sourceOrders'][0]['entryIds'].append('source-tail')
            rows = [*self.union, {**delta, 'agentId': TARGET}]
            self.receipt['baseline']['store'] = self.store('union-with-delta.db', rows)
            self.native['files']['store.db']['sha256'] = self.receipt['baseline']['store']['sha256']
            self.native['ui']['records'] = len(rows)
        fresh = self.write('source-fresh.json', {'capturedAtMs': 2, 'readOnly': True, 'agentId': OLD, 'generation': 7,
                                               'entries': self.entries([first, delta] if include_delta else [first])})
        self.proof['sourceCaptures'] = [fresh]
        plan_ref = self.write('new-plan.json', self.plan)
        self.native['planFileSHA'] = plan_ref['sha256']
        self.native['inputFiles'].update({ref['path']: ref['sha256'] for ref in [previous_ref, plan_ref, fresh]})
        self.proof['nativeSdkProof'] = self.write('native.json', self.native)

    def test_genuine_new_source_suffix_keeps_published_and_previous_native_occurrences(self):
        self.add_platform_source(include_delta=True)
        result = self.check()
        self.assertEqual((result['publishedEntries'], result['candidateEntries']), (3, 4))

    def set_gapped_source_capture(self, sequences):
        if not self.receipt['sources']:
            self.add_platform_source(include_delta=True)
        ref = self.proof['sourceCaptures'][0]
        capture = json.loads(pathlib.Path(ref['path']).read_bytes())
        for row, sequence in zip(capture['entries'], sequences):
            row['seq'] = sequence
        changed = self.write('source-actual-gapped.json', capture)
        self.proof['sourceCaptures'] = [changed]
        self.native['inputFiles'][changed['path']] = changed['sha256']
        return changed

    def test_actual_complete_source_capture_may_retain_real_sequence_gaps(self):
        ref = self.set_gapped_source_capture(['1', '334'])
        original_bytes = pathlib.Path(ref['path']).read_bytes()
        self.assertEqual(self.check()['candidateEntries'], 4)
        self.assertEqual(pathlib.Path(ref['path']).read_bytes(), original_bytes)
        self.assertEqual(json.loads(original_bytes)['entries'][1]['seq'], '334')

    def test_source_capture_cannot_start_late_repeat_or_reverse_a_sequence(self):
        for sequences in (['2', '334'], ['1', '1'], ['1', '0'], ['1', '-1'], ['1', '0334']):
            with self.subTest(sequences=sequences):
                self.set_gapped_source_capture(sequences)
                with self.assertRaisesRegex(RuntimeError, 'sequence'): self.check()

    def test_positive_source_sequences_cannot_run_backwards(self):
        rows = self.entries(self.union)
        for row, sequence in zip(rows, ['1', '334', '168']):
            row['seq'] = sequence
        ref = self.write('source-positive-backwards.json', {'capturedAtMs': 3, 'readOnly': True, 'agentId': OLD,
                                                           'generation': '7', 'entries': rows})
        with self.assertRaisesRegex(RuntimeError, 'strictly increase'):
            R._capture(R._helper(), self.root, ref)

    def test_destination_publication_still_requires_every_sequence_despite_source_gaps(self):
        self.set_gapped_source_capture(['1', '334'])
        self.capture['entries'][1]['seq'] = '9'
        ref = self.write('destination-illegal-gap.json', self.capture)
        self.proof['fullPublishedCapture'] = ref
        with self.assertRaisesRegex(RuntimeError, 'full sequence'): self.check()

    def test_original_source_generation_or_earlier_payload_cannot_be_replaced(self):
        self.add_platform_source(include_delta=True)
        self.receipt['sources'][0]['generation'] = 8
        with self.assertRaisesRegex(RuntimeError, 'original source generation'): self.check()
        self.receipt['sources'][0]['generation'] = 7
        ref = self.receipt['sources'][0]
        capture = json.loads(pathlib.Path(ref['path']).read_bytes())
        event = json.loads(base64.b64decode(capture['entries'][0]['body']))
        event['content'] = 'rewritten original source'
        capture['entries'][0]['body'] = base64.b64encode(json.dumps(event).encode()).decode()
        ref.update(self.write('source-edited.json', capture))
        with self.assertRaisesRegex(RuntimeError, 'original source prefix'): self.check()

    def test_current_native_unpublished_tail_is_preserved_without_a_self_source(self):
        fourth = {**self.third, 'id': 'native-unpublished', 'content': 'written locally, not published yet'}
        current_ref = self.store('current-with-unpublished.db', [self.first, self.second, fourth])
        self.proof['currentStoreSnapshot'] = current_ref; self.plan['currentStoreSnapshot'] = current_ref
        union_ref = self.store('union-with-unpublished.db', [*self.union, fourth])
        self.receipt['baseline']['store'] = union_ref
        self.native['files']['store.db']['sha256'] = union_ref['sha256']
        self.native['ui'].update(originalCurrentRecords=3, records=4)
        plan_ref = self.write('unpublished-plan.json', self.plan)
        self.native['planFileSHA'] = plan_ref['sha256']
        self.native['inputFiles'].update({ref['path']: ref['sha256'] for ref in [current_ref, plan_ref]})
        self.assertEqual(self.check()['candidateEntries'], 4)
        edited = {**fourth, 'content': 'invented instead of preserved'}
        self.receipt['baseline']['store'] = self.store('edited-unpublished.db', [*self.union, fourth, {**edited, 'id': 'forged'}])
        with self.assertRaisesRegex(RuntimeError, 'actual source projections or exact current native occurrences'): self.check()

    def make_parentless_sqlite_source(self):
        self.receipt['previousReceipt'] = None; self.proof['previousReceipt'] = None
        self.plan['originalParentReceipt'] = None
        source_store = self.store('original-source.db', [{**self.first, 'agentId': OLD}, {**self.second, 'agentId': OLD}])
        with contextlib.closing(sqlite3.connect(source_store['path'])) as db:
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (json.dumps({'agentId': OLD, 'latestRootBlobId': R.ROOT_SLOT}).encode().hex(),)); db.commit()
        source_store['sha256'] = self.sha(pathlib.Path(source_store['path']).read_bytes())
        source_blobs = self.previous['baseline']['blobs']
        self.receipt['sources'] = [{'agentId': OLD, 'kind': 'sqlite', **source_store, 'generation': None,
                                    'sdkRootSha256': self.sha(self.old_graph[R.ROOT_SLOT]), 'blobSnapshot': source_blobs}]
        self.receipt['projections'] = [{'sourceId': OLD, 'sourceKind': 'sqlite', 'sourceEntryId': self.long, 'targetEntryId': self.short},
                                       {'sourceId': OLD, 'sourceKind': 'sqlite', 'sourceEntryId': self.second['id'], 'targetEntryId': self.second['id']}]
        self.receipt['sourceOrders'] = [{'sourceId': OLD, 'sourceKind': 'sqlite', 'entryIds': [self.long, self.second['id']]}]
        plan_ref = self.write('parentless-plan.json', self.plan)
        self.native['planFileSHA'] = plan_ref['sha256']
        self.native['inputFiles'][plan_ref['path']] = plan_ref['sha256']

    def test_parentless_restoration_requires_every_original_full_sqlite_graph(self):
        self.make_parentless_sqlite_source()
        self.assertEqual(self.check(previous=False)['candidateEntries'], 3)
        alternate = self.blobs('unreviewed-original-blobs.db', self.old_graph)
        self.receipt['sources'][0]['blobSnapshot'] = alternate
        with self.assertRaisesRegex(RuntimeError, 'omitted an original full SQLite source graph'): self.check(previous=False)

    def test_parentless_restoration_cannot_keep_only_original_root_and_drop_its_child(self):
        self.make_parentless_sqlite_source()
        reduced = {key: value for key, value in self.final_graph.items() if key != 'old-child'}
        self.receipt['baseline']['blobs'] = self.blobs('parentless-childlost.db', reduced)
        self.sdk['blobs'] = self.hashes(reduced); self.native['blobs'] = self.sdk['blobs']
        self.native['files']['conversation-blobs.db']['sha256'] = self.receipt['baseline']['blobs']['sha256']
        with self.assertRaisesRegex(RuntimeError, 'lost source SDK root or blob'): self.check(previous=False)

    def test_candidate_nonhistory_blobs_inbox_and_kv_cannot_be_erased(self):
        for table in ('blobs', 'automation_completion_inbox', 'kv'):
            with self.subTest(table=table):
                candidate = self.store('lost-protected-' + table + '.db', self.union)
                with contextlib.closing(sqlite3.connect(candidate['path'])) as db:
                    db.execute('DELETE FROM ' + table); db.commit()
                candidate['sha256'] = self.sha(pathlib.Path(candidate['path']).read_bytes())
                self.receipt['baseline']['store'] = candidate
                self.native['files']['store.db']['sha256'] = candidate['sha256']
                with self.assertRaisesRegex(RuntimeError, 'protected nonhistory SQLite table'): self.check()

    def test_candidate_and_live_full_sqlite_ddl_changes_are_rejected(self):
        candidate = self.store('changed-schema.db', self.union)
        with contextlib.closing(sqlite3.connect(candidate['path'])) as db:
            db.execute('CREATE INDEX changed_schema ON kv(value)'); db.commit()
        candidate['sha256'] = self.sha(pathlib.Path(candidate['path']).read_bytes())
        self.receipt['baseline']['store'] = candidate
        self.native['files']['store.db']['sha256'] = candidate['sha256']
        with self.assertRaisesRegex(RuntimeError, 'full protected SQLite schema'): self.check()

    def test_live_nonhistory_completion_or_legacy_blob_is_compared_to_candidate(self):
        directory = self.prepare_live()
        for statement in ('UPDATE blobs SET data=x\'00\'', 'UPDATE automation_completion_inbox SET acknowledged=1'):
            with self.subTest(statement=statement):
                shutil.copyfile(self.receipt['baseline']['store']['path'], directory / 'store.db')
                with contextlib.closing(sqlite3.connect(directory / 'store.db')) as db:
                    db.execute(statement); db.commit()
                with self.assertRaisesRegex(RuntimeError, 'protected SQLite schema, KV, blobs, completion inbox'): self.live_check()

    def prepare_live(self):
        sd = self.root / 'native'; directory = sd / 'agents' / TARGET
        directory.mkdir(parents=True)
        shutil.copyfile(self.receipt['baseline']['store']['path'], directory / 'store.db')
        shutil.copyfile(self.receipt['baseline']['blobs']['path'], directory / 'conversation-blobs.db')
        with contextlib.closing(sqlite3.connect(directory / 'store.db')) as db:
            metadata = {'agentId': TARGET, 'latestRootBlobId': R.ROOT_SLOT}
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (json.dumps(metadata).encode().hex(),)); db.commit()
        for item in self.authored['after']:
            if item['logicalPath'] == '@inventory': continue
            target = directory / item['logicalPath']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item['evidence']['path'], target)
        self.controller.m = types.SimpleNamespace(SD=sd)
        self.refresh()
        return directory

    def live_check(self):
        return R.validate_live_graph(self.controller, {'backup': str(self.root)}, {'newId': TARGET}, self.receipt)

    def test_live_graph_proves_actual_owned_database_and_complete_authored_tree(self):
        directory = self.prepare_live()
        before = {str(path): self.sha(path.read_bytes()) for path in directory.rglob('*') if path.is_file()}
        self.assertEqual(self.live_check(), {'selectedGraphExact': True, 'authoredFiles': 6, 'liveApplied': False})
        self.assertEqual(before, {str(path): self.sha(path.read_bytes()) for path in directory.rglob('*') if path.is_file()})

    def test_live_graph_includes_wal_instead_of_ignoring_new_root(self):
        directory = self.prepare_live()
        with contextlib.closing(sqlite3.connect(directory / 'conversation-blobs.db')) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('UPDATE blobs SET data=? WHERE id=?', (b'unreviewed WAL root', R.ROOT_SLOT)); db.commit()
            self.assertTrue(pathlib.Path(str(directory / 'conversation-blobs.db') + '-wal').exists())
            with self.assertRaisesRegex(RuntimeError, 'selected SDK graph differs'): self.live_check()

    def test_live_authored_change_missing_file_extra_file_or_empty_directory_is_rejected(self):
        directory = self.prepare_live()
        path = directory / 'sessions/one.json'; original = path.read_bytes()
        path.write_bytes(b'{"different":true}')
        with self.assertRaisesRegex(RuntimeError, 'instructions, routines, sessions or tools changed'): self.live_check()
        path.unlink()
        with self.assertRaisesRegex(RuntimeError, 'lost a reviewed file'): self.live_check()
        path.write_bytes(original)
        (directory / 'unexpected').mkdir()
        with self.assertRaisesRegex(RuntimeError, 'lost a reviewed file or directory'): self.live_check()
        (directory / 'unexpected').rmdir()
        (directory / 'unexpected.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'unreviewed file'): self.live_check()

    def test_live_authored_symlink_is_never_followed(self):
        directory = self.prepare_live()
        target = directory / 'tools.json'; target.unlink(); target.symlink_to(self.root / 'sdk.json')
        with self.assertRaisesRegex(RuntimeError, 'regular file'): self.live_check()

    def test_live_metadata_cannot_select_another_root(self):
        directory = self.prepare_live()
        with contextlib.closing(sqlite3.connect(directory / 'store.db')) as db:
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (json.dumps({'agentId': TARGET, 'latestRootBlobId': 'elsewhere'}).encode().hex(),)); db.commit()
        with self.assertRaisesRegex(RuntimeError, 'protected SQLite schema'): self.live_check()


if __name__ == '__main__': unittest.main()
