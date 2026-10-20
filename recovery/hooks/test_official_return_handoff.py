"""Fresh identity return orchestration with real SQLite and immutable receipts.

The callbacks below model the typed native codec boundary. Their synthetic blob
payloads are not evidence of native decoding; the native checker has separate
JavaScript tests. These tests exercise coordinator ownership, source capture,
merge provenance, hidden install, and crash recovery without live RPCs.
"""
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
import unittest
import types
from unittest import mock

import test_remint_box_fleet as fleet
import test_custom_dispatch_recovery as dispatch

SPEC = importlib.util.spec_from_file_location('official_return_handoff', pathlib.Path(__file__).with_name('official-return-handoff.py'))
H = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(H)
ROOT_SLOT = b'sand-live-conversation-root-v1__'.hex()


def message(identifier, timestamp, owner, text):
    return {'id': identifier, 'timestampMs': timestamp, 'agentId': owner,
            'kind': 'message', 'role': 'user', 'content': text, 'isStreaming': False}


def cloud(values):
    return [{'seq': str(i + 1), 'body': base64.b64encode(H.encoded(value)).decode()}
            for i, value in enumerate(values)]


class FreshReturnTests(unittest.TestCase):
    write = fleet.RemintTests.write
    def rpc(self,method,body):
        if method!='ListGrokBotTranscriptEntries':return fleet.RemintTests.rpc(self,method,body)
        self.calls.append((method,copy.deepcopy(body)))
        values=copy.deepcopy(self.server_transcripts.get(body['agentId'],[]))
        if 'beforeSeq'in body:values=[row for row in values if int(row['seq'])<int(body['beforeSeq'])]
        values=sorted(values,key=lambda row:int(row['seq']),reverse=True)[:body.get('limit',500)]
        return {'generation':1,'entries':values}
    def gateway(self, method, body):
        if method != 'listAgents':
            return fleet.RemintTests.gateway(self, method, body)
        self.gateway_calls.append((method, copy.deepcopy(body)))
        if hasattr(self, 'live_listing'):
            return copy.deepcopy(self.live_listing)
        # Native manager overlay is an explicit authoritative fixture input;
        # disk visibility and the captured SDK are not running-state evidence.
        return [{'id': path.name, 'harness': 'box', 'isRunning': False, 'isRunningTurn': False}
                for path in (self.root / 'agents').iterdir() if path.is_dir()]

    def recovery(self):
        recovery = dispatch.M.Recovery(self.root, self.r, sleep=lambda _: None,
                                       clock=lambda: 100.0, worker_lock_fd=getattr(self, 'fd', None))
        recovery.module = fleet.M
        self.write(self.root / 'computer-recovery-state.json', {'pendingHostReload': False})
        return recovery

    def setUp(self):
        fleet.RemintTests.setUp(self)
        # Identity references in role instructions are intentionally absent;
        # unlike structural ownership, quoted profile text is never rewritten.
        self.profile['custom'] = {'budget': 8192}
        self.write(self.source / 'profile.json', self.profile)
        self.first = message('first', 1, fleet.OLD, 'original literal')
        self.set_rows(self.source, [self.first])
        self.server_transcripts[fleet.OLD] = cloud([self.first])
        self.server_transcripts[fleet.NEW] = cloud([{**self.first, 'agentId': fleet.NEW}])
        dispatch.DispatchTests.official_round_trip(self)
        self.target = self.root / 'agents' / fleet.NEW
        self.local = message('local-advanced', 2, fleet.NEW, 'Local advanced checkpoint')
        self.set_rows(self.target, [{**self.first, 'agentId': fleet.NEW}, self.local])
        self.server_transcripts[fleet.NEW] = cloud([{**self.first, 'agentId': fleet.NEW}, self.local])
        self.late = message('official-new', 3, fleet.OLD, 'Official literal\nkeep exactly')
        self.server_transcripts[fleet.OLD] = cloud([self.first, self.late])
        self.set_root(self.target, b'synthetic-current-local-typed-root')
        self.write(self.root / 'openbot-plan.json', {'catalog': {'models': [
            {'id': 'provider:model', 'providerId': 'provider', 'slug': 'model',
             'contextTokens': 10000, 'maxOutputTokens': 1000}]}})
        self.fd = os.open(self.root / 'openbot-custom-dispatch.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, self.fd)
        self.rec = self.recovery()
        self.builder = H.FreshReturn(self.rec, self.fd, checkpoint=self.checkpoint, append=self.append,
                                     activity=self.activity)
        self.checkpoint_calls = []; self.append_calls = []; self.activity_calls = []
        self.calls.clear(); self.gateway_calls.clear()

    def set_rows(self, folder, values):
        with contextlib.closing(sqlite3.connect(folder / 'store.db')) as db:
            db.execute('DELETE FROM transcript_entries')
            db.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',
                           [(i+1, value['id'], json.dumps(value)) for i, value in enumerate(values)])
            db.commit()

    def set_root(self, folder, raw):
        with contextlib.closing(sqlite3.connect(folder / 'store.db')) as db:
            metadata = json.loads(bytes.fromhex(db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
            metadata['latestRootBlobId'] = ROOT_SLOT
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (H.encoded(metadata).hex(),)); db.commit()
        with contextlib.closing(sqlite3.connect(folder / 'conversation-blobs.db')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS blobs(id TEXT PRIMARY KEY,data BLOB)')
            db.execute('INSERT OR REPLACE INTO blobs VALUES(?,?)', (ROOT_SLOT, raw)); db.commit()

    def activity(self, identities):
        # Synthetic observation boundary; no native Watch or live status is
        # claimed by this coordinator fixture, and admission remains unfenced.
        self.activity_calls.append(list(identities))
        return {'checkedSources': len(identities), 'scope': 'known-default-session', 'knownSessionId': '',
                'observedNotRunning': True, 'admissionFenced': False, 'sampledAtMs': int(H.time.time() * 1000),
                'sessionCounts': {aid: 0 for aid in identities}}

    def checkpoint(self, folder, plan):
        self.checkpoint_calls.append(copy.deepcopy(plan))
        report = {'schema': 'openbot-current-local-sdk-report/v1', 'syntheticBoundary': True,
                  'helperSha256': H.sha(pathlib.Path(H.__file__).with_name('current-local-sdk-proof.mjs').read_bytes()),
                  'sourceId': plan['sourceId'], 'targetId': plan['targetId'],
                  'rootSha256': plan['expectedRootSHA'], 'workerSha256': self.builder.validator.IDENTIFIER_WORKER_SHA,
                  **{key: True for key in ('rootRoundtrip', 'currentRootByteExact', 'completeContextExact',
                     'noPendingTools', 'quotedHistoryUnchanged', 'graphUnchanged', 'allSourcesByteUnchanged', 'metadataOwnerVerified')},
                  'structuralOwnerReferences': [], 'unresolvedProtoRefs': 0,
                  'providerCalls': 0, 'networkAttempts': 0, 'toolAttempts': 0, 'capacity': {'fits': True}}
        self.builder.tail.write(folder / 'checkpoint-fixture-report.json', report, True)
        return {'schema': 'openbot-sdk-merge-proof/v1', 'engineSha256': self.builder.tail.REVIEWED_HOST_SHA,
                'rootSlot': ROOT_SLOT, 'rootSha256': plan['expectedRootSHA'],
                'sourceRoots': plan['sourceRoots'], 'blobs': self.builder.tail.graph(folder / 'conversation-blobs.db'),
                'nativeReport': self.builder.tail.reference(folder / 'checkpoint-fixture-report.json')}

    def append(self, folder, plan):
        self.append_calls.append(copy.deepcopy(plan))
        with contextlib.closing(sqlite3.connect(folder / 'conversation-blobs.db')) as db:
            previous = db.execute('SELECT data FROM blobs WHERE id=?', (ROOT_SLOT,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO blobs(id,data) VALUES(?,?)', (H.sha(previous), previous))
            db.execute('UPDATE blobs SET data=? WHERE id=?',
                       (H.encoded({'syntheticNativeBoundaryRecords': plan['records']}), ROOT_SLOT)); db.commit()
        graph = self.builder.tail.graph(folder / 'conversation-blobs.db')
        proof = {'schema': 'openbot-history-tail-codec/v1', 'engineSha256': self.builder.tail.REVIEWED_HOST_SHA,
                 'oldRootSha256': plan['expectedRootSHA'], 'rootSlot': ROOT_SLOT,
                 'workerSHA': self.builder.validator.IDENTIFIER_WORKER_SHA, 'blobs': graph,
                 'rootSha256': graph[ROOT_SLOT], 'networkAttempts': 0, 'toolAttempts': 0, 'providerCalls': 0,
                 'historicalActionsReplayed': False, 'context': {key: True for key in
                    ('roundtrip', 'literalEquality', 'originalPrefixPreserved', 'otherRootFieldsPreserved')},
                 'sourcePlanFileSHA': H.sha((folder / 'plan.json').read_bytes()),
                 'bridgeSha256': H.sha(pathlib.Path(H.__file__).with_name('history-tail-codec.mjs').read_bytes()),
                 'capacity': {'fits': True}, 'files': {name: {'sha256': H.sha((folder / name).read_bytes())}
                                                     for name in ('store.db', 'conversation-blobs.db')}}
        self.builder.tail.write(folder / 'proof.json', proof, True)
        return proof

    def protected(self):
        roots = [self.source, self.target]
        return {p: p.read_bytes() for root in roots for p in root.rglob('*') if p.is_file()}

    def assert_protected(self, before):
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(any(name.startswith(('Create', 'Set', 'Update')) for name, _ in self.calls))
        self.assertTrue(all(name == 'listAgents' for name, _ in self.gateway_calls))

    def test_fresh_generation_retains_advanced_local_prefix_and_official_literal(self):
        previous = self.r.read(); before = self.protected()
        result = self.builder.run(); entry = result['entries'][0]
        self.assertEqual(entry['oldId'], fleet.NEW)
        self.assertNotIn(entry['newId'], (fleet.OLD, fleet.NEW))
        self.assertEqual(entry['phase'], 'copied')
        self.assertEqual(result['previousGenerations'][-1], previous)
        destination = self.root / 'agents' / entry['newId']
        values = self.builder.validator.db_entries(destination / 'store.db')
        self.assertEqual([value['content'] for value in values],
                         ['original literal', 'Local advanced checkpoint', 'Official literal\nkeep exactly'])
        self.assertEqual([value['agentId'] for value in values], [entry['newId']] * 3)
        self.assertEqual(self.append_calls[0]['records'][0]['text'], self.late['content'])
        self.assertEqual(len(self.checkpoint_calls), 1)
        self.assertTrue(json.loads((destination / 'settings.json').read_bytes())['hiddenFromSidebar'])
        routine = destination / 'automations/routine/automation.json'
        self.assertFalse(json.loads(routine.read_bytes())['enabled'])
        self.assertEqual(entry['copyProof']['automationStates'], {'automations/routine/automation.json': True})
        self.builder.validator.validate(self.r, result, entry)
        state = json.loads((self.root / H.STATE).read_bytes())
        self.assertEqual(state['phase'], 'waiting-for-publication')
        self.assertTrue(state['authorityCommitted']); self.assertFalse(state['nativeAppDeliveryVerified'])
        self.assert_protected(before)

    def test_temporal_harness_drift_without_messages_still_uses_fresh_identity(self):
        self.server_transcripts[fleet.OLD] = cloud([self.first])
        self.roster[1]['harness'] = 'temporal'
        before = self.protected(); previous = self.r.read()
        result = self.builder.run(); entry = result['entries'][0]
        self.assertNotEqual(entry['newId'], fleet.NEW)
        self.assertEqual(self.append_calls, [])
        self.assertEqual(len(self.checkpoint_calls), 1)
        self.assertEqual(result['previousGenerations'][-1], previous)
        self.assertEqual(len(self.builder.validator.db_entries(self.root / 'agents' / entry['newId'] / 'store.db')), 2)
        self.assert_protected(before)

    def test_actual_temporal_current_source_keeps_advanced_local_checkpoint_and_cloud_tail(self):
        self.roster[1]['harness'] = 'temporal'
        self.live_listing = [{'id': fleet.NEW, 'harness': 'temporal', 'isRunning': False, 'isRunningTurn': False}]
        self.server_transcripts[fleet.OLD] = cloud([self.first])
        tail = message('current-temporal-cloud-tail', 3, fleet.NEW, 'new current Temporal branch literal')
        self.server_transcripts[fleet.NEW] = cloud([{**self.first, 'agentId': fleet.NEW}, self.local, tail])
        before = self.protected(); previous = self.r.read()
        result = self.builder.run(); entry = result['entries'][0]
        self.assertNotIn(entry['newId'], (fleet.OLD, fleet.NEW))
        values = self.builder.validator.db_entries(self.root / 'agents' / entry['newId'] / 'store.db')
        self.assertEqual([value['content'] for value in values],
                         ['original literal', 'Local advanced checkpoint', 'new current Temporal branch literal'])
        self.assertEqual(result['previousGenerations'][-1], previous)
        self.assertTrue(self.activity_calls)
        self.assertTrue(all(fleet.NEW in identities for identities in self.activity_calls))
        inventory = json.loads(self.builder.validator.referenced(result['backup'], entry['returnRetainedSources']))
        self.assertEqual(inventory['schema'], 'openbot-retained-return-sources/v1')
        self.assertEqual(inventory['destinationId'], entry['newId'])
        self.assertEqual(inventory['knownSessionId'], '')
        self.assertFalse(inventory['admissionFenced']); self.assertFalse(inventory['futureMessagesMerged'])
        self.assertEqual({row['agentId']: row['serverId'] for row in inventory['sources']}, {fleet.OLD: '123', fleet.NEW: '456'})
        for source in inventory['sources']:
            self.assertEqual(source['sessionId'], '')
            self.builder.validator.referenced(result['backup'], source['platform'])
            self.builder.validator.referenced(result['backup'], source['store'])
            self.builder.validator.referenced(result['backup'], source['blobs'])
        self.assert_protected(before)

    def test_unintentional_temporal_drift_preserves_current_local_routine_intent(self):
        self.roster[1]['harness']='temporal'
        self.write(self.source/'settings.json',{'hiddenFromSidebar':True})
        self.write(self.target/'settings.json',{'hiddenFromSidebar':False})
        self.server_transcripts[fleet.OLD]=cloud([self.first])
        late=message('new-on-known-current',3,fleet.NEW,'new current default literal')
        self.server_transcripts[fleet.NEW]=cloud([{**self.first,'agentId':fleet.NEW},self.local,late])
        result=self.builder.run();entry=result['entries'][0]
        authority=json.loads(self.builder.validator.referenced(result['backup'],entry['returnRoutineAuthority']))
        self.assertEqual(authority['ownerId'],fleet.NEW)
        self.assertEqual(authority['selection'],'current-local')
        self.assertEqual(entry['activationAutomationStates'],{'automations/routine/automation.json':True})
        self.assertFalse(json.loads((self.root/'agents'/entry['newId']/'automations/routine/automation.json').read_bytes())['enabled'])

    def test_ambiguous_selected_routine_owner_visibility_refuses_before_install(self):
        self.write(self.target/'settings.json',{'hiddenFromSidebar':False})
        before=self.protected();journal=self.r.read()
        with self.assertRaisesRegex(RuntimeError,'owner visibility is ambiguous'):self.builder.run()
        self.assertEqual(self.r.read(),journal);self.assert_protected(before)
        self.assertEqual(set(path.name for path in(self.root/'agents').iterdir()),{fleet.OLD,fleet.NEW})

    def test_known_nondefault_viewer_or_routine_session_refuses_before_install(self):
        for source in ('viewer', 'routine'):
            with self.subTest(source=source):
                if source == 'viewer':
                    self.roster[0]['viewerSessionId'] = 'known-secondary-session'
                else:
                    self.roster[0].pop('viewerSessionId', None)
                    self.cloud_routines[fleet.OLD] = [{'automationId': fleet.OTHER, 'recordJson': json.dumps(
                        {'id': 'known-routine', 'sessionId': 'known-routine-session', 'isEnabled': False})}]
                before = self.protected(); journal = self.r.read()
                with self.assertRaisesRegex(RuntimeError, 'known nondefault'):
                    self.builder.run()
                self.assertEqual(self.r.read(), journal)
                self.assertEqual(set(path.name for path in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
                self.assert_protected(before)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])

    def test_coordinator_supported_official_return_registers_publishes_resumes_and_activates(self):
        previous = self.r.read()
        stores = {path: path.read_bytes() for source in (self.source, self.target)
                  for path in source.iterdir() if path.name in ('store.db', 'conversation-blobs.db')}
        original_load = dispatch.M.load
        def factory(recovery, inherited_fd):
            # Inject only native observation/codec boundaries. The complete
            # capture, immutable planning, install and coordinator remain real.
            builder = H.FreshReturn(recovery, inherited_fd, checkpoint=self.checkpoint,
                                    append=self.append, activity=self.activity)
            self.builder = builder
            return builder
        def load(name):
            return types.SimpleNamespace(FreshReturn=factory) if name == 'official-return-handoff' else original_load(name)
        def rpc(method, body):
            if method == 'CreateGrokBotAgent':
                self.created = fleet.row(body['agentId'], 'box', '789')
            return self.rpc(method, body)
        self.r.m.rpc = rpc; self.r.m.gateway = self.gateway
        with mock.patch.object(dispatch.M, 'load', side_effect=load):
            first = self.recovery().run()
            self.assertEqual(first['phase'], 'waiting-for-publication')
            journal = self.r.read(); entry = journal['entries'][0]; fresh = entry['newId']
            self.assertEqual(entry['phase'], 'registered-box')
            self.assertNotIn(fresh, (fleet.OLD, fleet.NEW))
            self.assertEqual(journal['previousGenerations'][-1], previous)
            destination = self.root / 'agents' / fresh
            self.assertTrue(json.loads((destination / 'settings.json').read_bytes())['hiddenFromSidebar'])
            self.server_transcripts[fresh] = cloud(self.builder.validator.db_entries(destination / 'store.db'))
            marker = self.r.reload.read(self.root)
            self.assertEqual(marker['requestId'], journal['publicationReloadRequestId'])
            marker['phase'] = 'acknowledged'; self.r.reload.write(self.root, marker)
            second = self.recovery().run()
        self.assertEqual(second['phase'], 'bound-box')
        self.assertEqual(self.r.read()['entries'][0]['newId'], fresh)
        self.assertEqual(self.r.read()['entries'][0]['phase'], 'active')
        self.assertEqual(sum(method == 'CreateGrokBotAgent' for method, _ in self.calls), 1)
        self.assertEqual([value['content'] for value in self.builder.validator.db_entries(destination / 'store.db')],
                         ['original literal', 'Local advanced checkpoint', 'Official literal\nkeep exactly'])
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fresh])
        self.assertEqual(self.server_settings['mainAgentId'], fresh)
        self.assertEqual({path: path.read_bytes() for path in stores}, stores)
        self.assertFalse(second['nativeAppDeliveryVerified'])

    def test_crash_after_hidden_install_resumes_exact_immutable_intent(self):
        previous = self.r.read(); before = self.protected(); install = self.builder.install
        def crash(entry):
            install(entry)
            raise InterruptedError('simulated process loss after hidden install')
        with mock.patch.object(self.builder, 'install', side_effect=crash), self.assertRaises(InterruptedError):
            self.builder.run()
        state = json.loads((self.root / H.STATE).read_bytes())
        self.assertEqual(self.r.read(), previous)
        prepared = pathlib.Path(state['preparedIntent']['path']); prepared_bytes = prepared.read_bytes()
        fresh_id = state['destinations'][fleet.NEW]
        self.assertTrue((self.root / 'agents' / fresh_id).exists())
        with mock.patch.object(self.builder, 'prepare', side_effect=AssertionError('must resume exact prepared intent')):
            result = self.builder.run()
        self.assertEqual(result['entries'][0]['newId'], fresh_id)
        self.assertEqual(prepared.read_bytes(), prepared_bytes)
        self.assertEqual(json.loads((self.root / H.STATE).read_bytes())['requestId'], state['requestId'])
        self.assertEqual(len(self.append_calls), 1)
        self.assertEqual(result['previousGenerations'][-1], previous)
        self.assert_protected(before)

    def test_missing_current_sdk_refuses_before_any_old_store_change(self):
        (self.target / 'conversation-blobs.db').unlink()
        before = self.protected(); journal = (self.root / 'openbot-box-remint.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'SDK graph is missing'):
            self.builder.run()
        self.assertEqual((self.root / 'openbot-box-remint.json').read_bytes(), journal)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
        self.assert_protected(before)

    def test_unknown_owner_refuses_before_checkpoint_and_without_writes(self):
        self.roster[0]['viewerIsOwner'] = None
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'ownership changed'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assertEqual(self.checkpoint_calls, [])
        self.assert_protected(before)

    def test_incomplete_published_local_prefix_is_never_replaced(self):
        self.server_transcripts[fleet.NEW] = cloud([{**self.first, 'agentId': fleet.NEW}])
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'published prefix'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assertEqual(self.checkpoint_calls, [])
        self.assert_protected(before)

    def test_rich_or_peer_official_history_refuses_without_overwriting_old_files(self):
        for extras in ({'attachments': []}, {'recipientAgentId': fleet.OTHER}, {'isStreaming': True}, {'unknown': True}):
            with self.subTest(extras=extras):
                self.server_transcripts[fleet.OLD] = cloud([self.first, {**self.late, **extras}])
                before = self.protected(); journal = self.r.read()
                with self.assertRaises(RuntimeError): self.builder.run()
                self.assertEqual(self.r.read(), journal); self.assert_protected(before)
        self.assertEqual(self.checkpoint_calls, [])

    def test_changed_source_root_after_checkpoint_refuses_before_install(self):
        original = self.checkpoint
        def moving(folder, plan):
            proof = original(folder, plan)
            self.set_root(self.target, b'new-native-current-root')
            return proof
        self.builder.checkpoint = moving
        journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'checkpoint changed'): self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})

    def test_changed_model_route_after_checkpoint_refuses_before_install(self):
        original = self.checkpoint
        def moving(folder, plan):
            proof = original(folder, plan)
            path = self.root / 'openbot-plan.json'; value = json.loads(path.read_bytes())
            value['catalog']['models'][0]['contextTokens'] += 1; self.write(path, value)
            return proof
        self.builder.checkpoint = moving
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'route changed'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assert_protected(before)

    def test_historical_send_author_and_wake_are_literal_provenance_only(self):
        event = {'id': 'reply', 'kind': 'send-message', 'timestampMs': 4,
                 'agentId': fleet.OLD, 'author': {'id': fleet.OLD, 'name': 'Assistant'},
                 'wake': 'historical-request-source', 'message': {'type': 'text', 'content': 'unchanged'}}
        self.assertEqual(self.builder.literal(event, fleet.OLD), ('assistant', 'unchanged'))
        for extra in ({'agentId': fleet.OTHER}, {'author': {'id': fleet.OTHER, 'name': 'Peer'}},
                      {'author': {'id': fleet.OLD, 'name': 'Assistant', 'role': 'assistant'}},
                      {'wake': ''}, {'wake': 'line\nbreak'}, {'wake': {'agentId': fleet.OLD}},
                      {'message': {'type': 'text', 'content': 'unchanged', 'execute': True}}):
            with self.subTest(extra=extra), self.assertRaises(RuntimeError):
                self.builder.literal({**event, **extra}, fleet.OLD)

    def test_earlier_divergent_branch_requires_review_not_reordering(self):
        self.server_transcripts[fleet.OLD] = cloud([self.first, {**self.late, 'timestampMs': 1.5}])
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'literal append'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assert_protected(before)

    def test_later_same_identifier_variant_retains_both_literal_bodies(self):
        self.server_transcripts[fleet.OLD] = cloud([
            self.first, {**self.late, 'id': self.local['id'], 'content': 'different Official variant'}])
        before = self.protected()
        result = self.builder.run(); entry = result['entries'][0]
        values = self.builder.validator.db_entries(self.root / 'agents' / entry['newId'] / 'store.db')
        self.assertEqual([value['content'] for value in values],
                         ['original literal', 'Local advanced checkpoint', 'different Official variant'])
        self.assertEqual(len(set(value['id'] for value in values)), 3)
        self.assertEqual(values[1]['id'], self.local['id'])
        self.assertNotEqual(values[2]['id'], self.local['id'])
        self.assertLessEqual(len(values[2]['id'].encode()), 128)
        self.builder.validator.validate(self.r, result, entry)
        self.assert_protected(before)

    def test_historical_send_author_and_wake_survive_archive_without_execution(self):
        sent = {'id': 'official-sent', 'timestampMs': 3, 'agentId': fleet.OLD,
                'kind': 'send-message', 'author': {'id': fleet.OLD, 'name': 'Assistant'},
                'wake': 'historical-source', 'message': {'type': 'text', 'content': 'exact sent literal'}}
        self.server_transcripts[fleet.OLD] = cloud([self.first, sent])
        before = self.protected()
        result = self.builder.run(); entry = result['entries'][0]
        values = self.builder.validator.db_entries(self.root / 'agents' / entry['newId'] / 'store.db')
        self.assertEqual(values[-1], {**sent, 'agentId': entry['newId']})
        record = self.append_calls[0]['records'][0]
        self.assertEqual(record['role'], 'assistant'); self.assertEqual(record['text'], 'exact sent literal')
        self.assertNotIn('wake', record); self.assertNotIn('author', record)
        self.assert_protected(before)

    def test_missing_typed_root_slot_refuses_before_codec_boundary(self):
        with contextlib.closing(sqlite3.connect(self.target / 'store.db')) as db:
            metadata = json.loads(bytes.fromhex(db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
            metadata.pop('latestRootBlobId')
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (H.encoded(metadata).hex(),)); db.commit()
        with contextlib.closing(sqlite3.connect(self.target / 'conversation-blobs.db')) as db:
            db.execute('DELETE FROM blobs'); db.commit()
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'current typed Local SDK checkpoint'): self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assert_protected(before)

    def test_wrong_current_checkpoint_proof_does_not_create_destination(self):
        original = self.checkpoint
        def wrong(folder, plan):
            return {**original(folder, plan), 'rootSha256': '0' * 64}
        self.builder.checkpoint = wrong
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'current native checkpoint proof differs'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assertEqual(self.append_calls, [])
        self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
        self.assert_protected(before)

    def test_wrong_detached_native_append_proof_does_not_create_destination(self):
        original = self.append
        def wrong(folder, plan):
            return {**original(folder, plan), 'historicalActionsReplayed': True}
        self.builder.append = wrong
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'detached native append proof differs'): self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
        self.assert_protected(before)

    def test_prepared_evidence_tampering_never_rebuilds_or_reuses_hidden_copy(self):
        install = self.builder.install
        def crash(entry):
            install(entry); raise InterruptedError('after hidden install')
        with mock.patch.object(self.builder, 'install', side_effect=crash), self.assertRaises(InterruptedError):
            self.builder.run()
        state = json.loads((self.root / H.STATE).read_bytes())
        path = pathlib.Path(state['preparedIntent']['path']); path.write_bytes(path.read_bytes() + b' ')
        before = self.protected(); journal = self.r.read()
        with mock.patch.object(self.builder, 'prepare', side_effect=AssertionError('never rebuild pinned intent')):
            with self.assertRaisesRegex(RuntimeError, 'Immutable merge evidence changed'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assert_protected(before)

    def test_second_return_captures_explicit_selected_ancestor_and_all_intermediate_sources(self):
        terminal = '00000000-0000-4000-8000-000000000004'
        third = self.root / 'agents' / terminal
        shutil.copytree(self.target, third)
        profile = json.loads((third / 'profile.json').read_bytes()); profile['serverId'] = '789'
        self.write(third / 'profile.json', profile)
        self.set_rows(third, [{**self.first, 'agentId': terminal}, {**self.local, 'agentId': terminal}])
        self.set_root(third, b'synthetic-third-current-checkpoint')
        with contextlib.closing(sqlite3.connect(third / 'store.db')) as db:
            row = db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
            metadata = json.loads(bytes.fromhex(row[0])); metadata['agentId'] = terminal
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (H.encoded(metadata).hex(),)); db.commit()
        self.roster.append(fleet.row(terminal, 'box', '789'))
        self.server_transcripts[terminal] = cloud([{**self.first, 'agentId': terminal}, {**self.local, 'agentId': terminal}])
        prior = self.r.read(); entry = prior['entries'][0]
        journal = {**prior, 'officialReturnModelsCopied': True, 'previousGenerations': [prior], 'entries': [{**entry,
            'oldId': fleet.NEW, 'newId': terminal, 'sourceServerId': '456', 'serverId': '789',
            'selectionSourceId': fleet.OLD, 'selectionSourceServerId': '123', 'phase': 'active'}]}
        self.r.save(journal)
        self.write(self.root / 'openbot-official-dispatch-state.json', {
            'phase': 'stock-roster-selected', 'mode': 'official', 'identityMapping': {terminal: fleet.OLD}})
        models_path = self.root / 'openbot-bot-models.json'; models = json.loads(models_path.read_bytes())
        models['reasoning'][terminal] = 'max'; self.write(models_path, models)
        before = self.protected(); third_before = {p: p.read_bytes() for p in third.rglob('*') if p.is_file()}
        result = self.builder.run(); fresh = result['entries'][0]
        self.assertEqual(fresh['oldId'], terminal)
        self.assertEqual(fresh['selectionSourceId'], fleet.OLD)
        self.assertEqual(fresh['selectionSourceServerId'], '123')
        self.assertEqual(json.loads(models_path.read_bytes())['reasoning'][fresh['newId']], 'max')
        self.assertIs(result['officialReturnModelsCopied'], True)
        state = json.loads((self.root / H.STATE).read_bytes())
        intent = json.loads(pathlib.Path(state['preparedIntent']['path']).read_bytes())
        self.assertNotIn('officialReturnModelsCopied', intent['journal'])
        receipt = json.loads(pathlib.Path(fresh['historyMergeReceipt']['path']).read_bytes())
        self.assertEqual({source['agentId'] for source in receipt['sources']}, {fleet.OLD, fleet.NEW, terminal})
        self.assertEqual(result['previousGenerations'][-1]['entries'], journal['entries'])
        self.builder.validator.validate(self.r, result, fresh)
        self.assertEqual({p: p.read_bytes() for p in third_before}, third_before)
        self.assert_protected(before)

    def test_dispatch_temporal_drift_invokes_fresh_return_with_inherited_lock(self):
        self.roster[1]['harness'] = 'temporal'
        original = dispatch.M.load; handoff = mock.Mock()
        handoff.FreshReturn.return_value.run.side_effect = InterruptedError('captured fresh-return dispatch')
        def load(name):
            return handoff if name == 'official-return-handoff' else original(name)
        before = self.protected()
        with mock.patch.object(dispatch.M, 'load', side_effect=load), mock.patch.object(self.r, 'reselect_active') as reselect:
            with self.assertRaisesRegex(InterruptedError, 'captured fresh-return dispatch'):
                self.rec.run()
        handoff.FreshReturn.assert_called_once_with(self.rec, self.fd)
        handoff.FreshReturn.return_value.run.assert_called_once_with()
        reselect.assert_not_called()
        self.assertEqual(self.rec.progress['untrackedSourceAgents'], 0)
        self.assertEqual(self.r.read()['entries'][0]['phase'], 'active')
        self.assert_protected(before)

    def test_dispatch_fresh_return_waits_for_matching_native_reload_ack_before_verification(self):
        journal = self.builder.run(); entry = journal['entries'][0]
        self.created = fleet.row(entry['newId'], 'box', '789')
        self.r.register(entry['oldId']); self.r.publish()
        request_id = self.r.read()['publicationReloadRequestId']
        for request in ({'requestId': request_id, 'phase': 'pending'},
                        {'requestId': '00000000-0000-4000-8000-000000000005', 'phase': 'acknowledged'}):
            with self.subTest(request=request), mock.patch.object(self.r.reload, 'read', return_value=request), \
                    mock.patch.object(self.r, 'verify') as verify, mock.patch.object(self.r, 'activate') as activate:
                result = self.recovery().run()
                self.assertEqual(result['phase'], 'waiting-for-publication')
                verify.assert_not_called(); activate.assert_not_called()
        request_id = self.r.read()['publicationReloadRequestId']
        with mock.patch.object(self.r.reload, 'read', return_value={'requestId': request_id, 'phase': 'acknowledged'}), \
                mock.patch.object(self.r, 'verify', return_value=[{'status': 'unpublished'}]) as verify, \
                mock.patch.object(self.r, 'activate') as activate:
            self.assertEqual(self.recovery().run()['phase'], 'waiting-for-publication')
            verify.assert_called_once(); activate.assert_not_called()

    def test_incomplete_native_current_report_is_not_authority(self):
        original = self.checkpoint
        def bad(folder, plan):
            proof = original(folder, plan)
            report = pathlib.Path(proof['nativeReport']['path'])
            value = json.loads(report.read_bytes()); value['noPendingTools'] = False
            report.write_bytes(H.encoded(value))
            proof['nativeReport'] = self.builder.tail.reference(report)
            return proof
        self.builder.checkpoint = bad
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'current native checkpoint proof differs'): self.builder.run()
        self.assertEqual(self.r.read(), journal); self.assertEqual(self.append_calls, [])
        self.assert_protected(before)

    def test_model_copy_is_once_per_generation_and_preserves_later_user_reasoning(self):
        result = self.builder.run(); fresh = result['entries'][0]['newId']
        self.assertIs(result['officialReturnModelsCopied'], True)
        models_path = self.root / 'openbot-bot-models.json'
        models = json.loads(models_path.read_bytes())
        self.assertEqual(models['reasoning'][fresh], 'xhigh')
        models['reasoning'][fresh] = 'max'; self.write(models_path, models)
        chosen = models_path.read_bytes()
        # Restart while publication is incomplete: copying the original
        # source's override again would erase this later user decision.
        self.builder.run()
        self.builder.finish_models(self.r.read())
        self.assertEqual(models_path.read_bytes(), chosen)
        # Ordinary all-active health checks call finish_models before the
        # roster guard. They must honor the same durable completion marker.
        journal = self.r.read(); journal['entries'][0].update(phase='active', serverId='789')
        self.r.save(journal)
        for _ in range(2):
            recovery = self.recovery()
            with mock.patch.object(recovery, 'check_roster_scope', return_value=False):
                recovery.run()
            self.assertEqual(models_path.read_bytes(), chosen)
        self.assertIs(self.r.read()['officialReturnModelsCopied'], True)

    def test_live_wal_capture_includes_committed_rows_without_touching_source_sqlite_files(self):
        store = self.target / 'store.db'
        connection = sqlite3.connect(store)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        committed = message('wal-committed', 2.5, fleet.NEW, 'committed only in live WAL')
        connection.execute('INSERT INTO transcript_entries VALUES(?,?,?)', (3, committed['id'], json.dumps(committed)))
        connection.commit()
        self.server_transcripts[fleet.NEW] = cloud([{**self.first, 'agentId': fleet.NEW}, self.local, committed])
        (self.target / 'conversation-blobs.db').unlink()
        files = [pathlib.Path(str(store) + suffix) for suffix in ('', '-wal', '-shm')]
        self.assertTrue(all(path.exists() for path in files))
        def file_state(path):
            data = path.read_bytes(); info = path.stat()
            return (data, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        before = {path: file_state(path) for path in files}
        protected = self.protected(); journal = self.r.read()
        original_connect = sqlite3.connect
        def isolated_connect(database, *args, **kwargs):
            name = str(database)
            for native in (self.source / 'store.db', store):
                if name == str(native) or name.startswith('file:' + str(native)):
                    raise AssertionError('Capture must never open the live native SQLite database')
            return original_connect(database, *args, **kwargs)
        captured = self.backup / 'isolated-wal-snapshot.db'
        with mock.patch.object(H.sqlite3, 'connect', side_effect=isolated_connect):
            H.Capture(self.rec).database(store, captured)
            self.assertEqual(self.builder.validator.db_entries(captured)[-1], committed)
            with self.assertRaisesRegex(RuntimeError, 'SDK graph is missing'):
                self.builder.run()
        self.assertEqual({path: file_state(path) for path in files}, before)
        self.assertEqual(self.r.read(), journal)
        snapshots = list(self.backup.rglob('sources/' + fleet.NEW + '/store.db'))
        self.assertTrue(snapshots)
        self.assertEqual(self.builder.validator.db_entries(snapshots[-1])[-1], committed)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assert_protected(protected)

    def test_pending_completion_queue_is_never_acknowledged_or_copied(self):
        store = self.target / 'store.db'
        with contextlib.closing(sqlite3.connect(store)) as db:
            db.execute('CREATE TABLE automation_completion_inbox(id TEXT PRIMARY KEY,payload TEXT,acknowledged)')
            db.commit()
        for acknowledged in (0, None, 2, 'unknown'):
            with self.subTest(acknowledged=acknowledged):
                with contextlib.closing(sqlite3.connect(store)) as db:
                    db.execute('DELETE FROM automation_completion_inbox')
                    db.execute('INSERT INTO automation_completion_inbox VALUES(?,?,?)',
                               ('pending-completion', 'preserve pending native payload', acknowledged)); db.commit()
                before = self.protected(); journal = self.r.read()
                with self.assertRaisesRegex(RuntimeError, 'pending completions must settle'):
                    self.builder.run()
                self.assertEqual(self.r.read(), journal)
                self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
                with contextlib.closing(sqlite3.connect(store)) as db:
                    self.assertEqual(db.execute('SELECT * FROM automation_completion_inbox').fetchall(),
                                     [('pending-completion', 'preserve pending native payload', acknowledged)])
                self.assert_protected(before)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])

    def test_acknowledged_completion_history_is_retained_in_hidden_generation(self):
        historical = ('finished-completion', 'exact acknowledged native payload', 1)
        with contextlib.closing(sqlite3.connect(self.target / 'store.db')) as db:
            db.execute('CREATE TABLE automation_completion_inbox(id TEXT PRIMARY KEY,payload TEXT,acknowledged)')
            db.execute('INSERT INTO automation_completion_inbox VALUES(?,?,?)', historical); db.commit()
        before = self.protected()
        result = self.builder.run(); entry = result['entries'][0]
        destination = self.root / 'agents' / entry['newId']
        with contextlib.closing(sqlite3.connect(destination / 'store.db')) as db:
            self.assertEqual(db.execute('SELECT * FROM automation_completion_inbox').fetchall(), [historical])
        self.assertTrue(json.loads((destination / 'settings.json').read_bytes())['hiddenFromSidebar'])
        self.assertFalse(json.loads((destination / 'automations/routine/automation.json').read_bytes())['enabled'])
        self.builder.validator.validate(self.r, result, entry)
        self.assert_protected(before)

    def test_unknown_populated_native_queue_requires_review_without_clearing_it(self):
        with contextlib.closing(sqlite3.connect(self.target / 'store.db')) as db:
            db.execute('CREATE TABLE unknown_native_queue(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO unknown_native_queue VALUES(?,?)', ('work', 'unknown execution state')); db.commit()
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(RuntimeError, 'unknown native queue requires review'):
            self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assert_protected(before)

    def test_active_generation_monitors_recorded_sources_after_current_sdk_advances(self):
        journal = self.builder.run(); entry = journal['entries'][0]; fresh = entry['newId']
        destination = self.root / 'agents' / fresh
        entry.update(phase='active', serverId='789'); journal['preferencesFinalized'] = True
        self.r.save(journal)
        profile = json.loads((destination / 'profile.json').read_bytes()); profile['serverId'] = '789'
        self.write(destination / 'profile.json', profile)
        self.write(destination / 'settings.json', {'hiddenFromSidebar': False})
        self.write(self.source / 'settings.json', {'hiddenFromSidebar': True})
        self.roster.append(fleet.row(fresh, 'box', '789'))
        self.server_settings.update(mainAgentId=fresh, pinnedAgents={'agentIds': [fresh]},
            sidebarSections={'sections': [{'id': 'me', 'name': 'Me', 'agentIds': [fresh, fleet.OTHER]},
                                          {'id': 'unassigned', 'name': 'Unassigned'}]})
        # A real later Local turn may advance both current SDK and UI. The
        # reviewed receipt constrains retained ancestor evidence, not this new
        # generation's legitimate continuation.
        self.set_root(destination, b'synthetic-legitimate-later-current-sdk')
        values = self.builder.validator.db_entries(destination / 'store.db')
        values.append(message('current-later', 4, fresh, 'later legitimate Local turn'))
        self.set_rows(destination, values)
        self.server_transcripts[fresh] = cloud(values)
        with mock.patch.object(self.r, 'verify') as verify:
            self.assertEqual(self.r.reselect_active()['state'], 'already-selected')
            verify.assert_not_called()
        later = message('ancestor-later', 5, fleet.OLD, 'late Official ancestor history')
        self.server_transcripts[fleet.OLD] = cloud([self.first, self.late, later])
        native = {p: p.read_bytes() for p in destination.rglob('*') if p.is_file()}
        before = self.protected(); authority = self.r.read()
        with self.assertRaisesRegex(fleet.M.ReturnHistoryRequired, 'Reviewed return source advanced'):
            self.r.reselect_active()
        self.assertEqual(self.r.read(), authority)
        # The ordinary coordinator must turn that exact guard into another
        # fresh-generation request even though selection already looks correct.
        original = dispatch.M.load; handoff = mock.Mock()
        handoff.FreshReturn.return_value.run.side_effect = InterruptedError('fresh generation requested for late ancestor')
        def load(name):
            return handoff if name == 'official-return-handoff' else original(name)
        recovery = self.recovery()
        with mock.patch.object(dispatch.M, 'load', side_effect=load):
            with self.assertRaisesRegex(InterruptedError, 'fresh generation requested for late ancestor'):
                recovery.run()
        handoff.FreshReturn.return_value.run.assert_called_once_with()
        self.assertEqual({p: p.read_bytes() for p in native}, native)
        self.assertEqual(self.r.read(), authority); self.assert_protected(before)

    def test_retained_default_scope_detects_same_id_middle_edit_and_deletion(self):
        self.test_coordinator_supported_official_return_registers_publishes_resumes_and_activates()
        journal=self.r.read();entry=journal['entries'][0]
        inventory_path=pathlib.Path(entry['returnRetainedSources']['path']);inventory=inventory_path.read_bytes()
        original=copy.deepcopy(self.server_transcripts[fleet.OLD])
        stores={path:path.read_bytes()for root in(self.source,self.target,self.root/'agents'/entry['newId'])
                for path in root.iterdir()if path.name in('store.db','conversation-blobs.db')}
        for action in('edit','delete'):
            with self.subTest(action=action):
                values=copy.deepcopy(original)
                if action=='edit':
                    body=json.loads(base64.b64decode(values[0]['body']));body['content']='edited earlier literal'
                    values[0]['body']=base64.b64encode(H.encoded(body)).decode()
                else:values.pop(0)
                self.server_transcripts[fleet.OLD]=values
                # The last row/generation remain unchanged. A complete source
                # comparison must still refuse the already-selected fast path.
                self.builder.validator.source_freshness(self.r,journal,entry,{})
                with self.assertRaisesRegex(fleet.M.ReturnHistoryRequired,'Retained source history changed'):
                    self.r.reselect_active()
                self.assertEqual(self.r.read(),journal)
                self.assertEqual(inventory_path.read_bytes(),inventory)
                self.assertEqual({path:path.read_bytes()for path in stores},stores)
                self.assertEqual(json.loads((self.root/H.STATE).read_bytes())['reconciliation'],'changed')
        self.server_transcripts[fleet.OLD]=original
        self.assertEqual(H.retained_source_changes(self.r,journal),set())
        self.assertEqual(json.loads((self.root/H.STATE).read_bytes())['reconciliation'],'current')

    def test_retained_sdk_archive_change_cannot_hide_behind_same_root_and_ui_head(self):
        self.test_coordinator_supported_official_return_registers_publishes_resumes_and_activates()
        journal=self.r.read();entry=journal['entries'][0]
        target=self.root/'agents'/entry['newId'];current={p:p.read_bytes()for p in target.rglob('*')if p.is_file()}
        with contextlib.closing(sqlite3.connect(self.target/'conversation-blobs.db'))as db:
            db.execute('INSERT INTO blobs(id,data)VALUES(?,?)',('retained-new-archive',b'inert new checkpoint archive'));db.commit()
        self.builder.validator.source_freshness(self.r,journal,entry,{})
        with self.assertRaisesRegex(fleet.M.ReturnHistoryRequired,'Retained source history changed'):
            self.r.reselect_active()
        self.assertEqual(self.r.return_source_changed_ids,{entry['newId']})
        self.assertEqual({p:p.read_bytes()for p in current},current)
        self.assertEqual(self.r.read(),journal)

    def test_retained_inventory_tracks_accepted_incremental_cutoff_without_editing_original(self):
        journal=self.builder.run();entry=journal['entries'][0];fresh=entry['newId']
        rpc=self.r.m.rpc
        def register_rpc(method,body):
            if method=='CreateGrokBotAgent':self.created=fleet.row(body['agentId'],'box','789')
            return rpc(method,body)
        self.r.m.rpc=register_rpc;self.r.register(entry['oldId'])
        inventory_path=pathlib.Path(entry['returnRetainedSources']['path']);before=inventory_path.read_bytes()
        later=message('later-before-activation',4,fleet.OLD,'bounded late literal');later.pop('agentId')
        self.server_transcripts[fleet.OLD]=cloud([self.first,self.late,later])
        repair=self.builder.tail.TailRepair(self.recovery(),self.fd,codec=self.append)
        repair.run()
        updated=self.r.read();active=updated['entries'][0]
        self.assertNotEqual(active['historyMergeReceipt'],entry['historyMergeReceipt'])
        self.assertEqual(H.retained_source_changes(self.r,updated),set())
        self.assertEqual(inventory_path.read_bytes(),before)
        self.assertFalse(json.loads(before)['futureMessagesMerged'])
        self.assertEqual(self.builder.validator.db_entries(self.root/'agents'/fresh/'store.db')[-1]['content'],later['content'])

    def test_live_local_running_or_unknown_manager_state_refuses_without_old_store_changes(self):
        idle = {'id': fleet.NEW, 'harness': 'box', 'isRunning': False, 'isRunningTurn': False}
        listings = [[], [dict(idle, isRunning=True)], [dict(idle, isRunningTurn=True)],
                    [{key: value for key, value in idle.items() if key != 'isRunning'}],
                    [{key: value for key, value in idle.items() if key != 'isRunningTurn'}],
                    [dict(idle, isRunning=0)], [dict(idle, isRunningTurn=None)],
                    [dict(idle, harness='unknown')], [idle, idle]]
        for listing in listings:
            with self.subTest(listing=listing):
                self.live_listing = listing
                before = self.protected(); journal = self.r.read()
                with self.assertRaisesRegex(RuntimeError, 'running or its live state is unknown'):
                    self.builder.run()
                self.assertEqual(self.r.read(), journal)
                self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
                self.assert_protected(before)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])

    def test_local_turn_starting_after_capture_stops_before_checkpoint_or_install(self):
        capture = self.builder.capture
        def becomes_running(*args):
            result = capture(*args)
            self.live_listing = [{'id': fleet.NEW, 'harness': 'box', 'isRunning': True, 'isRunningTurn': True}]
            return result
        before = self.protected(); journal = self.r.read()
        with mock.patch.object(self.builder, 'capture', side_effect=becomes_running):
            with self.assertRaisesRegex(RuntimeError, 'running or its live state is unknown'):
                self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assert_protected(before)

    def test_unverified_cloud_source_activity_never_installs_or_changes_authority(self):
        def unverified(identities):
            self.assertIn(fleet.OLD, identities)
            raise self.builder.activity_module.ActivityUnverified('Cloud source has no fresh idle snapshot')
        self.builder.activity = unverified
        before = self.protected(); journal = self.r.read()
        with self.assertRaisesRegex(self.builder.activity_module.ActivityUnverified, 'no fresh idle snapshot'):
            self.builder.run()
        self.assertEqual(self.r.read(), journal)
        self.assertEqual(set(p.name for p in (self.root / 'agents').iterdir()), {fleet.OLD, fleet.NEW})
        self.assertEqual(self.checkpoint_calls, []); self.assertEqual(self.append_calls, [])
        self.assert_protected(before)

    def test_dispatch_lock_is_required_before_evidence_or_status_writes(self):
        self.builder.lock_fd = None
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(RuntimeError, 'inherited dispatch lock'): self.builder.run()
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse((self.root / H.STATE).exists())

    def test_prepared_return_recovers_excluded_source_sql_before_resumed_install(self):
        before = self.protected(); previous = self.r.read()
        with mock.patch.object(self.builder, 'install', side_effect=InterruptedError('before hidden install')):
            with self.assertRaisesRegex(InterruptedError, 'before hidden install'): self.builder.run()
        self.assertEqual(self.r.read(), previous)
        state = json.loads((self.root / H.STATE).read_bytes())
        self.assertIn('preparedIntent', state)
        backup = pathlib.Path(previous['backup'])
        removed = {str(p): p.read_bytes() for p in backup.rglob('*')
                   if p.is_file() and p.name in ('store.db', 'conversation-blobs.db')}
        self.assertTrue(removed)
        for path in removed: pathlib.Path(path).unlink()
        retention = self.rec.module.helper('evidence-retention')
        _, metadata, _ = retention.locations(backup, state['preparedIntent'])
        required = {str(backup / item['path']) for item in json.loads(metadata.read_bytes())['files']}
        captured = {path: raw for path, raw in removed.items() if path in required}
        result = retention.recover_pending(previous, self.root, self.fd, self.rec.progress['jobId'])
        self.assertEqual(result['restored'], len(captured))
        self.assertEqual({path: pathlib.Path(path).read_bytes() for path in captured}, captured)
        journal = self.builder.run(); entry = journal['entries'][0]
        self.builder.validator.validate(self.r, journal, entry)
        self.assertEqual([v['content'] for v in self.builder.validator.db_entries(
            self.root / 'agents' / entry['newId'] / 'store.db')],
            ['original literal', 'Local advanced checkpoint', 'Official literal\nkeep exactly'])
        self.assert_protected(before)

    def test_sibling_generation_is_recovered_independently_and_prior_journal_json_stays_exact(self):
        retention = self.rec.module.helper('evidence-retention'); validator = self.builder.validator
        sibling = self.backup.parent / 'sibling-immutable'; sibling.mkdir(mode=0o700)
        source = sibling / 'source'; baseline = sibling / 'baseline'; source.mkdir(); baseline.mkdir()
        shutil.copyfile(self.source / 'store.db', source / 'store.db')
        shutil.copyfile(self.target / 'store.db', baseline / 'store.db')
        shutil.copyfile(self.target / 'conversation-blobs.db', baseline / 'conversation-blobs.db')
        with contextlib.closing(sqlite3.connect(source / 'conversation-blobs.db')) as db:
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)'); db.commit()
        with contextlib.closing(sqlite3.connect(baseline / 'conversation-blobs.db')) as db:
            graph = {row[0]: H.sha(row[1]) for row in db.execute('SELECT id,data FROM blobs')}
        ref = self.builder.tail.reference
        self.write(sibling / 'native.json', {})
        self.write(sibling / 'sdk.json', {'schema': 'openbot-sdk-merge-proof/v1', 'engineSha256': 'a' * 64,
            'rootSlot': ROOT_SLOT, 'rootSha256': graph[ROOT_SLOT], 'blobs': graph,
            'sourceRoots': {fleet.OLD: None}, 'nativeReport': ref(sibling / 'native.json')})
        self.write(sibling / 'receipt.json', {'schema': validator.SCHEMA, 'oldId': fleet.OLD, 'newId': fleet.NEW,
            'selectionSourceId': fleet.OLD, 'backup': str(sibling),
            'baseline': {'store': ref(baseline / 'store.db'), 'blobs': ref(baseline / 'conversation-blobs.db')},
            'sources': [{'agentId': fleet.OLD, 'kind': 'sqlite', **ref(source / 'store.db'), 'generation': None,
                'sdkRootSha256': None, 'blobSnapshot': ref(source / 'conversation-blobs.db')}],
            'projections': [{'sourceId': fleet.OLD, 'sourceKind': 'sqlite', 'sourceEntryId': 'first', 'targetEntryId': 'first'}],
            'sourceOrders': [{'sourceId': fleet.OLD, 'sourceKind': 'sqlite', 'entryIds': ['first']}],
            'orderResolutions': [], 'sdkProof': ref(sibling / 'sdk.json')})
        generation = copy.deepcopy(self.r.read()); generation['backup'] = str(sibling)
        generation['entries'][0]['historyMergeReceipt'] = ref(sibling / 'receipt.json')
        validator.validate(self.r, generation, generation['entries'][0])
        retention.retain(sibling, generation['entries'][0]['historyMergeReceipt'])
        journal = self.r.read(); journal.setdefault('previousGenerations', []).append(generation); self.r.save(journal)
        previous = self.r.read(); exact = H.encoded(previous)
        with mock.patch.object(self.builder, 'install', side_effect=InterruptedError('before install')):
            with self.assertRaises(InterruptedError): self.builder.run()
        state = json.loads((self.root / H.STATE).read_bytes()); prepared = json.loads(pathlib.Path(state['preparedIntent']['path']).read_bytes())
        prior = prepared['journal']['officialReturnPreviousJournal']
        self.assertEqual(pathlib.Path(prior['path']).read_bytes(), exact)
        for root in (self.backup, sibling):
            for path in root.rglob('*'):
                if path.is_file() and path.name in ('store.db', 'conversation-blobs.db'): path.unlink()
        self.assertGreater(retention.recover_journal(previous, self.root, self.fd)['restored'], 0)
        validator.validate(self.r, generation, generation['entries'][0])
        self.assertGreater(retention.recover_pending(previous, self.root, self.fd, self.rec.progress['jobId'])['restored'], 0)
        self.assertEqual(pathlib.Path(prior['path']).read_bytes(), exact)
        proposed = self.builder.run(); validator.validate(self.r, proposed, proposed['entries'][0])

    def test_prepared_retention_failure_does_not_pin_resumable_intent_or_install(self):
        original = self.rec.module.helper; retention = original('evidence-retention'); before = self.protected(); journal = self.r.read()
        failed = types.SimpleNamespace(retain=retention.retain,
            retain_prepared=mock.Mock(side_effect=RuntimeError('retention unavailable')))
        def helper(name): return failed if name == 'evidence-retention' else original(name)
        with mock.patch.object(self.rec.module, 'helper', side_effect=helper), mock.patch.object(self.builder, 'install') as install:
            with self.assertRaisesRegex(RuntimeError, 'retention unavailable'): self.builder.run()
        install.assert_not_called(); self.assertEqual(self.r.read(), journal); self.assert_protected(before)
        self.assertNotIn('preparedIntent', json.loads((self.root / H.STATE).read_bytes()))

    def test_prepared_source_inventory_recovers_nested_session_sql_and_all_sdk_blobs(self):
        nested = self.target / 'sessions' / 'retained-session'; nested.mkdir(parents=True)
        with contextlib.closing(sqlite3.connect(nested / 'store.db')) as db:
            db.execute('CREATE TABLE session_entries(value TEXT)')
            db.execute('INSERT INTO session_entries VALUES(?)', ('original separate session context',)); db.commit()
        session_blobs = {'session-root': b'{"child":"historical-context"}', 'historical-context': b'exact old tool context'}
        with contextlib.closing(sqlite3.connect(nested / 'conversation-blobs.db')) as db:
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)')
            db.executemany('INSERT INTO blobs VALUES(?,?)', session_blobs.items()); db.commit()
        untouched = {path: path.read_bytes() for path in nested.iterdir()}
        previous = self.r.read()
        with mock.patch.object(self.builder, 'install', side_effect=InterruptedError('before install')):
            with self.assertRaises(InterruptedError): self.builder.run()
        for path in self.backup.rglob('*'):
            if path.is_file() and path.name in ('store.db', 'conversation-blobs.db'): path.unlink()
        retention = self.rec.module.helper('evidence-retention')
        self.assertGreater(retention.recover_pending(previous, self.root, self.fd, self.rec.progress['jobId'])['restored'], 0)
        proposed = self.builder.run(); entry = proposed['entries'][0]
        restored = self.root / 'agents' / entry['newId'] / 'sessions' / 'retained-session'
        with contextlib.closing(sqlite3.connect(restored / 'store.db')) as db:
            self.assertEqual(db.execute('SELECT value FROM session_entries').fetchall(), [('original separate session context',)])
        with contextlib.closing(sqlite3.connect(restored / 'conversation-blobs.db')) as db:
            self.assertEqual(dict(db.execute('SELECT id,data FROM blobs')), session_blobs)
        self.assertEqual({path: path.read_bytes() for path in untouched}, untouched)
        self.builder.validator.validate(self.r, proposed, entry)


if __name__ == '__main__':
    unittest.main()
