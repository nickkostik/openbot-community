import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import pathlib
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('tailscale_recovery', pathlib.Path(__file__).with_name('tailscale-recovery.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def package(version='1.104.1', link=False):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as archive:
        for name in ['tailscale', 'tailscaled']:
            entry = tarfile.TarInfo('tailscale_' + version + '_amd64/' + name)
            content = (name + ' fixture').encode()
            if link and name == 'tailscaled':
                entry.type = tarfile.SYMTYPE; entry.linkname = '/bin/sh'
                archive.addfile(entry)
            else:
                entry.size = len(content); archive.addfile(entry, io.BytesIO(content))
    return out.getvalue()


class TailscaleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def installer(self, raw, digest=None):
        def download(url, limit):
            return (digest or hashlib.sha256(raw).hexdigest()).encode() if url.endswith('.sha256') else raw
        return download

    def test_checked_package_survives_a_new_process_and_detects_binary_damage(self):
        manifest = mod.install(self.root, '1.104.1', 'amd64', self.installer(package()))
        fresh = json.loads((self.root / 'current.json').read_text())
        self.assertEqual(fresh, manifest)
        self.assertEqual(mod.executable(self.root, fresh, 'tailscale').stat().st_mode & 0o777, 0o700)
        (self.root / 'versions/1.104.1/tailscaled').write_bytes(b'damaged')
        with self.assertRaisesRegex(RuntimeError, 'binary changed'):
            mod.executable(self.root, fresh, 'tailscaled')

    def test_bad_checksum_preserves_previous_authority(self):
        first = mod.install(self.root, '1.104.1', 'amd64', self.installer(package()))
        with self.assertRaisesRegex(RuntimeError, 'checksum differs'):
            mod.install(self.root, '1.104.2', 'amd64', self.installer(package('1.104.2'), '0' * 64))
        self.assertEqual(json.loads((self.root / 'current.json').read_text()), first)

    def test_link_entries_never_publish_a_manifest_or_execute(self):
        with self.assertRaisesRegex(RuntimeError, 'executable entry'):
            mod.install(self.root, '1.104.1', 'amd64', self.installer(package(link=True)))
        self.assertFalse((self.root / 'current.json').exists())
        self.assertFalse((self.root / 'versions/1.104.1/tailscale').exists())

    def test_version_selector_cannot_escape_the_package_root(self):
        for version in ['../outside', '1.2.3/../../x', '1.2.3?key=value']:
            with self.assertRaisesRegex(RuntimeError, 'selector'):
                mod.install(self.root, version, 'amd64', lambda *args: self.fail('network called'))

    def test_latest_version_is_numeric_and_architecture_specific(self):
        listing = b'tailscale_1.99.0_amd64.tgz tailscale_1.104.1_amd64.tgz tailscale_2.0.0_arm64.tgz'
        self.assertEqual(mod.latest_version('amd64', lambda *args: listing), '1.104.1')

    def test_optional_install_does_not_require_local_only_or_custom(self):
        for mode in ['official', 'custom', 'temporal']:
            (self.root / 'openbot-mode').write_text(mode)
            self.assertEqual(mod.ensure_network(self.root), {'state': 'disabled'})
        (self.root / 'openbot-tailscale.json').write_text(json.dumps({'schemaVersion': 1, 'enabled': False}))
        self.assertEqual(mod.ensure_network(self.root), {'state': 'disabled'})

    def test_symlinked_enable_flag_is_rejected(self):
        outside = self.root / 'elsewhere.json'; outside.write_text('{}')
        (self.root / 'openbot-tailscale.json').symlink_to(outside)
        with self.assertRaises(OSError):
            mod.ensure_network(self.root)

    def test_nonobject_settings_and_manifest_are_rejected(self):
        flag = self.root / 'openbot-tailscale.json'
        for invalid in ['null', '[]', '"text"']:
            flag.write_text(invalid)
            with self.assertRaisesRegex(RuntimeError, 'settings'):
                mod.ensure_network(self.root)
        flag.write_text('{"schemaVersion":1,"enabled":true}')
        folder = self.root / 'tailscale'; folder.mkdir()
        (folder / 'current.json').write_text('null')
        with self.assertRaisesRegex(RuntimeError, 'manifest'):
            mod.ensure_network(self.root)

    def test_pid_for_an_unrelated_process_is_not_adopted(self):
        (self.root / 'daemon.pid').write_text(str(__import__('os').getpid()))
        with self.assertRaisesRegex(RuntimeError, 'another process'):
            mod.live_pid(self.root)

    def test_exit_node_never_claims_to_route_os_traffic_in_userspace(self):
        settings = {'schemaVersion': 1, 'enabled': True, 'exitNodeIP': '100.64.0.2'}
        with self.assertRaisesRegex(RuntimeError, 'requires kernel'):
            mod.network_settings(settings)
        settings['networking'] = 'kernel'
        self.assertEqual(mod.network_settings(settings), settings)

    def test_exit_address_and_flags_cannot_inject_a_cli_argument(self):
        for address in ['--reset', 'pve; reboot', '192.0.2.100', 'https://pve/', 1682233215]:
            with self.assertRaises(RuntimeError):
                mod.network_settings({'schemaVersion': 1, 'enabled': True, 'networking': 'kernel', 'exitNodeIP': address})
        with self.assertRaises(RuntimeError):
            mod.network_settings({'schemaVersion': 1, 'enabled': True, 'exitNodeAllowLANAccess': 'true'})

    def test_kernel_daemon_restart_preserves_state_socket_and_user_identity(self):
        (self.root / 'openbot-tailscale.json').write_text(json.dumps({
            'schemaVersion': 1, 'enabled': True, 'networking': 'kernel', 'exitNodeIP': '100.64.0.2'}))
        folder = self.root / 'tailscale'; folder.mkdir()
        mod.install(folder, '1.104.1', 'amd64', self.installer(package()))
        with mock.patch.object(mod, 'live_pid', return_value=None), \
             mock.patch.object(mod, 'ensure_kernel_capabilities') as capabilities, \
             mock.patch.object(mod.subprocess, 'Popen', return_value=SimpleNamespace(pid=1234)) as start:
            result = mod.ensure_network(self.root)
        self.assertEqual(result['networking'], 'kernel')
        capabilities.assert_called_once_with(folder / 'versions/1.104.1/tailscaled')
        argv = start.call_args.args[0]
        self.assertIn('--tun=openbot-ts0', argv)
        self.assertIn('--state=' + str(folder / 'state/tailscaled.state'), argv)
        self.assertIn('--socket=' + str(folder / 'state/tailscaled.sock'), argv)
        self.assertNotIn('/usr/bin/sudo', argv)

    def test_unapproved_exit_node_is_not_selected(self):
        status = {'BackendState': 'Running', 'Peer': {'pve': {
            'ID': 'pve-id', 'TailscaleIPs': ['100.64.0.2'], 'ExitNodeOption': False}}}
        with mock.patch.object(mod.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'not approved'):
                mod.ensure_exit_node(['tailscale'], status, {'exitNodeIP': '100.64.0.2'})
        command.assert_not_called()

    def test_restart_waits_for_control_plane_peer_list_instead_of_crashing(self):
        with mock.patch.object(mod.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'not approved'):
                mod.ensure_exit_node(['tailscale'], {'BackendState': 'Running', 'Peer': None}, {'exitNodeIP': '100.64.0.2'})
        command.assert_not_called()

    def test_exit_selection_preserves_dns_routes_and_ssh_preferences(self):
        status = {'BackendState': 'Running', 'Peer': {'pve': {
            'ID': 'pve-id', 'TailscaleIPs': ['100.64.0.2'], 'ExitNodeOption': True}}}
        selected = {'BackendState': 'Running', 'ExitNodeStatus': {'ID': 'pve-id', 'TailscaleIPs': ['100.64.0.2']}}
        with mock.patch.object(mod, 'cli_json', side_effect=[{'ExitNodeID': ''}, selected]), \
             mock.patch.object(mod.subprocess, 'run') as command:
            result = mod.ensure_exit_node(['tailscale', '--socket=/persistent.sock'], status, {'exitNodeIP': '100.64.0.2'})
        self.assertEqual(result, selected)
        self.assertEqual(command.call_args.args[0], ['tailscale', '--socket=/persistent.sock',
            'set', '--exit-node=100.64.0.2', '--exit-node-allow-lan-access=true'])

    def test_same_exit_node_does_not_rewrite_preferences_each_tick(self):
        status = {'BackendState': 'Running', 'Peer': {'pve': {
            'ID': 'pve-id', 'TailscaleIPs': ['100.64.0.2'], 'ExitNodeOption': True}}}
        with mock.patch.object(mod, 'cli_json', return_value={'ExitNodeID': 'pve-id', 'ExitNodeAllowLANAccess': True}), \
             mock.patch.object(mod.subprocess, 'run') as command:
            self.assertIs(mod.ensure_exit_node(['tailscale'], status, {'exitNodeIP': '100.64.0.2'}), status)
        command.assert_not_called()

    def test_capability_setup_must_be_verified_before_start(self):
        daemon = pathlib.Path('/owned/verified/tailscaled')
        with mock.patch.object(mod.pathlib.Path, 'exists', return_value=True), \
             mock.patch.object(mod.pathlib.Path, 'read_text', return_value='NoNewPrivs:\t0\n'), \
             mock.patch.object(mod.subprocess, 'run', return_value=SimpleNamespace(stdout='')):
            with self.assertRaisesRegex(RuntimeError, 'did not persist'):
                mod.ensure_kernel_capabilities(daemon)

    def test_non_dumpable_daemon_identity_is_read_without_adopting_root_processes(self):
        identity = {'exe': str(self.root / 'versions/1.104.1/tailscaled'),
                    'argv': ['tailscaled', '--state=' + str(self.root / 'state/tailscaled.state')],
                    'uid': __import__('os').getuid()}
        (self.root / 'daemon.pid').write_text('1234')
        with mock.patch.object(mod.pathlib.Path, 'resolve', side_effect=PermissionError), \
             mock.patch.object(mod.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(identity))) as command:
            self.assertEqual(mod.live_pid(self.root), 1234)
        self.assertEqual(command.call_args.args[0][-1], '1234')
        identity['uid'] = __import__('os').getuid() + 1
        with mock.patch.object(mod, 'process_identity', return_value=identity):
            with self.assertRaisesRegex(RuntimeError, 'another process'):
                mod.live_pid(self.root)

    def test_exit_during_privileged_pid_probe_is_a_clean_restart(self):
        (self.root / 'daemon.pid').write_text('1234')
        with mock.patch.object(mod.pathlib.Path, 'resolve', side_effect=PermissionError), \
             mock.patch.object(mod.subprocess, 'run', return_value=SimpleNamespace(stdout='null')):
            self.assertIsNone(mod.live_pid(self.root))

    def test_exit_status_prefixes_are_recognized_as_the_selected_peer(self):
        self.assertEqual(mod.selected_exit_ips({'ExitNodeStatus': {'ID': 'pve', 'TailscaleIPs': [
            '100.64.0.2/32', 'fd7a:115c:a1e0::2/128']}}),
            ['100.64.0.2', 'fd7a:115c:a1e0::2'])
        self.assertEqual(mod.selected_exit_ips({'ExitNodeStatus': {'ID': 'pve'}, 'Peer': {
            'other': {'ID': 'other', 'TailscaleIPs': ['100.64.0.2']},
            'pve': {'ID': 'pve', 'TailscaleIPs': ['100.64.0.2']}}}), ['100.64.0.2'])

    def test_host_transport_exceptions_cannot_replace_public_internet_routing(self):
        for cidr in ['0.0.0.0/0', '1.1.1.0/24', '100.64.0.0/10', '198.18.0.1/15', '--reset']:
            with self.assertRaises(RuntimeError):
                mod.network_settings({'schemaVersion': 1, 'enabled': True, 'hostTransportCIDRs': [cidr]})
        settings = {'schemaVersion': 1, 'enabled': True, 'hostTransportCIDRs': ['198.18.0.0/15']}
        self.assertEqual(mod.network_settings(settings), settings)

    def test_host_proxy_route_is_preserved_idempotently_without_dns_changes(self):
        rules = [{'priority': 5100, 'dst': '198.18.0.0', 'dstlen': 15, 'table': 'main'}]
        with mock.patch.object(mod.subprocess, 'check_output', return_value=json.dumps(rules)), \
             mock.patch.object(mod.subprocess, 'run') as command:
            mod.ensure_host_transport({'hostTransportCIDRs': ['198.18.0.0/15']})
        command.assert_not_called()
        with mock.patch.object(mod.subprocess, 'check_output', return_value='[]'), \
             mock.patch.object(mod.subprocess, 'run') as command:
            mod.ensure_host_transport({'hostTransportCIDRs': ['198.18.0.0/15']})
        self.assertEqual(command.call_args.args[0][-9:], ['/usr/sbin/ip', '-4', 'rule', 'add',
            'priority', '5100', 'to', '198.18.0.0/15', 'lookup', 'main'][-9:])

    def test_host_transport_never_overwrites_an_unrelated_existing_rule(self):
        rules = [{'priority': 5100, 'dst': '192.168.0.0/16', 'table': 'main'}]
        with mock.patch.object(mod.subprocess, 'check_output', return_value=json.dumps(rules)), \
             mock.patch.object(mod.subprocess, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'already occupied'):
                mod.ensure_host_transport({'hostTransportCIDRs': ['198.18.0.0/15']})
        command.assert_not_called()

    def select_watch(self):
        policy = self.root / 'openbot-tailscale.json'
        policy.write_text('{"schemaVersion":1,"enabled":true,"autoUpdate":false}')
        return policy

    def test_network_watcher_starts_without_mode_or_local_only_and_ignores_stale_pid(self):
        before = self.select_watch().read_bytes()
        folder = self.root / 'tailscale'; folder.mkdir()
        (folder / 'recovery-watch.pid').write_text('1\n')
        with mock.patch.object(mod.subprocess, 'Popen', return_value=SimpleNamespace(pid=987)) as start:
            result = mod.ensure_network_watcher(self.root)
        self.assertEqual(result, {'state': 'started', 'pid': 987})
        self.assertEqual(start.call_args.args[0], ['/usr/bin/python3', str(pathlib.Path(mod.__file__).absolute()),
            '--sand-data', str(self.root), '--watch'])
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        self.assertEqual((self.root / 'openbot-tailscale.json').read_bytes(), before)
        self.assertFalse((self.root / 'openbot-mode').exists())
        self.assertFalse((self.root / 'openbot-local-only.enable').exists())

    def test_live_network_watcher_lock_prevents_duplicate_bootstrap_spawn(self):
        self.select_watch(); folder = self.root / 'tailscale'; folder.mkdir()
        with (folder / 'recovery-watch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(mod.subprocess, 'Popen') as start:
                self.assertEqual(mod.ensure_network_watcher(self.root), {'state': 'running'})
                start.assert_not_called()

    def test_network_opt_out_never_creates_watch_state_or_spawns(self):
        for policy in [None, '{"schemaVersion":1,"enabled":false}']:
            if policy is not None:
                (self.root / 'openbot-tailscale.json').write_text(policy)
            with mock.patch.object(mod.subprocess, 'Popen') as start:
                self.assertEqual(mod.ensure_network_watcher(self.root), {'state': 'disabled'})
                start.assert_not_called()
            self.assertFalse((self.root / 'tailscale').exists())

    def test_symlinked_watcher_lock_log_and_data_root_never_spawn(self):
        self.select_watch(); folder = self.root / 'tailscale'; folder.mkdir()
        target = self.root / 'untouched'; target.write_text('unchanged')
        for name in ['recovery-watch.lock', 'recovery-watch.log']:
            path = folder / name
            path.symlink_to(target)
            with mock.patch.object(mod.subprocess, 'Popen') as start:
                with self.assertRaises(OSError):
                    mod.ensure_network_watcher(self.root)
                start.assert_not_called()
            path.unlink()
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'data directory'):
            mod.ensure_network_watcher(alias)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_watch_retries_transient_failure_preserves_policy_and_exits_on_opt_out(self):
        policy = self.select_watch(); before = policy.read_bytes()
        states = [subprocess.CalledProcessError(1, ['cli'], output='credential must not appear'),
                  {'state': 'started', 'pid': 42}, {'state': 'Running', 'pid': 42}, {'state': 'disabled'}]
        output = io.StringIO()
        with mock.patch.object(mod, 'ensure_network', side_effect=states) as ensure, \
             mock.patch.object(mod.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            self.assertEqual(mod.watch_network(self.root), {'state': 'disabled'})
        self.assertEqual(ensure.call_count, 4)
        self.assertEqual(sleep.call_args_list, [mock.call(mod.WATCH_INTERVAL)] * 3)
        self.assertNotIn('credential must not appear', output.getvalue())
        folder = self.root / 'tailscale'
        final = json.loads((folder / 'recovery-watch-state.json').read_text())
        self.assertEqual(final['state'], 'disabled')
        self.assertEqual(final['watcherPid'], os.getpid())
        self.assertGreater(final['checkedAtMs'], 0)
        self.assertEqual((folder / 'recovery-watch-state.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(policy.read_bytes(), before)
        fd = mod.watcher_lock(folder); os.close(fd)

    def test_in_progress_recovery_is_retried_without_claiming_a_failure(self):
        self.select_watch()
        with mock.patch.object(mod, 'ensure_network', side_effect=[BlockingIOError(), {'state': 'disabled'}]), \
             mock.patch.object(mod.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
            mod.watch_network(self.root)
        self.assertIn('recovery-in-progress', output.getvalue())
        self.assertNotIn('errorClass', output.getvalue())

    def test_real_processes_have_one_watcher_and_release_ownership_after_exit(self):
        # A missing local manifest fails before any download, daemon or network
        # action. Real processes still exercise lifetime locking and receipts.
        self.select_watch()
        argv = [sys.executable, mod.__file__, '--sand-data', str(self.root), '--watch']
        children = [subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
        try:
            deadline = time.monotonic() + 5
            state_path = self.root / 'tailscale/recovery-watch-state.json'
            while time.monotonic() < deadline:
                if state_path.exists() and sum(child.poll() is None for child in children) == 1:
                    break
                time.sleep(0.01)
            self.assertEqual(sum(child.poll() is None for child in children), 1)
            state = json.loads(state_path.read_text())
            winner = next(child for child in children if child.poll() is None)
            self.assertEqual(state['watcherPid'], winner.pid)
            self.assertEqual(state['errorClass'], 'FileNotFoundError')
            self.assertEqual(mod.ensure_network_watcher(self.root), {'state': 'running'})
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
        fd = mod.watcher_lock(self.root / 'tailscale'); os.close(fd)


if __name__ == '__main__':
    unittest.main()
