import hashlib
import importlib.util
import json
import pathlib
import stat
import tempfile
import types
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('prepare_engine', HERE / 'prepare-local-engine.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
E = M.load('local-engine')
S = M.load('local-upstream-sync')
R = M.load('computer-recovery')
RUNNER = ('  async sendPrompt(prompt, options2) {\n' + '    // native runner\n' * 250 +
          '    return this.tm.turnRuntime.runAgentTurn(prompt, options2);\n  }\n'
          '  nextTurnEpoch(session) {}\n  runServerScheduledAutomation(args) {}\n')


class PrepareLocalEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.stage = self.root / 'preparation'
        self.package = self.root / 'openbot'
        self.package.mkdir()
        (self.package / 'package.json').write_text('{}')
        self.host = self.root / 'host.cjs'
        self.host.write_text('existing native host')
        self.live = {self.host: self.host.read_bytes()}
        for name in ('openbot-local-engine.json', 'openbot-mode', 'openbot-plan.json',
                     'openbot-bot-models.json', 'openbot-local-routines.json'):
            p = self.root / name
            p.write_text('owner state: ' + name)
            self.live[p] = p.read_bytes()
        self.baseline = {**M.BASELINE, 'sourceSha256': hashlib.sha256(RUNNER.encode()).hexdigest()}
        self.vendor = {'version': 'abcdef1', 'sourceSha256': 'a' * 64, 'clientVersion': 'reviewed'}
        self.sync = mock.Mock(wraps=S)
        self.recovery = mock.Mock(wraps=R)
        self.recovery.patched.side_effect = lambda source: source if '// canonical\n' in source else '// canonical\n' + source
        self.sync.compatibility_recipe.return_value = {'strategy': 'latest-host-with-box-ports'}
        self.sync.stage_vendor.side_effect = self.stage_vendor
        self.sync.compose.side_effect = self.compose
        self.sync.syntax.return_value = None
        self.check = {'loaded': True, 'latestDatabasePersistence': True}
        self.native_failed = False
        self.loader = mock.patch.object(M, 'load', side_effect=lambda name: {
            'local-upstream-sync': self.sync, 'local-engine': E, 'computer-recovery': self.recovery}[name])
        self.loader.start()
        self.addCleanup(self.loader.stop)
        baseline = mock.patch.object(M, 'BASELINE', self.baseline)
        baseline.start()
        self.addCleanup(baseline.stop)
        run = mock.patch.object(M.subprocess, 'run', side_effect=self.run_process)
        run.start()
        self.addCleanup(run.stop)

    def stage_vendor(self, root, version, *_):
        folder = root / 'vendor' / version
        folder.mkdir(parents=True)
        (folder / 'host-main.cjs').write_text(RUNNER)
        return folder, dict(self.baseline if version == self.baseline['version'] else self.vendor)

    def compose(self, root, baseline, upstream, output, node, **kwargs):
        self.assertIn('// canonical\n', baseline.read_text())
        self.assertEqual(kwargs, {'package_root': self.package, 'host_main': self.host})
        output.mkdir()
        return RUNNER.encode(), {'retainedModules': ['native BOX module']}

    def run_process(self, args, **_):
        if any(str(a).endswith('prepare-local-engine-wrap.mjs') for a in args):
            stock = pathlib.Path(args[-2]).read_text()
            pathlib.Path(args[-1]).write_text('/* openbot-stock-wrap */\n' + stock)
            value = {'payloadFingerprint': 'a' * 16, 'runtimePath': str(self.package / 'payload/runtime.cjs')}
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(value))
        self.assertTrue(any(str(a).endswith('verify-latest-local-engine.cjs') for a in args))
        self.assertIn('// canonical\n', pathlib.Path(args[-3]).read_text())
        self.assertEqual(args[-1], str(self.host))
        return types.SimpleNamespace(returncode=int(self.native_failed), stdout=json.dumps(self.check))

    def prepare(self):
        return M.prepare(self.stage, self.package, self.host, '/native/node', 'abcdef1')

    def assert_live_unchanged(self):
        for path, before in self.live.items():
            self.assertEqual(path.read_bytes(), before, str(path))
        self.assertFalse((self.root / 'openbot-local-only.enable').exists())

    def test_verified_pair_is_prepared_without_selecting_engine_or_changing_fleet(self):
        result = self.prepare()
        self.assertEqual(result['state'], 'prepared')
        self.assertFalse(result['activated'])
        self.assertFalse(result['nativeDeliveryVerified'])
        folder = pathlib.Path(result['directory'])
        self.assertEqual(json.loads((folder / 'preparation.json').read_bytes()), result)
        for name, key in (('stock.cjs', 'stockSha256'), ('wrapped.cjs', 'wrappedSha256'),
                          ('baseline.cjs', 'baselineCanonicalSha256'), ('baseline-wrapped.cjs', 'baselineWrappedSha256')):
            path = folder / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result[key])
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        self.assert_live_unchanged()

    def test_payload_change_between_baseline_and_latest_wrapping_cannot_publish_a_pair(self):
        real = self.run_process
        def run(args, **kwargs):
            value = real(args, **kwargs)
            if str(args[-1]).endswith('/wrapped.cjs'):
                proof = json.loads(value.stdout)
                proof['payloadFingerprint'] = 'b' * 16
                value.stdout = json.dumps(proof)
            return value
        with mock.patch.object(M.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'payload changed'):
                self.prepare()
        self.assertEqual(list(self.stage.glob('prepared-*/preparation.json')), [])
        self.assert_live_unchanged()

    def test_unreviewed_latest_release_refuses_before_loading_baseline_or_composing(self):
        self.sync.compatibility_recipe.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'no reviewed BOX'):
            self.prepare()
        self.assertEqual(self.sync.stage_vendor.call_count, 1)
        self.sync.compose.assert_not_called()
        self.assert_live_unchanged()

    def test_changed_baseline_manifest_or_bytes_refuse_before_composition(self):
        for mutate_bytes in (False, True):
            with self.subTest(mutate_bytes=mutate_bytes):
                def stage(root, version, *_):
                    folder, metadata = self.stage_vendor(root, version)
                    if version == self.baseline['version']:
                        if mutate_bytes: (folder / 'host-main.cjs').write_text('changed vendor source')
                        else: metadata['archiveSha256'] = 'b' * 64
                    return folder, metadata
                self.sync.stage_vendor.side_effect = stage
                with self.assertRaisesRegex(RuntimeError, 'baseline'):
                    self.prepare()
                self.sync.compose.assert_not_called()
                self.assert_live_unchanged()
                # Each subcase gets a distinct owned cache, never user state.
                self.stage = self.root / 'next-preparation'

    def test_failed_or_incomplete_final_native_contracts_do_not_publish_success(self):
        for proof in ('exit-failure', {'loaded': True}, []):
            with self.subTest(proof=proof):
                self.native_failed = proof == 'exit-failure'
                self.check = {} if self.native_failed else proof
                with self.assertRaisesRegex(RuntimeError, 'native offline'):
                    self.prepare()
                self.assertEqual(list(self.stage.glob('prepared-*/preparation.json')), [])
                self.assert_live_unchanged()
                self.stage = self.root / ('case-' + str(id(proof)))

    def test_symlinked_staging_tree_or_lock_is_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.stage.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'not symlinked'):
            self.prepare()
        self.stage.unlink()
        self.stage.mkdir()
        target = outside / 'lock'
        target.write_text('untouched')
        (self.stage / 'prepare.lock').symlink_to(target)
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(target.read_text(), 'untouched')
        self.sync.stage_vendor.assert_not_called()
        self.assert_live_unchanged()

    def test_invalid_vendor_selector_is_rejected_before_network(self):
        with self.assertRaisesRegex(RuntimeError, 'Invalid vendor version'):
            M.prepare(self.stage, self.package, self.host, '/native/node', '../elsewhere')
        self.sync.stage_vendor.assert_not_called()
        self.assert_live_unchanged()

    def test_relative_staging_path_refuses_before_creating_or_fetching(self):
        with self.assertRaisesRegex(RuntimeError, 'staging path must be absolute'):
            M.prepare(pathlib.Path('relative-stage'), self.package, self.host, '/native/node', 'abcdef1')
        self.sync.stage_vendor.assert_not_called()
        self.assert_live_unchanged()

    def test_shared_sticky_parent_is_allowed_but_foreign_staging_directory_is_not(self):
        parent = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=0)
        with mock.patch.object(M.os, 'getuid', return_value=1000), \
                mock.patch.object(pathlib.Path, 'lstat', return_value=parent):
            self.assertEqual(M.directory(self.root, allow_shared_parent=True), self.root)
            with self.assertRaisesRegex(RuntimeError, 'must be owned'):
                M.directory(self.root)
            parent.st_mode = stat.S_IFDIR | 0o777
            with self.assertRaisesRegex(RuntimeError, 'must be owned'):
                M.directory(self.root, allow_shared_parent=True)


if __name__ == '__main__':
    unittest.main()
