import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('migrate_message_context',
    pathlib.Path(__file__).with_name('migrate-message-context.py'))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MessagingContextMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / 'imessage-context'
        self.source.mkdir()
        self.target = self.root / migration.TARGET
        (self.source / 'nick-voice.md').write_text('Private user guidance fixture\n')
        (self.source / 'armed-state.json').write_text(json.dumps({'armed': [], 'expires': 1}))
        (self.source / 'inbox').mkdir()
        (self.source / 'inbox' / 'cursor.json').write_text(json.dumps({'lastRead': 13}))

    def test_dry_run_does_not_create_or_move_anything(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        result = migration.migrate(self.root)
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['files'], 3)
        self.assertFalse(self.source.is_symlink())
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_one_authoritative_directory_preserves_arming_cursors_and_file_identity(self):
        files = {str(p.relative_to(self.source)): (p.stat().st_ino, p.read_bytes())
                 for p in self.source.rglob('*') if p.is_file()}
        directory_inode = self.source.stat().st_ino
        result = migration.migrate(self.root, apply=True)
        self.assertEqual(result['status'], 'migrated')
        self.assertFalse(result['dataCopied'])
        self.assertEqual(self.source.resolve(), self.target)
        self.assertEqual(self.target.stat().st_ino, directory_inode)
        for relative, (inode, data) in files.items():
            self.assertEqual((self.target / relative).stat().st_ino, inode)
            self.assertEqual((self.target / relative).read_bytes(), data)
        (self.source / 'inbox/cursor.json').write_text('{"lastRead": 14}')
        self.assertEqual(json.loads((self.target / 'inbox/cursor.json').read_text())['lastRead'], 14)
        self.assertEqual(migration.migrate(self.root, apply=True)['status'], 'unchanged')

    def test_two_real_directories_are_never_merged_or_overwritten(self):
        self.target.mkdir(parents=True)
        (self.target / 'armed-state.json').write_text('{"armed": ["other-state"]}')
        with self.assertRaisesRegex(RuntimeError, 'Two context directories'):
            migration.migrate(self.root, apply=True)
        self.assertEqual(json.loads((self.source / 'armed-state.json').read_text())['armed'], [])
        self.assertEqual(json.loads((self.target / 'armed-state.json').read_text())['armed'], ['other-state'])

    def test_foreign_alias_symlink_child_credentials_and_database_are_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        for name in ['credentials.json', 'private.db', '.env', 'auth']:
            with self.subTest(name=name):
                path = self.source / name
                if name == 'auth':
                    path.mkdir()
                else:
                    path.write_text('fixture')
                with self.assertRaises(RuntimeError):
                    migration.migrate(self.root, apply=True)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
        link = self.source / 'other.json'
        link.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'symlink or special'):
            migration.migrate(self.root, apply=True)
        link.unlink()
        for child in self.source.iterdir():
            if child.is_file():
                child.unlink()
            else:
                (child / 'cursor.json').unlink()
                child.rmdir()
        self.source.rmdir()
        self.source.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'unexpected destination'):
            migration.migrate(self.root, apply=True)

    def test_failed_alias_creation_rolls_back_without_losing_data(self):
        original = os.symlink
        def fail_final(target, destination, *args, **kwargs):
            if pathlib.Path(destination) == self.source:
                raise OSError('Injected alias creation failure')
            return original(target, destination, *args, **kwargs)
        with mock.patch.object(migration.os, 'symlink', side_effect=fail_final):
            with self.assertRaisesRegex(OSError, 'Injected'):
                migration.migrate(self.root, apply=True)
        self.assertTrue(self.source.is_dir())
        self.assertFalse(self.source.is_symlink())
        self.assertFalse(self.target.exists())
        self.assertEqual(json.loads((self.source / 'inbox/cursor.json').read_text())['lastRead'], 13)
        self.assertFalse(list(self.root.glob('.imessage-context-alias-*')))

    def test_destination_claimed_during_move_is_not_overwritten(self):
        original = migration.rename_no_replace
        claimed = []
        def race(source, target):
            self.target.mkdir()
            claimed.append(self.target.stat().st_ino)
            return original(source, target)
        with mock.patch.object(migration, 'rename_no_replace', side_effect=race):
            with self.assertRaises(FileExistsError):
                migration.migrate(self.root, apply=True)
        self.assertEqual(self.target.stat().st_ino, claimed[0])
        self.assertTrue(self.source.is_dir())
        self.assertFalse(self.source.is_symlink())
        self.assertEqual(json.loads((self.source / 'inbox/cursor.json').read_text())['lastRead'], 13)

    def test_concurrent_legacy_writer_is_preserved_without_an_unsafe_rollback(self):
        original = os.symlink
        def race(target, destination, *args, **kwargs):
            if pathlib.Path(destination) == self.source:
                self.source.mkdir()
                (self.source / 'cursor.json').write_text('{"lastRead": 99}')
            return original(target, destination, *args, **kwargs)
        with mock.patch.object(migration.os, 'symlink', side_effect=race):
            with self.assertRaises(FileExistsError):
                migration.migrate(self.root, apply=True)
        self.assertEqual(json.loads((self.source / 'cursor.json').read_text())['lastRead'], 99)
        self.assertEqual(json.loads((self.target / 'inbox/cursor.json').read_text())['lastRead'], 13)
        with self.assertRaisesRegex(RuntimeError, 'Two context directories'):
            migration.migrate(self.root, apply=True)

    def test_interrupted_move_repairs_alias_only_with_matching_private_provenance(self):
        self.target.parent.mkdir(parents=True)
        info = self.source.stat()
        os.rename(self.source, self.target)
        with self.assertRaisesRegex(RuntimeError, 'without migration provenance'):
            migration.migrate(self.root, apply=True)
        snapshots = self.root / 'recovery-snapshots'
        snapshots.mkdir()
        marker = snapshots / 'imessage-context-migration.json'
        marker.write_text(json.dumps({'version': 1, 'source': str(self.source),
            'target': str(self.target), 'sourceDevice': info.st_dev,
            'sourceInode': info.st_ino, 'phase': 'prepared'}))
        marker.chmod(0o600)
        self.assertEqual(migration.migrate(self.root, apply=True)['status'], 'alias-repaired')
        self.assertEqual(self.source.resolve(), self.target)
        self.assertEqual(json.loads(marker.read_text())['phase'], 'complete')

    def test_snapshot_retains_one_context_alias_and_private_provenance(self):
        migration.migrate(self.root, apply=True)
        snapshot = self.root / 'snapshot'
        snapshot.mkdir()
        shutil.copytree(self.target, snapshot / migration.TARGET)
        marker = self.root / 'recovery-snapshots/imessage-context-migration.json'
        self.assertTrue(migration.snapshot_compatibility_state(self.root, snapshot))
        self.assertEqual((snapshot / 'imessage-context').resolve(), snapshot / migration.TARGET)
        self.assertEqual(os.readlink(snapshot / 'imessage-context'), str(migration.TARGET))
        copied = snapshot / 'recovery-snapshots' / marker.name
        self.assertEqual(copied.read_bytes(), marker.read_bytes())
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        self.assertEqual(migration.migrate(snapshot)['status'], 'unchanged')
        self.assertEqual(json.loads((snapshot / 'imessage-context/armed-state.json').read_text())['armed'], [])

    def test_snapshot_without_canonical_data_refuses_a_dangling_compatibility_alias(self):
        migration.migrate(self.root, apply=True)
        snapshot = self.root / 'snapshot'
        snapshot.mkdir()
        with self.assertRaises(FileNotFoundError):
            migration.snapshot_compatibility_state(self.root, snapshot)
        self.assertFalse((snapshot / 'imessage-context').exists())
        self.assertFalse((snapshot / 'imessage-context').is_symlink())


if __name__ == '__main__':
    unittest.main()
