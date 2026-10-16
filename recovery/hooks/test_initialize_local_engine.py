import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M, E, P, S = [load(name) for name in ('initialize-local-engine', 'local-engine', 'prepare-local-engine', 'local-upstream-sync')]
RUNNER = ('  async sendPrompt(prompt, options2) {\n' + '    // native BOX runner\n' * 220 +
          '    return this.tm.turnRuntime.runAgentTurn(prompt, options2);\n  }\n'
          '  nextTurnEpoch(session) {}\n  runServerScheduledAutomation(args) {}\n')


class InitialEngineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.stage = self.root / 'preparation'
        self.stage.mkdir()
        self.directory = self.stage / 'prepared-one'
        self.directory.mkdir()
        self.source = {name: ((('/* openbot-stock-wrap */\n' if 'wrapped' in name else '') +
                         ('// BASELINE\n' if name.startswith('baseline') else '// LATEST\n') + RUNNER).encode())
                       for name in M.FILES}
        self.live = {}
        for name in ('host-main.cjs', 'openbot-mode', 'openbot-plan.json', 'openbot-bot-models.json',
                     'openbot-remint-plan.json', 'openbot-local-routines.json', 'openbot-secrets.json'):
            file = self.data / name
            file.write_text('unchanged owner state: ' + name)
            self.live[file] = file.read_bytes()
        self.report = {'schemaVersion': 1, 'state': 'prepared', 'activated': False, 'liveHostChanged': False,
                       'nativeDeliveryVerified': False, 'directory': str(self.directory), 'baseline': dict(P.BASELINE),
                       'upstream': {'version': '91a9d91', 'clientVersion': '0.70.0-pre.1',
                                    'sourceSha256': '54cfee26f4aeb4ebb7fecce747a1087d916b03e42ab5e0fe198e10d29fb4c3a3'},
                       'nativeOfflineContracts': {'loaded': True, 'latestDatabasePersistence': True},
                       'payload': {'payloadFingerprint': 'a' * 16, 'runtimePath': str(self.data / 'openbot/payload/runtime.cjs')},
                       **{key: hashlib.sha256(self.source[name]).hexdigest() for name, key in M.FILES.items()}}
        for name, raw in self.source.items():
            (self.directory / name).write_bytes(raw)
            (self.directory / name).chmod(0o400)
        self.write_report()
        self.preparer = mock.Mock(wraps=P)
        self.preparer.prepare.side_effect = lambda *_: dict(self.report)
        patch = mock.patch.object(M, 'load', side_effect=lambda name: {
            'local-engine': E, 'prepare-local-engine': self.preparer, 'local-upstream-sync': S}[name])
        patch.start()
        self.addCleanup(patch.stop)

    def write_report(self):
        (self.directory / 'preparation.json').write_text(json.dumps(self.report))

    def initialize(self, apply=False):
        return M.initialize(self.data, self.directory, self.data / 'openbot', self.data / 'host-main.cjs', '/native/node', apply)

    def assert_live_unchanged(self):
        for file, value in self.live.items():
            self.assertEqual(file.read_bytes(), value)
        self.assertFalse((self.data / 'openbot-local-only.enable').exists())

    def test_plan_reproduces_the_pair_without_creating_any_engine_authority(self):
        result = self.initialize()
        self.assertEqual(result['state'], 'validated')
        self.assertFalse(result['activated'])
        self.assertFalse((self.data / 'openbot-local-engine.json').exists())
        self.assertFalse((self.data / 'openbot-local-engine').exists())
        self.preparer.prepare.assert_called_once_with(self.stage, self.data / 'openbot', self.data / 'host-main.cjs', '/native/node', '91a9d91')
        self.assert_live_unchanged()

    def test_initial_selection_publishes_complete_readonly_pair_and_future_compatibility_base(self):
        result = self.initialize(apply=True)
        self.assertTrue(result['activated'])
        self.assertFalse(result['hostReloadRequested'])
        self.assertFalse(result['nativeDeliveryVerified'])
        policy = E.policy(self.data)
        self.assertEqual(policy['clientVersion'], '0.70.0-pre.1')
        self.assertEqual(policy['generationId'], result['generationId'])
        folder = E.artifact_directory(self.data, policy)
        for name in ('stock.cjs', 'wrapped.cjs'):
            self.assertEqual((folder / name).read_bytes(), self.source[name])
            self.assertEqual((folder / name).stat().st_mode & 0o777, 0o400)
        self.assertEqual((self.data / 'openbot-local-engine.json').stat().st_mode & 0o777, 0o600)
        baseline, identity = S.compatibility_base(self.data, policy)
        self.assertEqual(baseline.read_bytes(), self.source['baseline.cjs'])
        self.assertEqual(identity['generationId'], policy['boxCompatibilityBase']['generationId'])
        self.assertEqual(E.select_source(self.data, pathlib.Path('host-main.cjs'), ''), self.source['wrapped.cjs'].decode())
        self.assert_live_unchanged()

    def test_existing_active_disabled_or_symlinked_policies_are_never_overwritten(self):
        path = self.data / 'openbot-local-engine.json'
        for value in (b'{"enabled":true}', b'{"enabled":false}', None):
            with self.subTest(value=value):
                if value is None:
                    path.symlink_to(self.data / 'missing')
                else:
                    path.write_bytes(value)
                with self.assertRaisesRegex(RuntimeError, 'already exists'):
                    self.initialize(apply=True)
                if value is not None:
                    self.assertEqual(path.read_bytes(), value)
                else:
                    self.assertTrue(path.is_symlink())
                path.unlink()
        self.preparer.prepare.assert_not_called()
        self.assert_live_unchanged()

    def test_changed_prepared_bytes_or_unreviewed_provenance_never_select(self):
        path = self.directory / 'wrapped.cjs'
        path.chmod(0o600)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'differs from its report'):
            self.initialize(True)
        path.write_bytes(self.source['wrapped.cjs'])
        self.report['upstream']['sourceSha256'] = 'b' * 64
        self.write_report()
        with self.assertRaisesRegex(RuntimeError, 'unreviewed provenance'):
            self.initialize(True)
        self.assertFalse((self.data / 'openbot-local-engine.json').exists())
        self.preparer.prepare.assert_not_called()
        self.assert_live_unchanged()

    def test_changed_payload_reconstruction_failure_or_missing_contracts_cannot_activate(self):
        for failure in ('hash', 'exception', 'contracts'):
            with self.subTest(failure=failure):
                def reproduce(*_):
                    if failure == 'exception':
                        raise RuntimeError('native contract failed')
                    value = dict(self.report)
                    if failure == 'hash':
                        value['wrappedSha256'] = 'b' * 64
                    else:
                        value['nativeOfflineContracts'] = {'loaded': False}
                        (self.directory / 'preparation.json').write_text(json.dumps(value))
                    return value
                self.preparer.prepare.side_effect = reproduce
                with self.assertRaises(RuntimeError):
                    self.initialize(True)
                self.write_report()
                self.assertFalse((self.data / 'openbot-local-engine.json').exists())
                self.assert_live_unchanged()

    def test_concurrent_policy_creation_is_retained_by_exclusive_atomic_publication(self):
        real = M.publish_initial
        owner = b'{"owner":"concurrent decision"}'
        def concurrent(path, raw):
            path.write_bytes(owner)
            real(path, raw)
        with mock.patch.object(M, 'publish_initial', side_effect=concurrent), self.assertRaises(FileExistsError):
            self.initialize(True)
        self.assertEqual((self.data / 'openbot-local-engine.json').read_bytes(), owner)
        self.assert_live_unchanged()

    def test_preparation_changed_during_reconstruction_is_refused(self):
        def reproduce(*_):
            self.write_report()
            path = self.directory / 'preparation.json'
            path.write_bytes(path.read_bytes() + b'\n')
            return dict(self.report)
        self.preparer.prepare.side_effect = reproduce
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            self.initialize(True)
        self.assertFalse((self.data / 'openbot-local-engine.json').exists())
        self.assert_live_unchanged()

    def test_generation_write_failure_cannot_publish_an_incomplete_policy(self):
        real = M.write_exclusive
        def failure(path, raw, mode):
            if path.name == 'wrapped.cjs':
                raise OSError('interrupted generation write')
            real(path, raw, mode)
        with mock.patch.object(M, 'write_exclusive', side_effect=failure), self.assertRaises(OSError):
            self.initialize(True)
        self.assertFalse((self.data / 'openbot-local-engine.json').exists())
        self.assert_live_unchanged()

    def test_symlinked_sources_generation_root_lock_or_data_root_are_rejected(self):
        source = self.directory / 'stock.cjs'
        source.unlink()
        source.symlink_to(self.data / 'host-main.cjs')
        with self.assertRaises(OSError):
            self.initialize(True)
        source.unlink()
        source.write_bytes(self.source['stock.cjs'])
        engine_root = self.data / 'openbot-local-engine'
        engine_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'nonsymlinked'):
            self.initialize(True)
        engine_root.unlink()
        lock = self.data / 'computer-recovery.lock'
        lock.unlink()
        lock.symlink_to(self.data / 'host-main.cjs')
        with self.assertRaises(OSError):
            self.initialize(True)
        alias = self.root / 'data-alias'
        alias.symlink_to(self.data, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'nonsymlinked'):
            M.initialize(alias, self.directory, self.data / 'openbot', self.data / 'host-main.cjs', '/native/node', True)
        self.assertFalse((self.data / 'openbot-local-engine.json').exists())
        self.assert_live_unchanged()


if __name__ == '__main__':
    unittest.main()
