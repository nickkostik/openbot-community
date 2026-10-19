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
    rpc = fleet.RemintTests.rpc
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
                                     activity=lambda ids: {'freshIdle': True, 'syntheticBoundary': True})
        self.checkpoint_calls = []; self.append_calls = []
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
        self.assertFalse(state['liveApplied']); self.assertFalse(state['nativeAppDeliveryVerified'])
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

    def test_live_local_running_or_unknown_manager_state_refuses_without_old_store_changes(self):
        idle = {'id': fleet.NEW, 'harness': 'box', 'isRunning': False, 'isRunningTurn': False}
        listings = [[], [dict(idle, isRunning=True)], [dict(idle, isRunningTurn=True)],
                    [{key: value for key, value in idle.items() if key != 'isRunning'}],
                    [{key: value for key, value in idle.items() if key != 'isRunningTurn'}],
                    [dict(idle, isRunning=0)], [dict(idle, isRunningTurn=None)],
                    [dict(idle, harness='temporal')], [idle, idle]]
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


if __name__ == '__main__':
    unittest.main()
