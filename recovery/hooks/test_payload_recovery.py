import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


def load(name):
    path = pathlib.Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CanonicalPayloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sd = pathlib.Path(self.temp.name)
        self.engine = load('local-engine')
        self.recovery = load('computer-recovery')
        self.recovery.SD = self.sd
        self.recovery.local_engine = lambda: self.engine
        self.recovery.patched = lambda source: source + '\n// owned injector revision\n'
        self.source = (
            '/* openbot-stock-wrap */\n/* openbot-payload 0123456789abcdef */\n'
            '  async sendPrompt(prompt, options2) {' + ' ' * 4100 +
            '\n  nextTurnEpoch(\nrunServerScheduledAutomation runAgentTurn\n'
        )
        directory = self.sd / 'openbot-local-engine'
        directory.mkdir(mode=0o700)
        (directory / 'wrapped.cjs').write_text(self.source)
        stock = self.source.replace('/* openbot-stock-wrap */\n', '')
        (directory / 'stock.cjs').write_text(stock)
        (self.sd / 'openbot-local-engine.json').write_text(json.dumps({
            'schemaVersion': 1, 'enabled': True,
            'wrappedSha256': hashlib.sha256(self.source.encode()).hexdigest(),
            'stockSha256': hashlib.sha256(stock.encode()).hexdigest(),
        }))
        self.path = self.sd / 'host-main.cjs'
        self.current = self.recovery.patched(self.source).replace('0123456789abcdef', 'ffffffffffffffff')
        self.path.write_text(self.current)
        self.checked = []

    def repair(self):
        return self.recovery.repair_bundle(self.path, self.sd / 'backups', self.checked.append)

    def test_new_payload_after_owned_injectors_needs_no_repair(self):
        self.assertFalse(self.repair())
        self.assertEqual(self.path.read_text(), self.current)
        self.assertEqual(self.checked, [])
        self.assertFalse((self.sd / 'backups').exists())

    def test_other_body_changes_restore_complete_canonical_engine(self):
        self.path.write_text(self.current + '// unexpected engine change\n')
        self.assertTrue(self.repair())
        self.assertEqual(self.path.read_text(), self.recovery.patched(self.source))
        self.assertEqual(len(self.checked), 1)

    def test_corrupt_pin_is_refused_before_canonicalization_or_live_write(self):
        (self.sd / 'openbot-local-engine/wrapped.cjs').write_text(self.source + '// tampered pin\n')
        self.recovery.patched = lambda source: self.fail('Corrupt pin reached owned injectors')
        with self.assertRaisesRegex(RuntimeError, 'hash differs'):
            self.repair()
        self.assertEqual(self.path.read_text(), self.current)
        self.assertEqual(self.checked, [])
        self.assertFalse((self.sd / 'backups').exists())


if __name__ == '__main__':
    unittest.main()
