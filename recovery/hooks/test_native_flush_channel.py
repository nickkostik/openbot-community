import contextlib
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location('flush_recovery', pathlib.Path(__file__).with_name('computer-recovery.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
namespace = {'__name__': 'test_flush_preparation'}
exec(m.FLUSH_CHANNEL_PREPARE, namespace)
prepare = namespace['prepare_channel']


class NativeFlushChannelTests(unittest.TestCase):
    def prepare(self, parent):
        return prepare(parent, os.getuid(), os.getgid(), parent_uid=os.getuid())

    def test_create_once_preserves_existing_contents_owner_and_mode(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root)
            self.assertEqual(self.prepare(parent), {'state': 'created'})
            channel = parent / 'sand-box-store-flush'
            self.assertEqual(stat.S_IMODE(channel.stat().st_mode), 0o700)
            self.assertEqual(channel.stat().st_uid, os.getuid())
            (channel / 'keep').write_text('KEEP')
            channel.chmod(0o750)
            before = channel.stat()
            self.assertEqual(self.prepare(parent), {'state': 'existing'})
            self.assertEqual((channel / 'keep').read_text(), 'KEEP')
            self.assertEqual(channel.stat().st_ino, before.st_ino)
            self.assertEqual(channel.stat().st_mode, before.st_mode)

    def test_symlinked_parent_or_channel_cannot_modify_the_target(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root)
            real = parent / 'real'; real.mkdir()
            link = parent / 'link'; link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError): self.prepare(link)
            (parent / 'sand-box-store-flush').symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError): self.prepare(parent)
            self.assertEqual(list(real.iterdir()), [])

    def test_shared_parent_is_refused_before_any_creation(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root); parent.chmod(0o777)
            with self.assertRaisesRegex(RuntimeError, 'Untrusted'): self.prepare(parent)
            self.assertEqual(list(parent.iterdir()), [])

    def test_failed_ownership_change_removes_only_the_new_empty_directory(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root)
            with mock.patch('os.fchown', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError): self.prepare(parent)
            self.assertEqual(list(parent.iterdir()), [])

    def test_file_winning_the_creation_race_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root); target = parent / 'sand-box-store-flush'
            def race(*args, **kwargs):
                target.write_text('OTHER WRITER')
                raise FileExistsError()
            with mock.patch('os.mkdir', side_effect=race):
                with self.assertRaises(OSError): self.prepare(parent)
            self.assertEqual(target.read_text(), 'OTHER WRITER')

    def test_foreign_existing_channel_is_not_chowned_or_removed(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root); target = parent / 'sand-box-store-flush'; target.mkdir()
            real_fstat = os.fstat
            def inspect(fd):
                info = real_fstat(fd)
                if info.st_ino == target.stat().st_ino:
                    return SimpleNamespace(st_uid=max(os.getuid(), 0) + 10000)
                return info
            with mock.patch('os.fstat', side_effect=inspect), mock.patch('os.fchown') as chown:
                with self.assertRaisesRegex(RuntimeError, 'Foreign'): self.prepare(parent)
                chown.assert_not_called()
            self.assertTrue(target.is_dir())

    def test_unknown_native_contract_does_not_invoke_sudo(self):
        with tempfile.TemporaryDirectory() as root:
            host = pathlib.Path(root) / 'host.cjs'
            for source in ('unknown vendor', '\n'.join(m.FLUSH_CHANNEL_CONTRACT) * 2):
                host.write_text(source)
                with mock.patch.object(m, 'HOST', host), mock.patch.object(m.subprocess, 'run') as run:
                    self.assertEqual(m.ensure_native_flush_channel(), {'state': 'unsupported-host'})
                    run.assert_not_called()

    def test_provisioning_uses_only_fixed_root_path_and_rechecks_as_host_user(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root); host = parent / 'host.cjs'
            host.write_text('\n'.join(m.FLUSH_CHANNEL_CONTRACT)); channel = parent / 'sand-box-store-flush'
            def sudo(args, **kwargs):
                self.assertEqual(args[:5], ['sudo', '-n', '/usr/bin/python3', '-c', m.FLUSH_CHANNEL_PREPARE])
                self.assertEqual(args[5:], [str(os.getuid()), str(os.getgid())])
                self.assertIn("prepare_channel('/run', uid, gid)", args[4])
                channel.mkdir(mode=0o700)
                return SimpleNamespace(stdout='{"state":"created"}')
            with mock.patch.object(m, 'HOST', host), mock.patch.object(m, 'FLUSH_CHANNEL_DIR', channel), \
                    mock.patch.object(m.subprocess, 'run', side_effect=sudo) as run:
                self.assertEqual(m.ensure_native_flush_channel(), {'state': 'created'})
                self.assertEqual(m.ensure_native_flush_channel(), {'state': 'existing'})
                self.assertEqual(run.call_count, 1)

    def test_inaccessible_or_symlinked_existing_channel_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            parent = pathlib.Path(root); host = parent / 'host.cjs'
            host.write_text('\n'.join(m.FLUSH_CHANNEL_CONTRACT)); channel = parent / 'sand-box-store-flush'
            real = parent / 'real'; real.mkdir(); channel.symlink_to(real)
            with mock.patch.object(m, 'HOST', host), mock.patch.object(m, 'FLUSH_CHANNEL_DIR', channel), \
                    mock.patch.object(m.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'symlinked'): m.ensure_native_flush_channel()
                channel.unlink(); channel.mkdir()
                with mock.patch.object(m.os, 'access', return_value=False):
                    with self.assertRaisesRegex(RuntimeError, 'not accessible'): m.ensure_native_flush_channel()
                run.assert_not_called()
                self.assertTrue(channel.is_dir())

    def test_prepared_channel_arms_one_durable_idle_reload_in_either_mode(self):
        for mode in ('official', 'custom'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root, contextlib.ExitStack() as stack:
                sd = pathlib.Path(root); host = sd / 'host.cjs'; host.write_text('NATIVE')
                (sd / 'openbot-mode').write_text(mode); (sd / 'openbot-local-only.enable').touch()
                stack.enter_context(mock.patch.object(m, 'SD', sd)); stack.enter_context(mock.patch.object(m, 'HOST', host))
                names = ('ensure_named_tunnel', 'ensure_openbot_service', 'ensure_browser_egress', 'ensure_bot_context',
                         'ensure_retired_windows', 'ensure_window_readiness', 'ensure_upstream_sync', 'check_server_roster',
                         'ensure_custom_dispatch', 'repair_local_profiles')
                for name in names: stack.enter_context(mock.patch.object(m, name, return_value={'state': 'test'}))
                stack.enter_context(mock.patch.object(m, 'local_engine', return_value=SimpleNamespace(summary=lambda _: {})))
                stack.enter_context(mock.patch.object(m, 'restore_official_stock', return_value=[]))
                stack.enter_context(mock.patch.object(m, 'repair_official_store_status', return_value=False))
                stack.enter_context(mock.patch.object(m, 'repair_bundle', return_value=False))
                repair = stack.enter_context(mock.patch.object(m, 'ensure_backup_permissions', return_value={
                    'nativeFlushChannel': {'state': 'created'}}))
                stack.enter_context(mock.patch.object(m, 'host_pids', return_value=[123]))
                status = stack.enter_context(mock.patch.object(m, 'gateway_status', return_value={'isBusy': True}))
                kill = stack.enter_context(mock.patch.object(m.os, 'kill'))
                state = m.tick(); key = 'pendingOfficialStockReload' if mode == 'official' else 'pendingHostReload'
                self.assertTrue(state[key]); self.assertTrue(json.loads((sd / 'computer-recovery-state.json').read_text())[key])
                kill.assert_not_called(); repair.return_value = {'nativeFlushChannel': {'state': 'existing'}}
                status.return_value = {'isBusy': False}; m.tick(); m.tick()
                kill.assert_called_once_with(123, m.signal.SIGTERM)
                self.assertEqual(host.read_text(), 'NATIVE'); self.assertEqual((sd / 'openbot-mode').read_text(), mode)


if __name__ == '__main__': unittest.main()
