import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location('native_engine_dependencies', pathlib.Path(__file__).with_name('native-engine-dependencies.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NativeDependencyRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        root = pathlib.Path(root); sd = root / 'sd'; sd.mkdir(mode=0o700)
        (sd / 'openbot-mode').write_text('custom'); (sd / 'openbot-local-only.enable').touch()
        source = root / 'daemon'; source.mkdir(mode=0o755)
        target = root / 'native'; target.mkdir(mode=0o755)
        for name, version in m.PACKAGES.items():
            folder = source / name; folder.mkdir(mode=0o755)
            (folder / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
        raw = b'TEST RETAINED BOX RUNNER'; (sd / 'stock.cjs').write_bytes(raw)
        policy = {'stockSha256': hashlib.sha256(raw).hexdigest()}
        engine = SimpleNamespace(policy=lambda _: dict(policy), artifact_directory=lambda *_: sd,
                                 owned_bytes=lambda p, _: p.read_bytes(), MAX_BYTES=1024,
                                 require_runner=lambda s: self.assertEqual(s, raw.decode()))
        return sd, source, target, engine

    def repair(self, values, **kwargs):
        sd, source, target, engine = values
        return m.repair(sd, engine, source=source, target=target, vendor_owners={os.getuid()},
                        probe=kwargs.pop('probe', lambda *_: None), **kwargs)

    def test_update_removed_links_restore_once_without_changing_data(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); sd, source, target, _ = values
            before = {p.name: p.read_bytes() for p in sd.iterdir()}
            self.assertEqual(self.repair(values)['state'], 'validated')
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(self.repair(values, apply=True)['state'], 'repaired')
            for name in m.PACKAGES:
                self.assertEqual(os.readlink(target / name), str(source / name))
            self.assertEqual(self.repair(values, apply=True)['state'], 'present')
            self.assertEqual(before, {p.name: p.read_bytes() for p in sd.iterdir()})

    def test_official_or_unselected_does_not_inspect_or_change_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); sd, _, target, engine = values
            (sd / 'openbot-mode').write_text('official')
            engine.policy = mock.Mock(side_effect=AssertionError('Official engine accessed'))
            self.assertEqual(self.repair(values, apply=True)['state'], 'native-dependencies')
            self.assertEqual(list(target.iterdir()), [])

    def test_corrupt_engine_or_wrong_package_version_refuses_before_links(self):
        for fault in ('engine', 'version', 'source-link', 'writable'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as root:
                values = self.fixture(root); sd, source, target, _ = values
                if fault == 'engine': (sd / 'stock.cjs').write_bytes(b'UNREVIEWED')
                elif fault == 'version':
                    (source / 'tree-sitter/package.json').write_text('{"name":"tree-sitter","version":"1"}')
                elif fault == 'source-link':
                    (source / 'tree-sitter/package.json').rename(source / 'original.json')
                    (source / 'tree-sitter/package.json').symlink_to(source / 'original.json')
                else: (source / 'tree-sitter').chmod(0o777)
                with self.assertRaises((OSError, RuntimeError)):
                    self.repair(values, apply=True)
                self.assertEqual(list(target.iterdir()), [])

    def test_native_addon_probe_failure_refuses_before_links(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root)
            with self.assertRaises(RuntimeError):
                self.repair(values, apply=True, probe=mock.Mock(side_effect=RuntimeError('ABI mismatch')))
            self.assertEqual(list(values[2].iterdir()), [])

    def test_existing_vendor_module_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); _, _, target, _ = values
            (target / 'tree-sitter').mkdir(); sentinel = target / 'tree-sitter/vendor'; sentinel.write_text('NEW VENDOR')
            self.assertEqual(self.repair(values, apply=True)['packages'], ['tree-sitter-bash'])
            self.assertEqual(sentinel.read_text(), 'NEW VENDOR')

    def test_concurrent_installer_never_overwritten_and_own_partial_link_rolled_back(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); _, _, target, _ = values
            original = os.link
            def race(src, dst, **kwargs):
                if dst == 'tree-sitter-bash':
                    (target / dst).mkdir()
                return original(src, dst, **kwargs)
            with mock.patch.object(m.os, 'link', side_effect=race):
                with self.assertRaises(FileExistsError): self.repair(values, apply=True)
            self.assertFalse(os.path.lexists(target / 'tree-sitter'))
            self.assertTrue((target / 'tree-sitter-bash').is_dir())

    def test_mid_publication_mode_change_rolls_back_only_own_links(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); sd, _, target, _ = values; original = os.link
            def switch(*args, **kwargs):
                result = original(*args, **kwargs); (sd / 'openbot-mode').write_text('official'); return result
            with mock.patch.object(m.os, 'link', side_effect=switch):
                with self.assertRaises(RuntimeError): self.repair(values, apply=True)
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual((sd / 'openbot-mode').read_text(), 'official')

    def test_replacement_after_publication_is_never_recorded_as_owned(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); sd, _, target, _ = values; original = os.link
            def replace_after_link(src, dst, **kwargs):
                result = original(src, dst, **kwargs)
                (target / dst).unlink(); (target / dst).write_text('CONCURRENT INSTALLER')
                (sd / 'openbot-mode').write_text('official')
                return result
            with mock.patch.object(m.os, 'link', side_effect=replace_after_link):
                with self.assertRaises(RuntimeError): self.repair(values, apply=True)
            self.assertEqual((target / 'tree-sitter').read_text(), 'CONCURRENT INSTALLER')
            self.assertEqual(list(target.glob('.openbot-native-dependency-*')), [])

    def test_foreign_target_directory_symlink_refused(self):
        with tempfile.TemporaryDirectory() as root:
            values = self.fixture(root); _, _, target, _ = values
            foreign = target.with_name('foreign'); target.rename(foreign); target.symlink_to(foreign)
            with self.assertRaises(RuntimeError): self.repair(values, apply=True)
            self.assertEqual(list(foreign.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
