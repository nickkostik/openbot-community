import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


HOOK = pathlib.Path(__file__).with_name('computer-recovery.py')


class ServiceProbeRecoveryTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('service_probe_recovery', HOOK)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.module.SD = self.root / 'sand-data'
        self.module.SD.mkdir()
        self.module.HERE = self.module.SD / 'openbot-hooks'
        self.pid = 123
        self.ui = self.module.SD / 'openbot/src/ui/server.ts'
        for name in ['openbot/src/cli.ts', 'openbot/src/ui/server.ts', 'node22/bin/node']:
            path = self.module.SD / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        (self.module.SD / 'openbot-ui.pid').write_text(str(self.pid))
        self.proc = self.root / 'proc'
        self.directory = self.proc / str(self.pid)
        (self.directory / 'fd').mkdir(parents=True)
        (self.proc / 'net').mkdir()
        self.write_identity()
        self.write_socket()

    def write_identity(self, script=None, start='900', state='S'):
        script = script or self.ui
        (self.directory / 'cmdline').write_bytes(b'node\0' + os.fsencode(script) + b'\0')
        fields = [state] + ['0'] * 19
        fields[19] = start
        (self.directory / 'stat').write_text(str(self.pid) + ' (ui worker (one)) ' + ' '.join(fields))

    def write_socket(self, address='0100007F:2440', state='0A', inode='900'):
        (self.proc / 'net/tcp').write_text(
            'sl local_address rem_address st queues timer retrans uid timeout inode\n'
            '0: ' + address + ' 00000000:0000 ' + state +
            ' 00000000:00000000 00:00000000 00000000 1000 0 ' + inode + '\n')
        link = self.directory / 'fd/3'
        link.unlink(missing_ok=True)
        link.symlink_to('socket:[' + inode + ']')

    def owns_listener(self):
        return self.module.owned_ui_listener(self.pid, self.ui, self.proc)

    def test_owned_exact_loopback_listener(self):
        self.assertTrue(self.owns_listener())

    def test_wrong_port_or_wildcard_or_established_socket_is_not_ownership(self):
        for address, state in [('0100007F:2441', '0A'), ('00000000:2440', '0A'), ('0100007F:2440', '01')]:
            with self.subTest(address=address, state=state):
                self.write_socket(address=address, state=state)
                self.assertFalse(self.owns_listener())

    def test_listener_owned_by_another_process(self):
        (self.directory / 'fd/3').unlink()
        (self.directory / 'fd/3').symlink_to('socket:[901]')
        self.assertFalse(self.owns_listener())

    def test_command_must_match_exact_script_argument(self):
        self.write_identity(script=str(self.ui) + '.foreign')
        self.assertFalse(self.owns_listener())

    def test_foreign_uid(self):
        with mock.patch.object(self.module.os, 'getuid', return_value=os.getuid() + 1):
            self.assertFalse(self.owns_listener())

    def test_missing_or_zombie_or_invalid_process_is_not_owned(self):
        self.write_identity(state='Z')
        self.assertFalse(self.owns_listener())
        (self.directory / 'stat').write_text('invalid')
        self.assertFalse(self.owns_listener())
        (self.directory / 'cmdline').unlink()
        self.assertFalse(self.owns_listener())
        self.assertFalse(self.module.owned_ui_listener(0, self.ui, self.proc))

    def test_pid_reuse_during_inspection(self):
        original = pathlib.Path.read_text
        calls = 0

        def read(path, *args, **kwargs):
            nonlocal calls
            if path == self.directory / 'stat':
                calls += 1
                if calls == 2:
                    self.write_identity(start='901')
            return original(path, *args, **kwargs)

        with mock.patch.object(pathlib.Path, 'read_text', new=read):
            self.assertFalse(self.owns_listener())

    def test_permission_failure_does_not_authorize_reinstall(self):
        with mock.patch.object(self.module, 'owned_ui_listener', side_effect=PermissionError), \
                mock.patch.object(self.module.subprocess, 'run') as install:
            with self.assertRaises(PermissionError):
                self.module.ensure_openbot_service()
            install.assert_not_called()

    def run_probe(self, response=None, error=None):
        ownership = self.module.owned_ui_listener
        with mock.patch.object(self.module, 'owned_ui_listener',
                               side_effect=lambda pid, ui: ownership(pid, ui, self.proc)), \
                mock.patch.object(self.module.urllib.request, 'urlopen', side_effect=error) as http, \
                mock.patch.object(self.module.subprocess, 'run') as install:
            if error is None:
                http.return_value.__enter__.return_value.status = 200
                http.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            result = self.module.ensure_openbot_service()
            install.assert_not_called()
            return result

    def test_healthy_api_preserves_listener(self):
        self.assertEqual(self.run_probe({'paused': False}), {'state': 'running', 'pid': self.pid})

    def test_slow_or_reset_probe_never_reinstalls_the_owned_listener(self):
        for error in [TimeoutError('slow event loop'), ConnectionResetError('reset')]:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.run_probe(error=error),
                                 {'state': 'degraded', 'pid': self.pid, 'reason': 'health-probe-unavailable'})

    def test_invalid_response_does_not_claim_health_or_restart(self):
        self.assertEqual(self.run_probe({'paused': 'invalid'}),
                         {'state': 'degraded', 'pid': self.pid, 'reason': 'health-probe-unavailable'})

    def test_dead_or_unbound_owner_uses_unified_reconcile(self):
        for owns in [False, None]:
            with self.subTest(owns=owns):
                (self.module.SD / 'openbot-ui.pid').write_text(str(self.pid) if owns is False else 'stale')
                result = SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'hostBounce': 'none'}))
                with mock.patch.object(self.module, 'owned_ui_listener', return_value=False), \
                        mock.patch.object(self.module.urllib.request, 'urlopen') as http, \
                        mock.patch.object(self.module.subprocess, 'run', return_value=result) as install:
                    self.assertEqual(self.module.ensure_openbot_service(),
                                     {'state': 'reconciled', 'hostReloadDeferred': False})
                    http.assert_not_called()
                    self.assertIn('--defer-host-bounce', install.call_args.args[0])
                    self.assertIn('install', install.call_args.args[0])

    def test_process_exit_during_probe_returns_to_reconcile(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'hostBounce': 'none'}))
        with mock.patch.object(self.module, 'owned_ui_listener', side_effect=[True, False]), \
                mock.patch.object(self.module.urllib.request, 'urlopen', side_effect=ConnectionResetError), \
                mock.patch.object(self.module.subprocess, 'run', return_value=result) as install:
            self.assertEqual(self.module.ensure_openbot_service()['state'], 'reconciled')
            install.assert_called_once()

    def test_foreign_listener_refusal_remains_a_failure(self):
        result = SimpleNamespace(returncode=1, stdout=json.dumps({'ok': False, 'error': 'foreign-hop'}))
        with mock.patch.object(self.module, 'owned_ui_listener', return_value=False), \
                mock.patch.object(self.module.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'refused service recovery'):
                self.module.ensure_openbot_service()


if __name__ == '__main__':
    unittest.main()
