import fcntl
import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('recovery_bootstrap', pathlib.Path(__file__).with_name('recovery-bootstrap.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = pathlib.Path(self.folder.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(module, 'FACTORY_SAND_DATA', self.root, create=True).start()
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        mock.patch.object(module, 'PROC', self.proc, create=True).start()
        mock.patch.object(module, 'HERE', self.root / 'copied-bootstrap', create=True).start()
        mock.patch.dict(os.environ, {}, clear=True).start()
        (self.root / 'openbot-local-only.enable').touch()
        (self.root / 'openbot-mode').write_text('custom\n')
        (self.root / 'openbot-hooks').mkdir()
        (self.root / 'openbot-hooks/computer-recovery.py').write_text('# owned helper\n')

    def test_live_lock_prevents_duplicate_watcher_even_with_missing_pidfile(self):
        with (self.root / 'computer-recovery-watch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(module.subprocess, 'Popen') as spawn:
                self.assertEqual(module.ensure_watcher(self.root), {'state': 'starting'})
                spawn.assert_not_called()

    def test_stale_pid_does_not_prevent_restart_and_official_is_also_supported(self):
        (self.root / 'openbot-local-only.enable').write_text('Owner-selected local recovery. ' * 5)
        (self.root / 'computer-recovery.pid').write_text('1\n')
        (self.root / 'openbot-mode').write_text('official\n')
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
            self.assertEqual(module.ensure_watcher(self.root), {'state': 'started', 'pid': 42})
            self.assertEqual(spawn.call_args.args[0], ['/usr/bin/python3', str(self.root / 'openbot-hooks/computer-recovery.py'), '--host', '/opt/sand/sand-host/host-main.cjs', '--watch'])
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            # The child can claim the watcher lock immediately.
            with (self.root / 'computer-recovery-watch.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_opt_out_and_unknown_mode_never_start_or_change_configuration(self):
        before = (self.root / 'openbot-mode').read_bytes()
        (self.root / 'openbot-local-only.enable').unlink()
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            self.assertEqual(module.ensure_watcher(self.root), {'state': 'disabled'})
            spawn.assert_not_called()
        self.assertEqual((self.root / 'openbot-mode').read_bytes(), before)
        (self.root / 'openbot-local-only.enable').touch()
        (self.root / 'openbot-mode').write_text('unknown')
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            self.assertEqual(module.ensure_watcher(self.root), {'state': 'disabled'})
            spawn.assert_not_called()

    def test_symlinked_helper_lock_and_log_are_refused(self):
        for target in ['openbot-hooks/computer-recovery.py', 'computer-recovery-watch.lock', 'computer-recovery-watch.log']:
            with self.subTest(target=target):
                path = self.root / target
                original = path.read_bytes() if path.exists() else None
                path.unlink(missing_ok=True)
                path.symlink_to(self.root / 'openbot-mode')
                with mock.patch.object(module.subprocess, 'Popen') as spawn:
                    with self.assertRaises(OSError):
                        module.ensure_watcher(self.root)
                    spawn.assert_not_called()
                path.unlink()
                if original is not None:
                    path.write_bytes(original)

    def test_symlinked_helper_parent_cannot_redirect_execution(self):
        directory = self.root / 'openbot-hooks'
        alternate = self.root / 'other-hooks'
        directory.rename(alternate)
        directory.symlink_to(alternate, target_is_directory=True)
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'helper directory'):
                module.ensure_watcher(self.root)
            spawn.assert_not_called()

    def test_network_recovers_without_a_local_harness_or_mode_flag(self):
        (self.root / 'openbot-local-only.enable').unlink()
        (self.root / 'openbot-mode').unlink()
        (self.root / 'openbot-tailscale.json').write_text('{"schemaVersion":1,"enabled":true}')
        (self.root / 'openbot-hooks/tailscale-recovery.py').write_text(
            'def ensure_network(root): return {"state":"Running"}\n'
            'def ensure_network_watcher(root): return {"state":"disabled"}\n')
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            self.assertEqual(module.bootstrap(self.root), {'state': 'disabled', 'network': {'state': 'Running'}})
            spawn.assert_not_called()

    def test_first_network_failure_still_starts_independent_retry_watcher(self):
        (self.root / 'openbot-local-only.enable').unlink()
        (self.root / 'openbot-mode').unlink()
        (self.root / 'openbot-tailscale.json').write_text('{"schemaVersion":1,"enabled":true}')
        (self.root / 'openbot-hooks/tailscale-recovery.py').write_text(
            'def ensure_network(root): raise OSError("private command output")\n'
            'def ensure_network_watcher(root): return {"state":"started","pid":43}\n')
        result = module.bootstrap(self.root)
        self.assertEqual(result, {'state': 'disabled', 'network': {
            'state': 'failed', 'errorClass': 'OSError', 'watcher': {'state': 'started', 'pid': 43}}})

    def test_network_lock_contention_keeps_watcher_and_is_not_a_failed_boot(self):
        (self.root / 'openbot-local-only.enable').unlink()
        (self.root / 'openbot-tailscale.json').write_text('{"schemaVersion":1,"enabled":true}')
        (self.root / 'openbot-hooks/tailscale-recovery.py').write_text(
            'def ensure_network(root): raise BlockingIOError()\n'
            'def ensure_network_watcher(root): return {"state":"running"}\n')
        self.assertEqual(module.bootstrap(self.root), {'state': 'disabled', 'network': {
            'state': 'recovery-in-progress', 'watcher': {'state': 'running'}}})

    def test_optional_network_failure_does_not_block_local_recovery(self):
        with mock.patch.object(module, 'ensure_optional_network', side_effect=AttributeError('invalid optional settings')):
            with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=43)):
                result = module.bootstrap(self.root)
        self.assertEqual(result['state'], 'started')
        self.assertEqual(result['network'], {'state': 'failed', 'errorClass': 'AttributeError'})

    def package(self, root=None):
        package = root or self.root / 'openbot'
        (package / 'recovery/hooks').mkdir(parents=True)
        (package / 'src/ui').mkdir(parents=True)
        (package / 'src/cli.ts').write_text('// installed CLI\n')
        (package / 'src/ui/server.ts').write_text('// installed UI\n')
        return package

    def process(self, pid, argv):
        directory = self.proc / str(pid)
        directory.mkdir()
        (directory / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in argv) + b'\0')
        return directory

    def watcher(self, argv, pid=42):
        (self.root / 'computer-recovery.pid').write_text(str(pid) + '\n')
        return self.process(pid, argv)

    def test_fresh_installed_package_starts_without_copied_hooks(self):
        package = self.package()
        helper = package / 'recovery/hooks/computer-recovery.py'
        helper.write_text('# packaged helper\n')
        (self.root / 'openbot-hooks/computer-recovery.py').unlink()
        (self.root / 'openbot-hooks').rmdir()
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
            self.assertEqual(module.bootstrap(self.root), {'state': 'started', 'pid': 42})
        self.assertEqual(spawn.call_args.args[0], ['/usr/bin/python3', str(helper), '--host', '/opt/sand/sand-host/host-main.cjs', '--watch'])

    def test_running_checkout_package_is_preferred_over_installed_and_copied_helpers(self):
        checkout = self.package(self.root / 'checkout')
        installed = self.package()
        helper = checkout / 'recovery/hooks/computer-recovery.py'
        helper.write_text('# checkout helper\n')
        (installed / 'recovery/hooks/computer-recovery.py').write_text('# installed helper\n')
        with mock.patch.object(module, 'HERE', checkout / 'recovery/hooks'):
            with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
                module.ensure_watcher(self.root)
        self.assertEqual(spawn.call_args.args[0][1], str(helper))

    def test_packaged_network_retry_starts_without_local_mode_or_copied_hooks(self):
        package = self.package()
        (self.root / 'openbot-local-only.enable').unlink()
        (self.root / 'openbot-mode').unlink()
        (self.root / 'openbot-tailscale.json').write_text('{"schemaVersion":1,"enabled":true}')
        (package / 'recovery/hooks/tailscale-recovery.py').write_text(
            'def ensure_network(root): raise OSError("not connected yet")\n'
            'def ensure_network_watcher(root): return {"state":"started","pid":43}\n')
        result = module.bootstrap(self.root)
        self.assertEqual(result, {'state': 'disabled', 'network': {
            'state': 'failed', 'errorClass': 'OSError', 'watcher': {'state': 'started', 'pid': 43}}})

    def test_explicit_legacy_host_is_forwarded_to_the_child(self):
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
            module.bootstrap(self.root, host='/home/box/sand-host/host-main.cjs')
        self.assertEqual(spawn.call_args.args[0][-3:], ['--host', '/home/box/sand-host/host-main.cjs', '--watch'])

    def test_host_environment_override_is_forwarded(self):
        with mock.patch.dict(os.environ, {'OPENBOT_HOST_MAIN': '/srv/native/host-main.cjs'}):
            with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
                module.ensure_watcher(self.root)
        self.assertEqual(spawn.call_args.args[0][-3:], ['--host', '/srv/native/host-main.cjs', '--watch'])

    def test_explicit_host_takes_precedence_over_environment(self):
        with mock.patch.dict(os.environ, {'OPENBOT_HOST_MAIN': '/srv/unused/host-main.cjs'}):
            with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
                module.ensure_watcher(self.root, host='/srv/native/host-main.cjs')
        self.assertEqual(spawn.call_args.args[0][-3:], ['--host', '/srv/native/host-main.cjs', '--watch'])

    def test_owned_live_native_host_is_selected_before_path_default(self):
        self.process(51, ['/exec-daemon/node', '--disable-warning=ExperimentalWarning', '/home/box/sand-host/host-main.cjs'])
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
            module.ensure_watcher(self.root)
        self.assertEqual(spawn.call_args.args[0][-3:], ['--host', '/home/box/sand-host/host-main.cjs', '--watch'])

    def test_syntax_probes_and_other_scripts_cannot_select_native_host(self):
        for pid, args in enumerate([
            ['/exec-daemon/node', '--check', '/home/box/sand-host/host-main.cjs'],
            ['/usr/bin/node', '/srv/other-script.cjs', '/home/box/sand-host/host-main.cjs'],
            ['/usr/bin/node', '--eval', 'console.log(1)', '/home/box/sand-host/host-main.cjs'],
        ], 51):
            self.process(pid, args)
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=42)) as spawn:
            module.ensure_watcher(self.root)
        self.assertEqual(spawn.call_args.args[0][-3:], ['--host', '/opt/sand/sand-host/host-main.cjs', '--watch'])

    def test_ambiguous_native_hosts_require_explicit_selection(self):
        self.process(51, ['/exec-daemon/node', '/home/box/sand-host/host-main.cjs'])
        self.process(52, ['/exec-daemon/node', '/opt/sand/sand-host/host-main.cjs'])
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                module.ensure_watcher(self.root)
            spawn.assert_not_called()

    def test_invalid_host_never_starts_a_watcher(self):
        for host in ['relative/host.cjs', '/srv/../other/host.cjs', '/srv/host\n.cjs', '/srv/host\r.cjs', '/srv/host\0.cjs']:
            with self.subTest(host=host):
                with mock.patch.object(module.subprocess, 'Popen') as spawn:
                    with self.assertRaises(ValueError):
                        module.ensure_watcher(self.root, host=host)
                    spawn.assert_not_called()

    def test_nonfactory_local_root_is_refused_but_its_network_recovery_still_runs(self):
        (self.root / 'openbot-tailscale.json').write_text('{"schemaVersion":1,"enabled":true}')
        (self.root / 'openbot-hooks/tailscale-recovery.py').write_text(
            'def ensure_network(root): return {"state":"Running"}\n'
            'def ensure_network_watcher(root): return {"state":"running"}\n')
        with mock.patch.object(module, 'FACTORY_SAND_DATA', self.root / 'factory'):
            with mock.patch.object(module.subprocess, 'Popen') as spawn:
                self.assertEqual(module.bootstrap(self.root), {'state': 'unsupported-path', 'network': {
                    'state': 'Running', 'watcher': {'state': 'running'}}})
                spawn.assert_not_called()
        self.assertFalse((self.root / 'factory').exists())

    def test_verified_process_and_lock_report_running_without_duplicate(self):
        helper = self.root / 'openbot-hooks/computer-recovery.py'
        self.watcher(['/usr/bin/python3', str(helper), '--host', '/opt/sand/sand-host/host-main.cjs', '--watch'])
        with (self.root / 'computer-recovery-watch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(module.subprocess, 'Popen') as spawn:
                self.assertEqual(module.ensure_watcher(self.root), {'state': 'running'})
                spawn.assert_not_called()

    def test_legacy_daemon_is_adopted_only_for_its_default_host(self):
        helper = self.root / 'openbot-hooks/computer-recovery.py'
        self.watcher(['/usr/bin/python3', str(helper), '--watch'])
        with (self.root / 'computer-recovery-watch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(module.subprocess, 'Popen') as spawn:
                self.assertEqual(module.ensure_watcher(self.root), {'state': 'running'})
                self.assertEqual(module.ensure_watcher(self.root, host='/home/box/sand-host/host-main.cjs'), {'state': 'conflict'})
                spawn.assert_not_called()

    def test_owned_watcher_with_wrong_host_or_extra_args_is_a_conflict(self):
        helper = self.root / 'openbot-hooks/computer-recovery.py'
        for argv in [
            ['/usr/bin/python3', str(helper), '--host', '/srv/other/host.cjs', '--watch'],
            ['/usr/bin/python3', str(helper), '--host', '/opt/sand/sand-host/host-main.cjs', '--watch', '--unknown'],
        ]:
            with self.subTest(argv=argv):
                directory = self.watcher(argv)
                with mock.patch.object(module.subprocess, 'Popen') as spawn:
                    self.assertEqual(module.ensure_watcher(self.root), {'state': 'conflict'})
                    spawn.assert_not_called()
                (directory / 'cmdline').unlink()
                directory.rmdir()

    def test_matching_process_before_lifetime_lock_claim_reports_starting(self):
        helper = self.root / 'openbot-hooks/computer-recovery.py'
        self.watcher(['/usr/bin/python3', str(helper), '--host', '/opt/sand/sand-host/host-main.cjs', '--watch'])
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            self.assertEqual(module.ensure_watcher(self.root), {'state': 'starting'})
            spawn.assert_not_called()

    def test_recycled_foreign_pid_cannot_be_adopted(self):
        self.watcher(['/usr/bin/python3', '/srv/foreign.py', '--watch'])
        with mock.patch.object(module.subprocess, 'Popen', return_value=mock.Mock(pid=43)) as spawn:
            self.assertEqual(module.ensure_watcher(self.root), {'state': 'started', 'pid': 43})
            spawn.assert_called_once()

    def test_symlinked_preferred_package_is_refused_even_with_a_legacy_helper(self):
        package = self.package(self.root / 'other-package')
        (package / 'recovery/hooks/computer-recovery.py').write_text('# package helper\n')
        (self.root / 'openbot').symlink_to(package, target_is_directory=True)
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'helper directory'):
                module.ensure_watcher(self.root)
            spawn.assert_not_called()

    def test_symlinked_preferred_packaged_helper_is_refused(self):
        package = self.package()
        (package / 'recovery/hooks/computer-recovery.py').symlink_to(self.root / 'openbot-hooks/computer-recovery.py')
        with mock.patch.object(module.subprocess, 'Popen') as spawn:
            with self.assertRaises(OSError):
                module.ensure_watcher(self.root)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
