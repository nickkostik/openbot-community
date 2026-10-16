import hashlib
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('browser_egress', HERE / 'browser-egress.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BrowserEgressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.sd = self.root / 'data'
        self.sd.mkdir()
        self.launcher = self.root / 'start-egress-tunnel'
        self.launcher.write_text(m.STOCK_LAUNCHER)
        self.patches = [mock.patch.object(m, 'SD', self.sd), mock.patch.object(m, 'LAUNCHER', self.launcher)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def select(self, mode='custom', direct=True, local=True):
        (self.sd / 'openbot-mode').write_text(mode)
        for name, enabled in [('openbot-browser-direct.enable', direct), ('openbot-local-only.enable', local)]:
            if enabled:
                (self.sd / name).touch()

    def fixture_process(self, proc, pid, parent, argv, exe, rules=None, start='1234'):
        path = proc / str(pid)
        path.mkdir(parents=True)
        (path / 'exe').symlink_to(exe)
        (path / 'stat').write_text(str(pid) + ' (process) ' + ' '.join(['S', str(parent)] + ['0'] * 17 + [start]))
        (path / 'cmdline').write_bytes(('\0'.join(argv) + '\0').encode())
        env = b'SAND_EGRESS_TUNNEL_BEARER=do-not-log-this\0'
        if rules is not None:
            env += ('SAND_EGRESS_TUNNEL_RELAY_RULES=' + rules + '\0').encode()
        (path / 'environ').write_bytes(env)
        return path

    def test_no_opt_in_never_inspects_or_changes_native_processes(self):
        with mock.patch.object(m, 'ensure_launcher') as install, mock.patch.object(m, 'owned_tunnel') as inspect:
            self.assertEqual(m.ensure_direct_browser_egress(), {'state': 'not-selected'})
            install.assert_not_called()
            inspect.assert_not_called()

    def test_only_custom_local_with_explicit_direct_setting_selects_route(self):
        for mode, direct, local in [('official', True, True), ('custom', False, True), ('custom', True, False), ('custom', True, True)]:
            for path in self.sd.iterdir():
                path.unlink()
            self.select(mode, direct, local)
            self.assertEqual(m.direct_selected(), mode == 'custom' and direct and local)

    def test_missing_mode_is_not_selected(self):
        self.select()
        (self.sd / 'openbot-mode').unlink()
        self.assertFalse(m.direct_selected())

    def test_actual_launcher_preserves_relay_environment_until_explicitly_selected(self):
        stub = self.root / 'native'
        stub.write_text('#!/bin/bash\nprintf "%s %s" "$SAND_EGRESS_TUNNEL_RELAY_RULES" "$SAND_EGRESS_TUNNEL_BEARER"\n')
        stub.chmod(0o755)
        script = m.DIRECT_LAUNCHER.replace('/home/box/sand-data', str(self.sd)).replace(m.BINARY, str(stub))
        env = {**os.environ, 'SAND_EGRESS_TUNNEL_RELAY_RULES': 'all', 'SAND_EGRESS_TUNNEL_BEARER': 'preserved-test-credential'}
        for mode, direct, local, expected in [('custom', True, True, '[{"hosts":["*"],"action":"direct"}]'), ('official', True, True, 'all'), ('custom', False, True, 'all'), ('custom', True, False, 'all')]:
            for path in self.sd.iterdir():
                path.unlink()
            self.select(mode, direct, local)
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, expected + ' preserved-test-credential')
            if direct and local and mode == 'custom':
                self.assertEqual(json.loads(result.stdout.rsplit(' ', 1)[0]), m.DIRECT_RULES)

    def test_known_launcher_backed_up_then_installed_once(self):
        with mock.patch.object(m, 'install_launcher', side_effect=lambda before: self.launcher.write_text(m.DIRECT_LAUNCHER)) as install:
            self.assertTrue(m.ensure_launcher())
            self.assertFalse(m.ensure_launcher())
            install.assert_called_once_with(m.STOCK_LAUNCHER)
        backup = self.sd / 'recovery-snapshots/browser-egress' / (hashlib.sha256(m.STOCK_LAUNCHER.encode()).hexdigest() + '.sh')
        self.assertEqual(backup.read_text(), m.STOCK_LAUNCHER)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_unknown_launcher_not_overwritten(self):
        self.launcher.write_text('#!/bin/bash\n# future platform launcher\n')
        with mock.patch.object(m, 'install_launcher') as install:
            with self.assertRaisesRegex(RuntimeError, 'Unknown native'):
                m.ensure_launcher()
            install.assert_not_called()

    def test_symlink_launcher_not_followed(self):
        original = self.root / 'original'
        self.launcher.rename(original)
        self.launcher.symlink_to(original)
        with self.assertRaisesRegex(RuntimeError, 'symlinked'):
            m.ensure_launcher()
        self.assertEqual(original.read_text(), m.STOCK_LAUNCHER)

    def test_bad_shell_syntax_check_prevents_install(self):
        with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=1)), mock.patch.object(m, 'install_launcher') as install:
            with self.assertRaisesRegex(RuntimeError, 'syntax'):
                m.ensure_launcher()
            install.assert_not_called()

    def test_conflicting_backup_prevents_install(self):
        backup = self.sd / 'recovery-snapshots/browser-egress'
        backup.mkdir(parents=True)
        (backup / (hashlib.sha256(m.STOCK_LAUNCHER.encode()).hexdigest() + '.sh')).write_text('other')
        with mock.patch.object(m, 'install_launcher') as install:
            with self.assertRaisesRegex(RuntimeError, 'backup differs'):
                m.ensure_launcher()
            install.assert_not_called()

    def test_native_process_identity_checked_and_credential_not_returned(self):
        proc = self.root / 'proc'
        self.fixture_process(proc, 313, 1, m.SUPERVISOR_ARGV, '/usr/bin/bash')
        self.fixture_process(proc, 361, 313, [m.BINARY, 'server'], m.BINARY, json.dumps(m.DIRECT_RULES))
        child = m.owned_tunnel(proc)
        self.assertEqual(child['rules'], 'direct')
        self.assertEqual(child['startTime'], '1234')
        self.assertNotIn('do-not-log-this', json.dumps(child))

    def test_foreign_supervisor_rejected(self):
        proc = self.root / 'proc'
        self.fixture_process(proc, 313, 1, ['bash', '/other/launcher'], '/usr/bin/bash')
        self.fixture_process(proc, 361, 313, [m.BINARY, 'server'], m.BINARY)
        with self.assertRaisesRegex(RuntimeError, 'not supervised'):
            m.owned_tunnel(proc)

    def test_inaccessible_foreign_proc_entries_do_not_hide_the_owned_tunnel(self):
        proc = self.root / 'proc'
        foreign = proc / '1';foreign.mkdir(parents=True)
        self.fixture_process(proc, 313, 1, m.SUPERVISOR_ARGV, '/usr/bin/bash')
        self.fixture_process(proc, 361, 313, [m.BINARY, 'server'], m.BINARY, json.dumps(m.DIRECT_RULES))
        original = pathlib.Path.stat
        def checked(path, *args, **kwargs):
            if path == foreign:
                return mock.Mock(st_uid=os.getuid() + 1)
            return original(path, *args, **kwargs)
        with mock.patch.object(pathlib.Path, 'stat', autospec=True, side_effect=checked):
            self.assertEqual(m.owned_tunnel(proc)['pid'], 361)

    def test_duplicate_managed_processes_rejected(self):
        proc = self.root / 'proc'
        self.fixture_process(proc, 313, 1, m.SUPERVISOR_ARGV, '/usr/bin/bash')
        for pid in (361, 362):
            self.fixture_process(proc, pid, 313, [m.BINARY, 'server'], m.BINARY)
        with self.assertRaisesRegex(RuntimeError, 'Multiple'):
            m.owned_tunnel(proc)

    def test_absent_child_waits_for_supervisor_instead_of_launching(self):
        self.select()
        with mock.patch.object(m, 'ensure_launcher', return_value=False), mock.patch.object(m, 'owned_tunnel', return_value=None), mock.patch.object(m.os, 'kill') as kill:
            self.assertEqual(m.ensure_direct_browser_egress()['state'], 'waiting-for-supervisor')
            kill.assert_not_called()

    def test_direct_child_left_running_and_does_not_claim_navigation_proof(self):
        self.select()
        with mock.patch.object(m, 'ensure_launcher', return_value=False), mock.patch.object(m, 'owned_tunnel', return_value={'pid': 361, 'rules': 'direct'}), mock.patch.object(m.os, 'kill') as kill:
            state = m.ensure_direct_browser_egress()
            self.assertEqual(state['state'], 'direct')
            self.assertFalse(state['browserNavigationVerified'])
            kill.assert_not_called()

    def test_invalid_plain_text_none_is_not_a_direct_policy(self):
        proc = self.root / 'proc'
        self.fixture_process(proc, 313, 1, m.SUPERVISOR_ARGV, '/usr/bin/bash')
        self.fixture_process(proc, 361, 313, [m.BINARY, 'server'], m.BINARY, 'none')
        self.assertEqual(m.owned_tunnel(proc)['rules'], 'other')

    def test_transient_none_launcher_is_upgraded_to_actual_json_rules(self):
        self.launcher.write_text(m.LEGACY_NONE_LAUNCHER)
        with mock.patch.object(m, 'install_launcher') as install:
            self.assertTrue(m.ensure_launcher())
            install.assert_called_once_with(m.LEGACY_NONE_LAUNCHER)

    def test_only_same_managed_child_signalled_for_native_supervised_restart(self):
        self.select()
        child = {'pid': 361, 'rules': None, 'startTime': '1234'}
        with mock.patch.object(m, 'ensure_launcher', return_value=True), mock.patch.object(m, 'owned_tunnel', return_value=child), mock.patch.object(m.os, 'kill') as kill:
            state = m.ensure_direct_browser_egress()
            self.assertEqual(state['state'], 'restart-requested')
            kill.assert_called_once_with(361, signal.SIGTERM)

    def test_recycled_pid_not_signalled(self):
        self.select()
        child = {'pid': 361, 'rules': None, 'startTime': '1234'}
        recycled = {**child, 'startTime': '5678'}
        with mock.patch.object(m, 'ensure_launcher', return_value=False), mock.patch.object(m, 'owned_tunnel', side_effect=[child, recycled]), mock.patch.object(m.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'changed before'):
                m.ensure_direct_browser_egress()
            kill.assert_not_called()

    def test_changed_selection_not_signalled(self):
        child = {'pid': 361, 'rules': None, 'startTime': '1234'}
        with mock.patch.object(m, 'direct_selected', side_effect=[True, False]), mock.patch.object(m, 'ensure_launcher', return_value=False), mock.patch.object(m, 'owned_tunnel', return_value=child), mock.patch.object(m.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'changed before'):
                m.ensure_direct_browser_egress()
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
