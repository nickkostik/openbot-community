import importlib.util
import pathlib
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('readiness', pathlib.Path(__file__).with_name('window-readiness-inject.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class WindowReadinessTests(unittest.TestCase):
    def test_both_native_probes_are_bounded_unix_only_and_idempotent(self):
        source = m.ANCHOR + '\n return `' + m.OLD + '`;\n}\n' + 'const runsAnything = `' + m.OLD + '`;'
        patched = m.patch(source)
        self.assertEqual(patched.count(m.NEW), 2)
        self.assertNotIn(m.OLD, patched)
        self.assertEqual(m.patch(patched), patched)
        for drift in [source.replace(m.OLD, '', 1), source + m.OLD, patched.replace(m.NEW, m.OLD, 1)]:
            with self.assertRaises(RuntimeError): m.patch(drift)

    def test_start_script_retains_owner_guards_and_uses_unix_display(self):
        source = '#!/bin/bash\n' + m.SCRIPT_OLD + '\n[ "$token" = "$owner" ] || exit 75\n'
        updated = m.patch_script(source)
        self.assertIn('"unix/${DISP}"', updated)
        self.assertIn('[ "$token" = "$owner" ] || exit 75', updated)
        self.assertEqual(m.patch_script(updated), updated)
        self.assertEqual(subprocess.run(['/bin/bash', '-n'], input=updated, text=True).returncode, 0)
        with self.assertRaises(RuntimeError): m.patch_script(source + m.SCRIPT_OLD)

    def test_latest_composition_bounds_the_live_and_retained_helpers(self):
        legacy = '/* ' + m.MARK + ' */\nfunction __openbot_box_windowAnswers(windowIndex) { return `' + m.NEW + '`; }\n' + m.NEW
        source = '/* openbot-latest-box-compatibility-v1 */\n' + legacy + '\n' + m.ANCHOR + m.OLD + '}\n' + m.OLD
        patched = m.patch(source)
        self.assertEqual(patched.count(m.NEW), 4)
        self.assertNotIn(m.OLD, patched)
        self.assertEqual(m.patch(patched), patched)
        with self.assertRaises(RuntimeError): m.patch(source + m.OLD)
        with self.assertRaises(RuntimeError): m.patch(source.replace(m.ANCHOR, 'function other() {'))

    def test_unix_transport_cannot_contact_the_overlapping_tcp_port(self):
        # The protocol prefix matters: ':80' alone tries localhost:6080 when X
        # is absent. Keeping the timeout while dropping 'unix/' is insufficient.
        for display in (80, 81):
            rendered = m.NEW.replace('${windowIndex}', str(display))
            self.assertIn('xdpyinfo -display unix/:' + str(display), rendered)
            self.assertIn('timeout -k 1 2', rendered)

    def test_pre_x_dbus_and_desktop_x_checks_share_the_bounded_unix_transport(self):
        source = '#!/bin/bash\neval "$(' + m.DBUS_OLD + ')"\n' + m.DBUS_OLD + '\n' + m.DESKTOP_OLD + '\n'
        updated = m.patch_desktop_script(source)
        self.assertEqual(updated.count(m.DBUS_NEW), 2)
        self.assertEqual(updated.count(m.DESKTOP_NEW), 1)
        self.assertEqual(m.patch_desktop_script(updated), updated)
        self.assertEqual(subprocess.run(['/bin/bash', '-n'], input=updated, text=True).returncode, 0)
        for drift in [source.replace(m.DBUS_OLD, '', 1), updated.replace(m.DESKTOP_NEW, m.DESKTOP_OLD)]:
            with self.assertRaises(RuntimeError): m.patch_desktop_script(drift)

    def test_background_xvfb_painter_cannot_hang_before_server_readiness(self):
        source = '#!/bin/bash\n' + m.XVFB_OLD + ' && break\n'
        patched = m.patch_xvfb_script(source)
        self.assertEqual(patched.count(m.XVFB_NEW), 1)
        self.assertEqual(m.patch_xvfb_script(patched), patched)
        with self.assertRaises(RuntimeError): m.patch_xvfb_script(source + m.XVFB_OLD)


if __name__ == '__main__': unittest.main()
