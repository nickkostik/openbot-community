import importlib.util
import pathlib
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('routine_writer', HERE / 'native-routine-trigger-preservation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NativeRoutineTriggerPreservationTests(unittest.TestCase):
    def fixture(self, original=None):
        source = original or (m.START + '\n  // bounded test fixture, not a vendor bundle\n' + m.OLD + '\n}\n' + m.END + 'detail) {}\n')
        expected = source.replace(m.OLD, m.NEW)
        return source, expected

    def reviewed_fixture(self, source, result):
        return patch.multiple(m, REVIEWED={m.sha256(source): m.sha256(result)},
                              BEFORE_BLOCK_SHA256=m.sha256(m.serializer_block(source)),
                              AFTER_BLOCK_SHA256=m.sha256(m.serializer_block(result)))

    def test_unknown_full_source_is_refused_before_transform(self):
        source, _ = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
            m.patch(source)

    def test_exact_reviewed_transform_keeps_schedule_and_nonwriter_bytes(self):
        source, expected = self.fixture('const before = 1;\n' + self.fixture()[0] + 'const after = 2;\n')
        with self.reviewed_fixture(source, expected):
            self.assertEqual(m.patch(source), expected)
            self.assertEqual(m.patch(expected), expected)
            self.assertEqual(expected.replace(m.NEW, m.OLD), source)

    def test_serializer_body_drift_fails_even_when_full_source_allowlisted(self):
        source, expected = self.fixture()
        with self.reviewed_fixture(source, expected):
            m.BEFORE_BLOCK_SHA256 = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'source changed'):
                m.patch(source)

    def test_ambiguous_or_moved_boundaries_refuse(self):
        source, _ = self.fixture()
        for altered in [source + m.START, source + m.END, m.END + source.replace(m.END, '')]:
            with self.assertRaisesRegex(RuntimeError, 'boundary'):
                m.serializer_block(altered)

    def test_wrong_output_cas_refuses(self):
        source, expected = self.fixture()
        with self.reviewed_fixture(source, expected):
            m.REVIEWED[m.sha256(source)] = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'output differs'):
                m.patch(source)

    def test_incomplete_marked_patch_is_not_treated_as_success(self):
        source, expected = self.fixture()
        bad = expected.replace(m.NEW, m.NEW + '\n' + m.OLD)
        with patch.object(m, 'REVIEWED', {m.sha256(source): m.sha256(bad)}):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                m.patch(bad)

    def test_proof_is_explicitly_staged_and_does_not_claim_idle_or_activation(self):
        source, expected = self.fixture()
        with self.reviewed_fixture(source, expected):
            result = m.proof(source, expected)
        self.assertFalse(result['activated'])
        self.assertFalse(result['liveApplied'])
        self.assertFalse(result['historyLimitChanged'])
        self.assertFalse(result['agentFilesChanged'])
        self.assertFalse(result['modeChanged'])
        self.assertNotIn('idle', result)

    def component_fixture(self):
        source, result = self.fixture()
        parser = 'function parseStoredAutomationConfig(raw) { return raw; }'
        trigger = 'function serializeStoredTrigger(t) { return t; }'
        # Delimiters are genuine module-level function boundaries; no vendor fixture.
        source = parser + '\n' + source + trigger + '\nfunction triggerIdentity(t) {}\n'
        result = source.replace(m.OLD, m.NEW)
        deps = ((parser.split('raw')[0], '\nfunction serializeStoredAutomationConfig(', m.sha256(parser)),
                ('function serializeStoredTrigger(', '\nfunction triggerIdentity(', m.sha256(trigger)))
        return source, result, deps

    def test_component_injector_allows_outer_wrapper_but_pins_dependencies(self):
        source, expected, deps = self.component_fixture()
        with self.reviewed_fixture(source, expected), patch.object(m, 'DEPENDENCIES', deps):
            outer = '/* separately CAS-verified wrapper */\n' + source
            self.assertEqual(m.patch_component(outer), '/* separately CAS-verified wrapper */\n' + expected)
            self.assertEqual(m.patch_component(m.patch_component(outer)), m.patch_component(outer))
            with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
                m.patch(outer)

    def test_component_injector_refuses_dependency_edits_and_duplicate_native_copies(self):
        source, expected, deps = self.component_fixture()
        with self.reviewed_fixture(source, expected), patch.object(m, 'DEPENDENCIES', deps):
            for altered in [source.replace('return raw;', 'return null;'), source + source]:
                with self.assertRaisesRegex(RuntimeError, 'dependency'):
                    m.patch_component(altered)

    def test_component_injector_refuses_unknown_serializer_and_partial_markers(self):
        source, expected, deps = self.component_fixture()
        with self.reviewed_fixture(source, expected), patch.object(m, 'DEPENDENCIES', deps):
            for altered in [source + m.MARK, source.replace(m.OLD, '      trigger: null,'), expected + m.OLD]:
                with self.assertRaises(RuntimeError):
                    m.patch_component(altered)

    def test_no_patch_api_mutates_host_or_agents_or_invokes_reload(self):
        # The production module is a pure transform: no filesystem/process/network API.
        source = (HERE / 'native-routine-trigger-preservation.py').read_text()
        for forbidden in ['subprocess', 'os.open(', '.write_text(', '.write_bytes(', 'urllib', 'fetch(']:
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
