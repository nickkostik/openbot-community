import importlib.util
import json
import pathlib
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('auto_assign', pathlib.Path(__file__).with_name('openbot-auto-assign.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = '00000000-0000-4000-8000-000000000001'
B = '00000000-0000-4000-8000-000000000002'
C = '00000000-0000-4000-8000-000000000003'


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        (self.root / 'openbot-mode').write_text('custom')
        self.write_plan('go', 'chosen')
        self.write('openbot-bot-models.json', {'assignments': {B: 'openai:pin'},
            'reasoning': {B: {'openai:pin': 'max'}}, 'other': 'preserved'})
        for aid in [A, B, C]:
            self.write('agents/' + aid + '/profile.json', {'id': aid, 'harness': 'box'})
            self.write('agents/' + aid + '/settings.json', {'hiddenFromSidebar': aid == C})
            (self.root / 'agents' / aid / 'store.db').write_bytes(b'PRESERVED TRANSCRIPT')

    def write(self, name, value):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def bytes(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def write_plan(self, provider, slug):
        self.write('openbot-plan.json', {'kind': 'custom', 'agents': {'*': {'providerId': provider, 'modelId': slug}},
            'catalog': {'models': [{'id': 'go:chosen', 'providerId': 'go', 'slug': 'chosen'},
                                   {'id': 'openai:pin', 'providerId': 'openai', 'slug': 'pin'}]}})

    def test_inherited_default_tracks_picker_without_overwriting_pins_or_efforts(self):
        before = self.bytes()
        report = M.audit(self.root)
        self.assertEqual(report['effectiveDefault'], 'go:chosen')
        self.assertEqual(report['inheritsDefault'], 1); self.assertEqual(report['explicitPins'], 1)
        self.assertEqual(report['visibleBots'], 2); self.assertEqual(before, self.bytes())
        self.write_plan('openai', 'pin')
        before = self.bytes()
        self.assertEqual(M.audit(self.root)['effectiveDefault'], 'openai:pin')
        self.assertEqual(before, self.bytes())

    def test_official_does_not_assign_a_custom_model_or_change_the_saved_mode(self):
        (self.root / 'openbot-mode').write_text('official')
        before = self.bytes()
        self.assertEqual(M.audit(self.root)['effectiveDefault'], 'official')
        self.assertEqual(before, self.bytes())

    def test_stale_pin_is_reported_and_preserved(self):
        self.write('openbot-bot-models.json', {'assignments': {B: 'removed:model'}})
        before = self.bytes()
        self.assertEqual(M.audit(self.root)['stalePinBotIds'], [B])
        self.assertEqual(before, self.bytes())

    def test_symlinked_bot_or_settings_are_not_followed(self):
        path = self.root / 'agents' / A
        for p in path.iterdir(): p.unlink()
        path.rmdir(); path.symlink_to(self.root / 'agents' / B, target_is_directory=True)
        with self.assertRaises(RuntimeError): M.audit(self.root)

    def test_missing_default_or_unknown_mode_fails_without_writing(self):
        self.write('openbot-plan.json', {'catalog': {'agents': {}, 'models': []}})
        before = self.bytes()
        with self.assertRaises(RuntimeError): M.audit(self.root)
        self.assertEqual(before, self.bytes())
        (self.root / 'openbot-mode').write_text('unexpected')
        with self.assertRaises(RuntimeError): M.audit(self.root)


if __name__ == '__main__':
    unittest.main()
