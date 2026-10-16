import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('payload_local_engine', pathlib.Path(__file__).with_name('local-engine.py'))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class PinnedPayloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.sd = pathlib.Path(self.folder.name)
        self.artifacts = self.sd / 'openbot-local-engine'
        self.artifacts.mkdir()
        self.stock = '  async sendPrompt(prompt, options2) {\n' + 'x' * 4100 + '\n  nextTurnEpoch(\nrunServerScheduledAutomation\nrunAgentTurn\n'
        self.wrapped = '/* openbot-stock-wrap */\n/* openbot-payload 1111111111111111 */\n' + self.stock
        (self.artifacts / 'stock.cjs').write_text(self.stock)
        (self.artifacts / 'wrapped.cjs').write_text(self.wrapped)
        (self.sd / 'openbot-local-engine.json').write_text(json.dumps({
            'schemaVersion': 1, 'enabled': True,
            'stockSha256': hashlib.sha256(self.stock.encode()).hexdigest(),
            'wrappedSha256': hashlib.sha256(self.wrapped.encode()).hexdigest(),
        }))

    def select(self, current):
        return engine.select_source(self.sd, self.sd / 'host-main.cjs', current)

    def test_payload_upgrade_keeps_current_engine_without_changing_pin(self):
        updated = self.wrapped.replace('1111111111111111', '2222222222222222')
        policy_before = (self.sd / 'openbot-local-engine.json').read_bytes()
        self.assertEqual(self.select(updated), updated)
        self.assertEqual((self.sd / 'openbot-local-engine.json').read_bytes(), policy_before)
        self.assertEqual((self.artifacts / 'wrapped.cjs').read_text(), self.wrapped)

    def test_changed_engine_body_is_restored_even_with_a_valid_payload_stamp(self):
        updated = self.wrapped.replace('1111111111111111', '2222222222222222').replace('runAgentTurn', 'temporalOnly')
        self.assertEqual(self.select(updated), self.wrapped)

    def test_runtime_path_or_extra_code_change_cannot_bypass_pin(self):
        self.assertEqual(self.select(self.wrapped + '\nrequire("/different/runtime.cjs");'), self.wrapped)

    def test_missing_duplicate_or_malformed_stamp_is_restored(self):
        for current in [self.wrapped.replace('/* openbot-payload 1111111111111111 */\n', ''),
                        self.wrapped + '/* openbot-payload 2222222222222222 */\n',
                        self.wrapped.replace('1111111111111111', 'invalid')]:
            with self.subTest(current=current[:80]):
                self.assertEqual(self.select(current), self.wrapped)

    def test_corrupt_trusted_artifact_is_refused_before_accepting_upgrade(self):
        (self.artifacts / 'wrapped.cjs').write_text(self.wrapped + 'changed')
        with self.assertRaisesRegex(RuntimeError, 'hash differs'):
            self.select(self.wrapped.replace('1111111111111111', '2222222222222222'))


if __name__ == '__main__':
    unittest.main()
