import fcntl
import base64
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


M = load('local-upstream-sync')
ENGINE = load('local-engine')
REFRESH = load('refresh-local-engine')
RUNNER = ('  async sendPrompt(prompt, options2) {\n' + '    // native retained runner\n' * 200 +
          '    return this.tm.turnRuntime.runAgentTurn(prompt, options2);\n  }\n'
          '  nextTurnEpoch(session) {}\n  runServerScheduledAutomation(args) {}\n')


class UpstreamSyncTests(unittest.TestCase):
    def test_routine_writer_dependency_invalidates_cached_engine_assessment(self):
        original = pathlib.Path.read_bytes
        before = M.assessment_key(b'unchanged-policy', 'a' * 64)
        def revised_dependency(path):
            raw = original(path)
            return raw + b'\n# revised writer contract\n' if path.name == 'native-routine-trigger-preservation.py' else raw
        with mock.patch.object(pathlib.Path, 'read_bytes', revised_dependency):
            after = M.assessment_key(b'unchanged-policy', 'a' * 64)
        self.assertNotEqual(before, after)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name); folder = self.root / 'openbot-local-engine'; folder.mkdir()
        self.sources = {'stock': RUNNER.encode(), 'wrapped': ('/* openbot-stock-wrap */\n' + RUNNER).encode()}
        self.policy = {'schemaVersion': 1, 'enabled': True, 'clientVersion': '0.67.0',
                       **{k + 'Sha256': hashlib.sha256(v).hexdigest() for k, v in self.sources.items()}}
        self.path = self.root / 'openbot-local-engine.json'; self.path.write_text(json.dumps(self.policy))
        for kind, raw in self.sources.items(): (folder / (kind + '.cjs')).write_bytes(raw)
        for name in ('openbot-plan.json', 'openbot-bot-models.json', 'openbot-box-remint.json', 'openbot-local-routines.json'):
            (self.root / name).write_text('{"userOwned":"keep"}')
        (self.root / 'openbot-mode').write_text('custom')
        self.before = self.path.read_bytes()
        self.candidates = {kind: raw + b'// UPDATED INSTRUCTION\n' for kind, raw in self.sources.items()}

    def test_official_update_saves_complete_pair_without_selecting_or_touching_live_host(self):
        (self.root / 'openbot-mode').write_text('official')
        host = self.root / 'host.cjs'; host.write_text('STOCK LIVE ENGINE')
        (self.root / 'version').write_text('abcdef0\n')
        before = M.configuration_hashes(self.root)
        with mock.patch.object(M, 'gateway') as gateway:
            result = M.save_local_release(self.root, self.candidates, self.before,
                {'upstreamVersion': 'abcdef1', 'hostVersion': 'abcdef1'}, {'nativeBoxRunnerPresent': True})
        gateway.assert_not_called()
        self.assertEqual(result['phase'], 'local-release-ready')
        self.assertFalse(result['nativeDeliveryVerified']); self.assertTrue(result['activationDeferred'])
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(M.configuration_hashes(self.root), before)
        self.assertEqual(host.read_text(), 'STOCK LIVE ENGINE')
        self.assertEqual((self.root / 'version').read_text(), 'abcdef0\n')
        ready = M.owned_json(self.root / 'openbot-upstream-local-ready.json')
        saved = ENGINE.artifact_directory(self.root, ready['manifest'])
        for kind, raw in self.candidates.items():
            self.assertEqual((saved / (kind + '.cjs')).read_bytes(), raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), ready['manifest'][kind + 'Sha256'])

    def test_official_update_rejects_partial_release_or_a_concurrent_mode_change(self):
        for mode, report, message in [('official', {'nativeBoxRunnerPresent': False}, 'partial'),
                                      ('custom', {'nativeBoxRunnerPresent': True}, 'Mode changed')]:
            (self.root / 'openbot-mode').write_text(mode)
            with self.assertRaisesRegex(RuntimeError, message):
                M.save_local_release(self.root, self.candidates, self.before, {}, report)
            self.assertEqual(self.path.read_bytes(), self.before)
            self.assertFalse((self.root / 'openbot-upstream-local-ready.json').exists())

    def test_successive_latest_host_compositions_use_the_original_box_baseline(self):
        for _ in range(2):
            REFRESH.activate_pair(self.root, self.candidates, self.path.read_bytes(),
                {'upstreamSyncStrategy': 'latest-host-with-box-ports'})
        source, baseline = M.compatibility_base(self.root, json.loads(self.path.read_bytes()))
        self.assertEqual(source, self.root / 'openbot-local-engine/stock.cjs')
        self.assertEqual(baseline['stockSha256'], self.policy['stockSha256'])
        self.assertNotIn('generationId', baseline)
        (self.root / 'openbot-local-engine/stock.cjs').write_bytes(b'corrupted')
        with self.assertRaisesRegex(RuntimeError, 'baseline digest differs'):
            M.compatibility_base(self.root, json.loads(self.path.read_bytes()))

    def test_explicit_compatibility_baseline_cannot_escape_the_owned_generation_tree(self):
        bad = {**self.policy, 'generationId': '../outside'}
        with self.assertRaisesRegex(RuntimeError, 'baseline generation'):
            M.compatibility_base(self.root, bad)
        root_policy = {**self.policy, 'upstreamSyncStrategy': 'latest-host-with-box-ports',
            'boxCompatibilityBase': {**self.policy, 'generationId': '../outside'}}
        with self.assertRaisesRegex(RuntimeError, 'baseline generation'):
            M.compatibility_base(self.root, root_policy)

    def test_canary_accepts_both_transcript_formats_but_rejects_user_echoes_streams_and_stale_replies(self):
        old = {'timestampMs':101, 'message':{'type':'text','content':' marker\n'}}
        new = {'timestampMs':101, 'kind':'message','role':'assistant','isStreaming':False,'content':'marker'}
        self.assertTrue(M.is_delivered_canary(old, 100, 'marker'))
        self.assertTrue(M.is_delivered_canary(new, 100, 'marker'))
        for entry in [{**new,'role':'user'}, {**new,'isStreaming':True}, {**new,'timestampMs':99},
                      {**new,'content':'a guess'}]:
            self.assertFalse(M.is_delivered_canary(entry, 100, 'marker'))

    def test_successful_native_delivery_activates_complete_pair_and_preserves_all_user_settings(self):
        before_config = M.configuration_hashes(self.root)
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates,
            self.before, {'upstreamVersion': 'abcdef1'}, self.root,
            canary=lambda *_: {'accepted': True, 'delivered': True}, reload=lambda *_: {'reloaded': True})
        self.assertEqual(result['phase'], 'verified'); self.assertTrue(result['nativeDeliveryVerified'])
        self.assertEqual(M.configuration_hashes(self.root), before_config)
        self.assertEqual(json.loads(self.path.read_bytes())['upstreamVersion'], 'abcdef1')
        self.assertEqual(ENGINE.select_source(self.root, pathlib.Path('host-main.cjs'), ''), self.candidates['wrapped'].decode())
        self.assertEqual(M.trial(self.root)['phase'], 'verified')

    def test_interrupted_activation_rolls_back_on_the_next_run_even_if_updates_are_disabled(self):
        def interrupted(*_): raise KeyboardInterrupt('simulated process interruption')
        with self.assertRaises(KeyboardInterrupt):
            M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates, self.before, {},
                self.root, canary=lambda *_: {'delivered': True}, reload=interrupted)
        self.assertNotEqual(self.path.read_bytes(), self.before)
        self.assertEqual(M.trial(self.root)['phase'], 'activated')
        before_config = M.configuration_hashes(self.root)
        result = M.recover_interrupted_trial(self.root, self.root / 'host.cjs', reload=lambda *_: {'reloaded': True})
        self.assertTrue(result['rolledBack']); self.assertTrue(result['interruptedUpdateRecovered'])
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(M.configuration_hashes(self.root), before_config)
        self.assertEqual(M.trial(self.root)['phase'], 'rolled-back')

    def test_verified_trial_survives_an_interruption_before_the_summary_write(self):
        M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates, self.before,
            {'upstreamSyncStrategy': 'instruction-backport'}, self.root,
            canary=lambda *_: {'accepted': True, 'delivered': True}, reload=lambda *_: {'reloaded': True})
        selected = self.path.read_bytes(); reload = mock.Mock()
        result = M.recover_interrupted_trial(self.root, self.root / 'host.cjs', reload=reload)
        self.assertEqual(result['phase'], 'verified'); self.assertEqual(result['featureParity'], 'partial')
        self.assertEqual(self.path.read_bytes(), selected); reload.assert_not_called()
        self.assertEqual(M.trial(self.root)['phase'], 'completed')

    def test_recovering_an_interrupted_trial_respects_a_later_authority_edit(self):
        def interrupted(*_): raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates, self.before, {},
                self.root, reload=interrupted)
        d = json.loads(self.path.read_bytes()); d['ownerEdited'] = True; self.path.write_text(json.dumps(d))
        later = self.path.read_bytes()
        result = M.recover_interrupted_trial(self.root, self.root / 'host.cjs', reload=mock.Mock())
        self.assertFalse(result['rolledBack']); self.assertTrue(result['engineAuthorityChangedConcurrently'])
        self.assertEqual(self.path.read_bytes(), later)

    def test_rollback_defers_a_busy_reload_instead_of_killing_work(self):
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates, self.before, {},
            self.root, canary=lambda *_: {'delivered': False}, reload=lambda *_: {'deferred': True})
        self.assertTrue(result['rolledBack']); self.assertTrue(result['reloadDeferred'])
        self.assertTrue(json.loads((self.root / 'computer-recovery-state.json').read_text())['pendingHostReload'])

    def test_cached_assessment_is_digest_checked_and_does_not_parse_again(self):
        folder = self.root / 'vendor'; folder.mkdir(); directory = self.root / 'openbot-local-engine'
        vendor = {'sourceSha256': 'a' * 64}
        key = M.assessment_key(self.before, vendor['sourceSha256'])
        attempt = folder / ('assessment-' + key); attempt.mkdir()
        for kind, raw in self.candidates.items(): (attempt / ('candidate-' + kind + '.cjs')).write_bytes(raw)
        report = {'cacheKey': key, 'upstreamSourceSha256': vendor['sourceSha256'], 'phase': 'assessed',
                  'candidateHashes': {k: hashlib.sha256(v).hexdigest() for k, v in self.candidates.items()}}
        (attempt / 'assessment.json').write_text(json.dumps(report))
        with mock.patch.object(M, 'inherit') as parse:
            _, candidates, _ = M.assess(self.root, folder, directory, self.policy, self.sources, self.before, vendor, '/node')
            self.assertEqual(candidates, self.candidates); parse.assert_not_called()
            (attempt / 'candidate-stock.cjs').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'digest differs'):
                M.assess(self.root, folder, directory, self.policy, self.sources, self.before, vendor, '/node')
            parse.assert_not_called()

    def test_only_an_exact_reviewed_vendor_identity_can_use_box_composition(self):
        registry = json.loads((HERE / 'latest-box-compatibility.json').read_text())
        digest, recipe = next(iter(registry['reviewedSources'].items()))
        vendor = {**recipe, 'sourceSha256': digest}
        self.assertEqual(M.compatibility_recipe(vendor), recipe)
        self.assertIsNone(M.compatibility_recipe({**vendor, 'sourceSha256': 'a' * 64}))
        for key in ('version', 'clientVersion'):
            with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                M.compatibility_recipe({**vendor, key: 'changed'})

    def test_composition_rejects_failed_or_incomplete_native_offline_contracts(self):
        for outcome in [types.SimpleNamespace(returncode=1, stdout=b''),
                        types.SimpleNamespace(returncode=0, stdout=b'{"loaded":true}')]:
            output = self.root / ('compose-' + str(outcome.returncode)); output.mkdir()
            source = b'const fixture = true;'
            (output / 'candidate.cjs').write_bytes(source)
            (output / 'compatibility.json').write_text(json.dumps({'candidateSha256':hashlib.sha256(source).hexdigest()}))
            with mock.patch.object(M.subprocess, 'run', side_effect=[types.SimpleNamespace(returncode=0), outcome]):
                with self.assertRaisesRegex(RuntimeError, 'offline contract'):
                    M.compose(self.root, self.path, self.path, output, '/node')
            self.assertEqual(self.path.read_bytes(), self.before)

    def test_reviewed_retired_host_gets_latest_composition_and_both_canonical_sources(self):
        folder = self.root / 'vendor'; folder.mkdir()
        (folder / 'host-main.cjs').write_text('const retired = true;')
        registry = json.loads((HERE / 'latest-box-compatibility.json').read_text())
        digest, recipe = next(iter(registry['reviewedSources'].items()))
        vendor = {**recipe, 'sourceSha256': digest}
        inventory = {'originalSha256': self.policy['stockSha256'], 'upstreamSha256': digest,
                     'applied': [], 'pending': [{'reason': 'module-code-changed'}], 'upstreamSections': ['changed']}
        composed = {'retainedSha256': self.policy['stockSha256'], 'upstreamSha256': digest,
                    'retainedModules': ['native-runner'], 'inheritedBindings': ['latest-instructions']}
        with mock.patch.object(M, 'syntax'), mock.patch.object(M, 'inherit', return_value=(b'', inventory)) as inherit, \
             mock.patch.object(M, 'compose', return_value=(self.sources['stock'], composed)) as compose, \
             mock.patch.object(M, 'wrap', return_value=self.sources['wrapped'].decode()), \
             mock.patch.object(M, 'load', side_effect=lambda name: mock.Mock(patched=lambda s: s,
                               PATCHES=load('computer-recovery').PATCHES)
                               if name == 'computer-recovery' else load(name)):
            _, candidates, report = M.assess(self.root, folder, self.root / 'openbot-local-engine',
                self.policy, self.sources, self.before, vendor, '/node')
        self.assertEqual(candidates, self.sources); compose.assert_called_once(); inherit.assert_called_once()
        self.assertEqual(report['strategy'], 'latest-host-with-box-ports')
        self.assertTrue(report['nativeBoxRunnerPresent']); self.assertFalse(report['nativeDeliveryVerified'])
        self.assertFalse(report['vendorNativeBoxRunnerPresent']); self.assertTrue(report['candidateNativeBoxRunnerPresent'])
        self.assertEqual(report['retainedCompatibilityModules'], 1)
        self.assertIsNone(report['pendingInstructionCount']); self.assertIsNone(report['pendingSourceSections'])
        self.assertFalse(report['fullFeatureParityVerified'])
        self.assertEqual(report['retainedBackportPendingInstructionCount'], 1)

    def test_a_stale_large_log_is_rotated_without_reading_it_into_memory(self):
        (self.root / 'openbot-upstream-sync.json').write_text('{"schemaVersion":1,"enabled":true}')
        log = self.root / 'openbot-upstream-sync.log'
        with log.open('wb') as handle: handle.truncate(5 * 1024 * 1024)
        with mock.patch.object(M.subprocess, 'Popen', return_value=mock.Mock(pid=42)):
            result = M.maybe_start(self.root)
        self.assertEqual(result['state'], 'started')
        self.assertEqual((self.root / 'openbot-upstream-sync.previous.log').stat().st_size, 5 * 1024 * 1024)
        self.assertEqual(log.stat().st_size, 0)

    def test_release_retention_preserves_the_selected_version_and_latest_captures(self):
        base = self.root / 'openbot-upstream/releases'; base.mkdir(parents=True)
        versions = ['abcdef' + str(i) for i in range(7)]
        for i, version in enumerate(versions):
            folder = base / version; folder.mkdir()
            (folder / 'vendor.json').write_text(json.dumps({'version': version, 'capturedAtMs': i}))
        self.path.write_text(json.dumps({**self.policy, 'upstreamVersion': versions[0]}))
        removed = M.prune_releases(self.root, versions[-1], keep=4)
        self.assertEqual(set(removed), set(versions[1:3])); self.assertTrue((base / versions[0]).exists())
        self.assertTrue(all((base / v).exists() for v in versions[3:]))

    def test_release_retention_refuses_symlinked_directories(self):
        base = self.root / 'openbot-upstream/releases'; base.mkdir(parents=True)
        (base / 'abcdef1').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Untrusted'):
            M.prune_releases(self.root, 'abcdef1')

    def test_prepare_callback_is_durable_before_the_manifest_becomes_live(self):
        seen = []
        def prepare(selected):
            self.assertEqual(self.path.read_bytes(), self.before)
            d = json.loads(selected); seen.append(d['generationId'])
            folder = self.root / 'openbot-local-engine/generations' / d['generationId']
            self.assertEqual((folder / 'stock.cjs').read_bytes(), self.candidates['stock'])
            raise RuntimeError('Journal write failed')
        with self.assertRaisesRegex(RuntimeError, 'Journal write failed'):
            REFRESH.activate_pair(self.root, self.candidates, self.before, before_activate=prepare)
        self.assertEqual(self.path.read_bytes(), self.before); self.assertEqual(len(seen), 1)

    def test_http_acceptance_without_delivered_reply_rolls_back_only_the_engine(self):
        before_config = M.configuration_hashes(self.root); reloads = []
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates,
            self.before, {}, self.root, canary=lambda *_: {'accepted': True, 'delivered': False},
            reload=lambda *_: reloads.append(True) or {'reloaded': True})
        self.assertEqual(result['phase'], 'verification-failed'); self.assertTrue(result['rolledBack'])
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(M.configuration_hashes(self.root), before_config); self.assertEqual(len(reloads), 2)

    def test_rolled_back_verification_failure_retries_the_same_release_after_the_poll(self):
        (self.root / 'openbot-upstream-sync.json').write_text('{"schemaVersion":1,"enabled":true}')
        progress = {'phase': 'verification-failed', 'upstreamVersion': 'abcdef1',
                    'baseEngineSourceHashes': {k: self.policy[k + 'Sha256'] for k in self.sources},
                    'rolledBack': True, 'nativeDeliveryVerified': False}
        (self.root / 'openbot-upstream-sync-state.json').write_text(json.dumps(progress))
        with mock.patch.object(M, 'vendor_origin', return_value=('https://vendor.example', 'host')), \
             mock.patch.object(M, 'fetch_text', return_value='abcdef1'), \
             mock.patch.object(M, 'stage_vendor', return_value=(self.root, {
                 'clientVersion': '0.69.0', 'sourceSha256': 'a' * 64})) as capture, \
             mock.patch.object(M, 'prune_releases'), \
             mock.patch.object(M, 'gateway', return_value={'isBusy': True}):
            result = M.sync(self.root, self.root / 'host.cjs', apply=True)
        capture.assert_called_once()
        self.assertEqual(result['phase'], 'waiting-for-idle')
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_a_failed_trial_still_waits_for_the_poll_before_retrying(self):
        (self.root / 'openbot-upstream-sync.json').write_text('{"schemaVersion":1,"enabled":true}')
        now = int(M.time.time() * 1000)
        (self.root / 'openbot-upstream-sync-state.json').write_text(json.dumps({
            'phase': 'verification-failed', 'upstreamVersion': 'abcdef1',
            'nextCheckAtMs': now + 1800000}))
        with mock.patch.object(M.subprocess, 'Popen') as spawn:
            result = M.maybe_start(self.root)
        spawn.assert_not_called()
        self.assertEqual(result['state'], 'verification-failed')

    def test_new_release_cannot_reuse_previous_delivery_or_candidate_evidence(self):
        (self.root / 'openbot-upstream-sync.json').write_text('{"schemaVersion":1,"enabled":true}')
        old = {'phase': 'verified', 'upstreamVersion': 'abcdef0',
               'nativeDeliveryVerified': True, 'nativeCanary': {'delivered': True},
               'configurationUnchanged': True, 'generationId': 'a' * 32,
               'localEngineSaved': True, 'activationDeferred': True,
               'compatibilityReport': 'old-source/report.json', 'errorType': 'OldError',
               'rolledBack': True, 'finishedAtMs': 100, 'lastAppliedAtMs': 90}
        (self.root / 'openbot-upstream-sync-state.json').write_text(json.dumps(old))
        with mock.patch.object(M, 'vendor_origin', return_value=('https://vendor.example', 'host')), \
             mock.patch.object(M, 'fetch_text', return_value='abcdef1'), \
             mock.patch.object(M, 'stage_vendor', return_value=(self.root, {
                 'clientVersion': '0.71.0', 'sourceSha256': 'b' * 64})), \
             mock.patch.object(M, 'prune_releases'), \
             mock.patch.object(M, 'gateway', return_value={'isBusy': True}):
            result = M.sync(self.root, self.root / 'host.cjs', apply=True)
        self.assertEqual(result['phase'], 'waiting-for-idle')
        self.assertEqual(result['upstreamVersion'], 'abcdef1')
        self.assertFalse(result['nativeDeliveryVerified'])
        self.assertEqual(result['lastAppliedAtMs'], 90)
        for field in ['nativeCanary', 'configurationUnchanged', 'generationId',
                      'localEngineSaved', 'activationDeferred', 'compatibilityReport',
                      'errorType', 'rolledBack', 'finishedAtMs']:
            self.assertNotIn(field, result)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_busy_reload_is_not_followed_by_a_probe_or_a_forced_restart(self):
        probe = mock.Mock()
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates,
            self.before, {}, self.root, canary=probe, reload=lambda *_: {'deferred': True})
        probe.assert_not_called(); self.assertTrue(result['rolledBack'])
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_checkpoint_reload_requires_upgrade_ownership_and_no_outstanding_work(self):
        for owner, quiet, ready in [('upgrade', True, True), ('upgrade', False, False),
                                   ('recreate', True, False), (None, True, False)]:
            with mock.patch.object(M, 'gateway', side_effect=[{'isBusy': True}, {'owner': owner, 'quiescent': quiet}]):
                self.assertEqual(M.reload_ready(self.root, allow_quiescent=True), ready)
        with mock.patch.object(M, 'gateway', return_value={'isBusy': True}) as gateway:
            self.assertFalse(M.reload_ready(self.root)); self.assertEqual(gateway.call_count, 1)

    def test_latest_composition_reload_waits_for_native_startup_not_just_the_gateway(self):
        host = self.root / 'host-main.cjs'; (self.root / 'version').write_text('abcdef1\n')
        trace = self.root / 'openbot-upstream-startup.json'
        trace.write_text(json.dumps({'phase': 'starting', 'pid': 456}))
        pids = mock.Mock(side_effect=[[123], [123], [456], [456]])
        recovery = types.SimpleNamespace(repair_bundle=lambda *_: True, host_pids=pids,
            atomic_bytes=lambda path, raw, *_: path.write_bytes(raw))
        engine = types.SimpleNamespace(policy=lambda _: {'hostVersion': 'abcdef1',
            'upstreamSyncStrategy': 'latest-host-with-box-ports'}, owned_bytes=lambda p, _: p.read_bytes())
        def sleep(_): trace.write_text(json.dumps({'phase': 'ready', 'pid': 456}))
        with mock.patch.object(M, 'load', side_effect=lambda n: recovery if n == 'computer-recovery' else engine), \
                mock.patch.object(M, 'reload_ready', return_value=True), mock.patch.object(M.os, 'kill') as kill, \
                mock.patch.object(M, 'gateway', return_value={'hostVersion': 'abcdef1'}), \
                mock.patch.object(M.time, 'sleep', side_effect=sleep) as wait:
            self.assertTrue(M.reload_selected(self.root, host)['reloaded'])
        wait.assert_called_once(); kill.assert_called_once_with(123, M.signal.SIGTERM)

    def test_failed_native_startup_rejects_a_composition_even_when_its_gateway_is_up(self):
        host = self.root / 'host-main.cjs'; (self.root / 'version').write_text('abcdef1\n')
        (self.root / 'openbot-upstream-startup.json').write_text(json.dumps({'phase': 'failed', 'pid': 456}))
        recovery = types.SimpleNamespace(repair_bundle=lambda *_: True,
            host_pids=mock.Mock(side_effect=[[123], [123], [456]]))
        engine = types.SimpleNamespace(policy=lambda _: {'hostVersion': 'abcdef1',
            'upstreamSyncStrategy': 'latest-host-with-box-ports'}, owned_bytes=lambda p, _: p.read_bytes())
        with mock.patch.object(M, 'load', side_effect=lambda n: recovery if n == 'computer-recovery' else engine), \
                mock.patch.object(M, 'reload_ready', return_value=True), mock.patch.object(M.os, 'kill'), \
                mock.patch.object(M, 'gateway', return_value={'hostVersion': 'abcdef1'}) as gateway:
            with self.assertRaisesRegex(RuntimeError, 'startup contract'):
                M.reload_selected(self.root, host)
        gateway.assert_not_called()

    def test_checkpoint_refreshes_the_native_pause_sweep_until_work_is_quiescent(self):
        clock = [0]; prepare = mock.Mock(return_value={'quiescing': True, 'runningTurns': 1})
        def sleep(_): clock[0] += 4
        with mock.patch.object(M, 'gateway', side_effect=[{'isBusy': True},
                {'owner': 'upgrade', 'quiescent': False}, {'owner': 'upgrade', 'quiescent': False},
                {'owner': 'upgrade', 'quiescent': True}]), \
             mock.patch.object(M.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(M.time, 'sleep', side_effect=sleep):
            M.checkpoint_for_reload(self.root, timeout=20, prepare=prepare)
        self.assertEqual(prepare.call_count, 2)

    def test_an_idle_host_needs_no_checkpoint_and_a_declined_checkpoint_never_reloads(self):
        prepare = mock.Mock(return_value={'quiescing': False})
        with mock.patch.object(M, 'gateway', return_value={'isBusy': False}):
            M.checkpoint_for_reload(self.root, prepare=prepare)
        prepare.assert_not_called()
        with mock.patch.object(M, 'gateway', return_value={'isBusy': True}):
            with self.assertRaisesRegex(RuntimeError, 'declined'):
                M.checkpoint_for_reload(self.root, prepare=prepare)

    def test_checkpoint_deadline_and_changed_ownership_do_not_count_as_quiescence(self):
        clock = [0]
        def sleep(_): clock[0] += 5
        with mock.patch.object(M, 'gateway', side_effect=lambda root, name:
                {'isBusy': True} if name == 'getHostStatus' else {'owner': 'recreate', 'quiescent': True}), \
             mock.patch.object(M.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(M.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError, 'did not become quiescent'):
                M.checkpoint_for_reload(self.root, timeout=10,
                    prepare=lambda root: {'quiescing': True})

    def test_failed_canary_cannot_undo_another_owners_new_engine_authority(self):
        def concurrent(*_):
            d = json.loads(self.path.read_text()); d['ownerEdited'] = True; self.path.write_text(json.dumps(d))
            raise RuntimeError('interrupted')
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates,
            self.before, {}, self.root, canary=concurrent, reload=lambda *_: {'reloaded': True})
        self.assertFalse(result['rolledBack']); self.assertTrue(result['engineAuthorityChangedConcurrently'])
        self.assertTrue(json.loads(self.path.read_bytes())['ownerEdited'])

    def test_concurrent_model_edit_remains_saved_even_when_the_trial_rolls_back(self):
        def changed_model(*_):
            (self.root / 'openbot-bot-models.json').write_text('{"model":"new-user-choice"}')
            return {'delivered': True}
        result = M.activate_and_verify(self.root, self.root / 'host.cjs', self.candidates,
            self.before, {}, self.root, canary=changed_model, reload=lambda *_: {'reloaded': True})
        self.assertTrue(result['rolledBack'])
        self.assertEqual(json.loads((self.root / 'openbot-bot-models.json').read_text())['model'], 'new-user-choice')

    def test_official_mode_never_sends_a_native_update_canary(self):
        (self.root / 'openbot-mode').write_text('official')
        with mock.patch.object(M, 'gateway') as gateway:
            with self.assertRaisesRegex(RuntimeError, 'Custom mode'):
                M.native_canary(self.root, self.root, fixture_root=self.root / 'accessible')
            gateway.assert_not_called()

    def test_mode_change_during_roster_read_refuses_the_send(self):
        aid = '00000000-0000-4000-8000-000000000001'
        (self.root / 'openbot-box-remint.json').write_text(json.dumps({'entries': [{'name': 'Q', 'newId': aid}]}))
        def rpc(root, method, *_):
            self.assertEqual(method, 'listAgents')
            (self.root / 'openbot-mode').write_text('official')
            return [{'id': aid, 'harness': 'box', 'isRunning': False}]
        with mock.patch.object(M, 'gateway', side_effect=rpc) as gateway:
            with self.assertRaisesRegex(RuntimeError, 'Mode changed'):
                M.native_canary(self.root, self.root)
            self.assertEqual(gateway.call_count, 1)

    def test_native_canary_requires_both_timestamp_and_unpredictable_file_content(self):
        aid = '00000000-0000-4000-8000-000000000001'
        (self.root / 'openbot-box-remint.json').write_text(json.dumps({'entries': [{'name': 'Q', 'newId': aid}]}))
        def rpc(root, method, body=None):
            if method == 'listAgents': return [{'id': aid, 'harness': 'box', 'isRunning': False}]
            if method == 'sendPrompt':
                fixture = pathlib.Path(json.loads((self.root / 'native-canary.json').read_text())['fixturePath'])
                self.assertNotIn(fixture.read_text().strip(), body['prompt'])
                self.assertNotIn('recovery-snapshots', str(fixture))
                return {'accepted': True}
            if method == 'getAgentTranscriptTail':
                fixture = pathlib.Path(json.loads((self.root / 'native-canary.json').read_text())['fixturePath'])
                marker = fixture.read_text().strip()
                return {'entries': [{'id': 'fresh-response', 'timestampMs': int(M.time.time() * 1000),
                    'message': {'type': 'text', 'content': marker}}]}
            self.fail(method)
        with mock.patch.object(M, 'gateway', side_effect=rpc):
            proof = M.native_canary(self.root, self.root, max_seconds=1, fixture_root=self.root / 'accessible')
        self.assertTrue(proof['delivered']); self.assertEqual(proof['replyEntryId'], 'fresh-response')

    def test_accepted_canary_execution_failure_rolls_back_without_waiting_for_timeout(self):
        aid = '00000000-0000-4000-8000-000000000001'
        (self.root / 'openbot-box-remint.json').write_text(json.dumps({'entries': [{'name': 'Q', 'newId': aid}]}))
        (self.root / 'openbot-upstream-startup.json').write_text(json.dumps({'pid': 9, 'phase': 'ready'}))
        def rpc(root, method, body=None):
            if method == 'listAgents': return [{'id': aid, 'harness': 'box', 'isRunning': False}]
            if method == 'sendPrompt': return {'accepted': True}
            if method == 'getAgentTranscriptTail':
                (self.root / ('openbot-upstream-turn-' + aid + '.json')).write_text(json.dumps(
                    {'phase': 'failed', 'pid': 9, 'atMs': int(M.time.time() * 1000), 'errorClass': 'TypeError'}))
                return {'entries': []}
            self.fail(method)
        with mock.patch.object(M, 'gateway', side_effect=rpc), mock.patch.object(M.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'failed during execution'):
                M.native_canary(self.root, self.root, fixture_root=self.root / 'accessible')
            sleep.assert_not_called()
        self.assertEqual(json.loads((self.root / 'native-canary-turn-error.json').read_text())['errorClass'], 'TypeError')

    def test_checkpointed_canary_agent_finishes_its_resumed_turn_before_a_test_send(self):
        aid = '00000000-0000-4000-8000-000000000001'
        (self.root / 'openbot-box-remint.json').write_text(json.dumps({'entries': [{'name': 'Q', 'newId': aid}]}))
        reads = 0
        def rpc(root, method, body=None):
            nonlocal reads
            if method == 'listAgents':
                reads += 1
                return [{'id': aid, 'harness': 'box', 'isRunning': reads == 1}]
            if method == 'sendPrompt':
                self.assertEqual(reads, 2)
                return {'accepted': True}
            if method == 'getAgentTranscriptTail':
                fixture = pathlib.Path(json.loads((self.root / 'native-canary.json').read_text())['fixturePath'])
                return {'entries': [{'id': 'reply', 'timestampMs': int(M.time.time() * 1000),
                    'kind': 'message', 'role': 'assistant', 'isStreaming': False, 'content': fixture.read_text().strip()}]}
            self.fail(method)
        with mock.patch.object(M, 'gateway', side_effect=rpc), mock.patch.object(M.time, 'sleep') as sleep:
            proof = M.native_canary(self.root, self.root, fixture_root=self.root / 'accessible')
            sleep.assert_called_once()
        self.assertTrue(proof['delivered'])

    def test_archive_paths_and_links_are_never_extracted(self):
        archive = self.root / 'vendor.tgz'
        with tarfile.open(archive, 'w:gz') as tar:
            raw = b'VENDOR SOURCE'; info = tarfile.TarInfo('../../elsewhere/host-main.cjs'); info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
            link = tarfile.TarInfo('../../outside'); link.type = tarfile.SYMTYPE; link.linkname = '/secret'; tar.addfile(link)
        destination = self.root / 'source.cjs'
        digest = M.unpack_host(archive, destination)
        self.assertEqual(destination.read_bytes(), b'VENDOR SOURCE')
        self.assertEqual(digest, hashlib.sha256(b'VENDOR SOURCE').hexdigest())
        self.assertFalse((self.root / 'outside').exists())

    def test_duplicate_or_symlinked_host_sources_are_refused(self):
        for symlink in (False, True):
            archive = self.root / ('vendor-link.tgz' if symlink else 'vendor-duplicate.tgz')
            with tarfile.open(archive, 'w:gz') as tar:
                for i in range(1 if symlink else 2):
                    info = tarfile.TarInfo(str(i) + '/host-main.cjs')
                    if symlink: info.type = tarfile.SYMTYPE; info.linkname = '/secret'; tar.addfile(info)
                    else: info.size = 3; tar.addfile(info, io.BytesIO(b'var'))
            with self.assertRaisesRegex(RuntimeError, 'bounded regular'):
                M.unpack_host(archive, self.root / 'source.cjs')

    def test_explicit_disable_and_invalid_or_symlinked_policy_start_nothing(self):
        with mock.patch.object(M.subprocess, 'Popen') as popen:
            self.assertEqual(M.maybe_start(self.root)['state'], 'disabled')
            policy = self.root / 'openbot-upstream-sync.json'
            policy.write_text('{"schemaVersion":1,"enabled":false}')
            self.assertEqual(M.maybe_start(self.root)['state'], 'disabled')
            policy.write_text('{"schemaVersion":1,"enabled":true,"pollSeconds":0}')
            with self.assertRaises(RuntimeError): M.maybe_start(self.root)
            policy.unlink(); policy.symlink_to(self.path)
            with self.assertRaises(OSError): M.maybe_start(self.root)
            popen.assert_not_called()

    def test_recovery_tick_releases_its_mutation_lock_for_a_later_upgrade(self):
        with M.mutation_lock(self.root):
            with self.assertRaises(BlockingIOError):
                with M.mutation_lock(self.root): pass
        with M.mutation_lock(self.root): pass

    def test_only_literal_vendor_bucket_region_prefix_and_version_are_accepted(self):
        valid = 'SAND_HOST_BUNDLE_S3_BUCKET = "bucket"; SAND_HOST_BUNDLE_S3_REGION = "us-east-1"; SAND_HOST_BUNDLE_S3_PREFIX = "sand-host";'
        self.assertEqual(M.vendor_origin(valid), ('https://bucket.s3.us-east-1.amazonaws.com/sand-host', 'sand-host'))
        for source in (valid.replace('"bucket"', '`evil/${token}`'), valid + valid):
            with self.assertRaises(RuntimeError): M.vendor_origin(source)
        with self.assertRaises(RuntimeError): M.stage_vendor(self.root, '../elsewhere', 'https://invalid', 'host')

    def test_shared_vendor_origin_layout_preserves_the_exact_bucket_path(self):
        source = self.shared_vendor_origin()
        self.assertEqual(M.vendor_origin(source),
            ('https://public-bucket.s3.us-east-1.amazonaws.com/sand-host', 'sand-host'))
        self.assertEqual(M.vendor_origin(source.replace(' = ', '=\n  ')),
                         M.vendor_origin(source))

    @staticmethod
    def shared_vendor_origin():
        return ('PUBLIC_VM_DAEMON_BUCKET = "public-bucket";\n'
                'PUBLIC_VM_DAEMON_BUCKET_REGION = "us-east-1";\n'
                'PUBLIC_VM_DAEMON_BUCKET_ORIGIN = `https://${PUBLIC_VM_DAEMON_BUCKET}.s3.${PUBLIC_VM_DAEMON_BUCKET_REGION}.amazonaws.com`;\n'
                'SAND_HOST_BUNDLE_S3_PREFIX = "sand-host";\n'
                'SAND_HOST_BUNDLE_PUBLIC_ORIGIN = PUBLIC_VM_DAEMON_BUCKET_ORIGIN;\n'
                'SAND_HOST_BUNDLE_PUBLIC_BASE_URL = `${SAND_HOST_BUNDLE_PUBLIC_ORIGIN}/${SAND_HOST_BUNDLE_S3_PREFIX}`;\n')

    def test_shared_vendor_origin_rejects_partial_dynamic_duplicated_or_mixed_chains(self):
        valid = self.shared_vendor_origin()
        changes = [
            valid + valid,
            valid + 'PUBLIC_VM_DAEMON_BUCKET = unknown;',
            valid + 'SAND_HOST_BUNDLE_PUBLIC_ORIGIN = alternate;',
            valid + 'SAND_HOST_BUNDLE_S3_BUCKET = "legacy";',
            valid.replace('"public-bucket"', '`public/${token}`'),
            valid.replace('"us-east-1"', 'getRegion()'),
            valid.replace('https://', 'http://'),
            valid.replace('.amazonaws.com`', '.amazonaws.com.evil.example`'),
            valid.replace('SAND_HOST_BUNDLE_PUBLIC_ORIGIN = PUBLIC_VM_DAEMON_BUCKET_ORIGIN;', ''),
            valid.replace('SAND_HOST_BUNDLE_PUBLIC_ORIGIN = PUBLIC_VM_DAEMON_BUCKET_ORIGIN;',
                          'SAND_HOST_BUNDLE_PUBLIC_ORIGIN = "https://other.example";'),
            valid.replace('${SAND_HOST_BUNDLE_PUBLIC_ORIGIN}/', '${other}/'),
            valid.replace('"sand-host"', '"../elsewhere"'),
        ]
        for source in changes:
            with self.subTest(source=source):
                with self.assertRaises(RuntimeError): M.vendor_origin(source)

    def test_data_backports_cannot_claim_native_version_or_executable_feature_parity(self):
        body = 'function createProtoSessionProvider(a) {return a;}\n'
        retained = ('/* openbot-stock-wrap */\n/* openbot-payload 1234567890abcdef */\n'
                    'var __openbotRuntime = require("/payload/runtime.cjs");\n'
                    'function createProtoSessionProvider() {\n'
                    '  return __openbotRuntime.attachSession(createProtoSessionProvider_stock, arguments);\n}\n' +
                    body.replace('createProtoSessionProvider(', 'createProtoSessionProvider_stock('))
        wrapped = M.wrap(body, retained)
        self.assertIn('createProtoSessionProvider_stock(a)', wrapped)
        self.assertEqual(wrapped.count('/* openbot-stock-wrap */'), 1)
        with self.assertRaises(RuntimeError): M.wrap(body + body, retained)


if __name__ == '__main__':
    unittest.main()
