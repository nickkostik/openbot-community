import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
MANAGED = 'grokbot-desktop.service'
RECOVERY_LAUNCHER = 'grokbot-date-recovery-launch.service'


class DateGuardWatcherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.asar = self.root / 'Grok Bot' / 'app.asar'
        self.asar.parent.mkdir()
        self.asar.write_text('stock')
        self.events = self.root / 'events'
        self.events.touch()
        self.set_control('patch', 'install')
        self.set_control('restart', 'success')
        self.set_control('active', MANAGED)
        self.set_control('native', 'no')
        self.env = {
            **os.environ,
            'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'XDG_RUNTIME_DIR': str(self.root),
            'DATE_GUARD_TEST_ROOT': str(self.root),
        }

        watcher = (HERE / 'ensure-date-guard.sh').read_text()
        substitutions = {
            'asar="/opt/Grok Bot/resources/app.asar"': 'asar=' + shlex.quote(str(self.asar)),
            'state="$HOME/.local/state/openbot"': 'state=' + shlex.quote(str(self.state)),
            '/usr/bin/python3 - "$1"': 'fixture-native-desktop "$1"',
        }
        for original, replacement in substitutions.items():
            self.assertEqual(watcher.count(original), 1)
            watcher = watcher.replace(original, replacement)
        self.watcher = self.root / 'ensure-date-guard.sh'
        self.watcher.write_text(watcher)
        self.tool('sudo', '#!/bin/sh\nshift\nexec "$@"\n')
        (self.root / 'patch-current-date.py').write_text('''
import os
from pathlib import Path
root = Path(os.environ['DATE_GUARD_TEST_ROOT'])
archive = root / 'Grok Bot' / 'app.asar'
behavior = (root / 'patch').read_text()
if behavior == 'unsupported':
    raise SystemExit('Renderer layout is unsupported')
archive.write_text('fixed')
if behavior == 'interrupted':
    raise SystemExit('Watcher interrupted after replacing the archive')
''')
        self.tool('systemctl', '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['DATE_GUARD_TEST_ROOT'])
target = sys.argv[-1]
active = root / 'active'
if 'is-active' in sys.argv:
    raise SystemExit(0 if active.read_text() == target else 3)
if 'restart' in sys.argv:
    with (root / 'events').open('a') as stream:
        print('restart ' + target, file=stream)
    success = (root / 'restart').read_text() == 'success'
    active.write_text(target if success else '')
    raise SystemExit(0 if success else 1)
raise SystemExit(2)
''')
        self.tool('fixture-native-desktop', '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
root = Path(os.environ['DATE_GUARD_TEST_ROOT'])
native = root / 'native'
if sys.argv[1] == 'status':
    print(native.read_text())
else:
    with (root / 'events').open('a') as stream:
        print('stop desktop-menu', file=stream)
    native.write_text('no')
''')
        self.tool('gtk-launch', '''#!/usr/bin/env python3
import os
from pathlib import Path
root = Path(os.environ['DATE_GUARD_TEST_ROOT'])
with (root / 'events').open('a') as stream:
    print('start desktop-menu', file=stream)
success = (root / 'restart').read_text() == 'success'
(root / 'native').write_text('yes' if success else 'no')
raise SystemExit(0 if success else 1)
''')

    def tool(self, name, source):
        executable = self.bin / name
        executable.write_text(source)
        executable.chmod(0o700)

    def set_control(self, name, value):
        (self.root / name).write_text(value)

    def run_watcher(self, expected_status=0):
        result = subprocess.run(
            ['bash', str(self.watcher)], env=self.env, capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, expected_status, result.stderr)

    def assert_resolved(self):
        for suffix in ('reload-baseline', 'reload-target', 'alert'):
            self.assertFalse((self.state / ('date-guard.' + suffix)).exists())

    def test_already_guarded_archive_clears_alert_without_restart(self):
        self.asar.write_text('fixed')
        (self.state / 'date-guard.alert').write_text('Old failure')
        self.run_watcher()
        self.assert_resolved()
        self.assertEqual(self.events.read_text(), '')

    def test_failed_managed_restart_retries_the_same_inactive_service(self):
        self.set_control('active', RECOVERY_LAUNCHER)
        self.set_control('restart', 'failure')
        self.run_watcher(expected_status=1)
        self.assertEqual(self.asar.read_text(), 'fixed')
        self.assertEqual((self.state / 'date-guard.reload-target').read_text().strip(), RECOVERY_LAUNCHER)
        self.assertIn('reload is pending', (self.state / 'date-guard.alert').read_text())
        self.set_control('restart', 'success')
        self.run_watcher()
        self.assert_resolved()
        self.assertEqual(self.events.read_text().splitlines(), [f'restart {RECOVERY_LAUNCHER}'] * 2)

    def test_interruption_between_patching_and_restarting_is_recoverable(self):
        self.set_control('patch', 'interrupted')
        self.run_watcher(expected_status=1)
        self.assertEqual(self.asar.read_text(), 'fixed')
        self.assertEqual(self.events.read_text(), '')
        self.set_control('patch', 'install')
        self.run_watcher()
        self.assert_resolved()
        self.assertEqual(self.events.read_text().splitlines(), [f'restart {MANAGED}'])

    def test_unsupported_archive_keeps_one_current_alert_and_leaves_app_running(self):
        self.set_control('patch', 'unsupported')
        self.run_watcher(expected_status=1)
        self.run_watcher(expected_status=1)
        self.assertEqual(self.asar.read_text(), 'stock')
        self.assertEqual(self.events.read_text(), '')
        alert = (self.state / 'date-guard.alert').read_text().splitlines()
        self.assertEqual(len(alert), 1)
        self.assertIn('Renderer layout is unsupported', alert[0])
        self.set_control('patch', 'install')
        self.run_watcher()
        self.assert_resolved()

    def test_desktop_menu_launcher_retries_after_the_original_process_stops(self):
        self.set_control('active', '')
        self.set_control('native', 'yes')
        self.set_control('restart', 'failure')
        self.run_watcher(expected_status=1)
        self.assertEqual((self.root / 'native').read_text(), 'no')
        self.set_control('restart', 'success')
        self.run_watcher()
        self.assert_resolved()
        self.assertEqual((self.root / 'native').read_text(), 'yes')
        self.assertEqual(self.events.read_text().splitlines(), [
            'stop desktop-menu', 'start desktop-menu', 'stop desktop-menu', 'start desktop-menu',
        ])

    def test_closed_desktop_stays_closed_after_patch(self):
        self.set_control('active', '')
        self.run_watcher()
        self.assert_resolved()
        self.assertEqual(self.asar.read_text(), 'fixed')
        self.assertEqual(self.events.read_text(), '')


if __name__ == '__main__':
    unittest.main()
