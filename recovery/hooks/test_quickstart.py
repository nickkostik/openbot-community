"""Execute the real shell preflight; installation is an observable stand-in."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[2] / 'quickstart.sh'


@unittest.skipUnless(os.name == 'posix', 'Computer quickstart requires POSIX')
class QuickstartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='openbot-quickstart-')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.root.chmod(0o755)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o755)
        shutil.copyfile(SOURCE, self.source / 'quickstart.sh')
        (self.source / 'quickstart.sh').chmod(0o755)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        self.host = self.root / 'host-main.cjs'
        self.host.write_text('/* A presence fixture, not a native runner. */\n')
        self.host.chmod(0o644)
        self.uid, self.gid = os.getuid(), os.getgid()
        if self.uid == 0:
            import pwd
            nobody = pwd.getpwnam('nobody')
            self.uid, self.gid = nobody.pw_uid, nobody.pw_gid
            try:
                os.chown(self.data, self.uid, self.gid)
            except PermissionError:
                self.skipTest('Cannot run owner checks as a non-root Computer user')
        (self.source / 'install.sh').write_text('''#!/usr/bin/env bash
set -eu
python3 - "$@" <<'PY'
import json,os,pathlib,sys
root=pathlib.Path(os.environ['OPENBOT_SAND_DATA'])
with (root/'installer-calls.jsonl').open('a') as stream:
 stream.write(json.dumps({'args':sys.argv[1:],'tunnel':os.environ.get('OPENBOT_TUNNEL'),'defer':os.environ.get('OPENBOT_DEFER_HOST_BOUNCE')})+'\\n')
PY
''')
        (self.source / 'install.sh').chmod(0o755)
        self.env = {**os.environ, 'PATH': '/usr/bin:/bin',
                    'OPENBOT_SAND_DATA': str(self.data), 'OPENBOT_HOST_MAIN': str(self.host)}
        for name in ('OPENBOT_TUNNEL', 'OPENBOT_DEFER_HOST_BOUNCE'):
            self.env.pop(name, None)

    def write(self, name, text):
        path = self.data / name
        path.write_text(text)
        path.chmod(0o600)
        if os.getuid() == 0:
            os.chown(path, self.uid, self.gid)
        return path

    def run_quickstart(self, *args):
        def drop_owner():
            if os.getuid() == 0:
                os.setgroups([])
                os.setgid(self.gid)
                os.setuid(self.uid)
        return subprocess.run(['bash', str(self.source / 'quickstart.sh'), *args],
                              env=self.env, capture_output=True, text=True,
                              preexec_fn=drop_owner, timeout=15)

    def calls(self):
        path = self.data / 'installer-calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_default_check_is_read_only_and_reports_public_exposure(self):
        self.write('openbot-expose', 'cloudflare-quick\n')
        self.write('openbot-mode', 'official\n')
        before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        result = self.run_quickstart()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['ready'])
        self.assertFalse(report['loopbackInstallAllowed'])
        self.assertEqual(report['savedExposure'], 'cloudflare-quick')
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()}, before)
        self.assertEqual(self.calls(), [])

    def test_loopback_install_does_not_disable_an_existing_public_tunnel(self):
        for value in ('cloudflare-quick\n', ' Cloudflare\n'):
            with self.subTest(value=value):
                exposure = self.write('openbot-expose', value)
                mode = self.write('openbot-mode', 'official\n')
                result = self.run_quickstart('--install')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('existing public tunnel', result.stderr)
                self.assertEqual(exposure.read_text(), value)
                self.assertEqual(mode.read_text(), 'official\n')
                self.assertEqual(self.calls(), [])

    def test_loopback_install_defers_and_finalizes_without_replacing_settings(self):
        self.write('openbot-expose', 'loopback\n')
        self.write('openbot-mode', 'custom\n')
        self.write('openbot-bot-models.json', '{"models":{},"reasoning":{}}\n')
        before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        result = self.run_quickstart('--install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            {'args': [], 'tunnel': 'off', 'defer': '1'},
            {'args': ['--bot-finalize'], 'tunnel': 'off', 'defer': '1'}])
        for name, value in before.items():
            self.assertEqual((self.data / name).read_bytes(), value)

    def test_explicit_public_install_uses_the_detached_path(self):
        self.write('openbot-expose', 'cloudflare-quick\n')
        result = self.run_quickstart('--public-tunnel')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [{'args': ['--bot-mode'], 'tunnel': None, 'defer': None}])

    def test_invalid_saved_exposure_refuses_every_action_without_echoing_it(self):
        private_value = 'unknown-private-value'
        path = self.write('openbot-expose', private_value)
        for action in ('--check', '--install', '--public-tunnel'):
            with self.subTest(action=action):
                result = self.run_quickstart(action)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn(private_value, result.stdout + result.stderr)
                self.assertFalse(json.loads(result.stdout)['exposureReadable'])
        self.assertEqual(path.read_text(), private_value)
        self.assertEqual(self.calls(), [])

    def test_symlinked_or_oversized_saved_exposure_never_starts_installation(self):
        target = self.write('other', 'loopback\n')
        exposure = self.data / 'openbot-expose'
        exposure.symlink_to(target.name)
        result = self.run_quickstart('--install')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(exposure.is_symlink())
        exposure.unlink()
        self.write('openbot-expose', ' ' * 4097)
        result = self.run_quickstart('--install')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unknown_or_extra_arguments_refuse_before_the_installer(self):
        for args in [('--unknown',), ('--install', '--public-tunnel')]:
            with self.subTest(args=args):
                result = self.run_quickstart(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Usage:', result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
