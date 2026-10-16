"""Regressions for shared bot context without exposing the private host store."""
import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('bot_context', pathlib.Path(__file__).with_name('publish-bot-context.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = '00000000-0000-4000-8000-000000000001'
B = '00000000-0000-4000-8000-000000000002'
C = '00000000-0000-4000-8000-000000000003'


def row(aid=A, **fields):
    return {'id': aid, 'name': 'Existing bot', 'harness': 'box', 'isHiddenFromSidebar': False, **fields}


class ProjectionTests(unittest.TestCase):
    def test_only_identity_fields_are_exported_and_active_copies_are_included(self):
        source = [{'id': A, 'name': 'Stale name', 'token': 'NEVER EXPORT', 'previousIds': [C]}]
        native = [row(description='PRIVATE ROLE', apiKey='PRIVATE KEY', serverId='123'), row(B, name='New active copy')]
        snapshot = copy.deepcopy((source, native))
        result, omitted = M.roster_projection(source, [{'newId': B, 'phase': 'active'}], native)
        self.assertEqual(result, [
            {'id': A, 'name': 'Existing bot', 'harness': 'box', 'isGroup': False, 'memberIds': []},
            {'id': B, 'name': 'New active copy', 'harness': 'box', 'isGroup': False, 'memberIds': []}])
        self.assertEqual(omitted, [])
        self.assertEqual((source, native), snapshot)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_archived_peers_are_omitted_and_no_unrelated_native_bots_are_added(self):
        result, omitted = M.roster_projection([{'id': A}, {'id': B}], [],
            [row(), row(B, isHiddenFromSidebar=True), row(C, name='Unrelated probe')])
        self.assertEqual([r['id'] for r in result], [A])
        self.assertEqual(omitted, [B])

    def test_active_copy_refuses_missing_hidden_and_temporal_native_records(self):
        for rows in ([], [row(isHiddenFromSidebar=True)], [row(harness='temporal')]):
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, 'active copy'):
                M.roster_projection([], [{'newId': A, 'phase': 'active'}], rows)

    def test_ambiguous_or_malformed_identity_refuses_publication(self):
        cases = [([{'id': A}, {'id': A}], [], [row()]),
                 ([{'id': 'not-a-bot'}], [], [row()]),
                 ([{'id': A}], [], [row(), row()]),
                 ([], [{'newId': A, 'phase': 'active'}] * 2, [row()])]
        for args in cases:
            with self.subTest(args=args), self.assertRaises((RuntimeError, ValueError)):
                M.roster_projection(*args)

    def test_room_members_are_real_explicit_box_peers(self):
        result, _ = M.roster_projection([{'id': A}], [], [row(isGroup=True, memberIds=[B]), row(B)])
        self.assertEqual(result[0]['memberIds'], [B])
        for members, peers in (([], []), ([B, B], [row(B)]), ([B], []),
                               ([B], [row(B, harness='temporal')]),
                               ([B], [row(B, isHiddenFromSidebar=True)])):
            with self.subTest(members=members), self.assertRaises(RuntimeError):
                M.roster_projection([{'id': A}], [], [row(isGroup=True, memberIds=members), *peers])

    def test_non_group_does_not_inherit_leftover_members(self):
        result, _ = M.roster_projection([{'id': A, 'isGroup': True, 'memberIds': [B]}], [],
                                        [row(isGroup=False, memberIds=[B])])
        self.assertFalse(result[0]['isGroup'])
        self.assertEqual(result[0]['memberIds'], [])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sd = pathlib.Path(temporary.name)
        patch = mock.patch.object(M, 'SD', self.sd)
        patch.start()
        self.addCleanup(patch.stop)
        (self.sd / 'agents' / A).mkdir(parents=True)
        (self.sd / 'openbot-recovery').mkdir()
        (self.sd / 'recovery-snapshots').mkdir()
        self.source = self.sd / 'openbot-recovery/roster.json'
        self.source.write_text(json.dumps([{'id': A, 'name': 'Role'}]))
        (self.sd / 'openbot-box-remint.json').write_text(json.dumps({'entries': [{'newId': A, 'phase': 'active'}]}))
        self.path = self.sd / 'agents' / A / 'profile.json'
        self.profile = {'name': 'Keep this name', 'harness': 'box', 'serverId': '123',
                        'description': 'Keep my specialist role. ' + M.OLD_PATHS[0],
                        'custom': {'model': 'route', 'effort': 'max'}}
        self.path.write_text(json.dumps(self.profile))
        self.calls = []

    def gateway(self, method, body=None):
        self.calls.append((method, copy.deepcopy(body)))
        if method == 'listAgents':
            return [row()]
        if method == 'updateAgent':
            saved = json.loads(self.path.read_text())
            saved.update(body['profile'])
            self.path.write_text(json.dumps(saved))
            return {'id': A}
        self.fail('Unexpected mutation or message: ' + method)

    def test_publish_is_a_fixed_point_and_keeps_private_inputs_unchanged(self):
        before = self.source.read_bytes()
        profile = self.path.read_bytes()
        with mock.patch.object(M, 'native', side_effect=self.gateway):
            first = M.publish()
            destination = self.sd / M.PUBLIC_RELATIVE
            snapshot = destination.stat().st_mtime_ns
            second = M.publish()
        self.assertEqual(first['status'], 'published')
        self.assertEqual(second['status'], 'unchanged')
        self.assertEqual(destination.stat().st_mtime_ns, snapshot)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), profile)
        self.assertTrue(all(method == 'listAgents' for method, _ in self.calls))

    def test_private_and_model_readable_locations_are_separate_real_files(self):
        with mock.patch.object(M, 'native', side_effect=self.gateway):
            M.publish()
        destination = self.sd / M.PUBLIC_RELATIVE
        self.assertFalse(destination.is_symlink())
        self.assertNotEqual(destination.resolve(), self.source.resolve())
        self.assertEqual(destination.relative_to(self.sd).parts[0], 'workflows')

    def test_destination_directory_and_file_symlinks_are_refused(self):
        workflows = self.sd / 'workflows'
        workflows.symlink_to(self.sd / 'openbot-recovery', target_is_directory=True)
        with mock.patch.object(M, 'native', side_effect=self.gateway), self.assertRaises(RuntimeError):
            M.publish()
        workflows.unlink()
        (workflows / 'openbot-fleet').mkdir(parents=True)
        destination = self.sd / M.PUBLIC_RELATIVE
        destination.symlink_to(self.source)
        before = self.source.read_bytes()
        with mock.patch.object(M, 'native', side_effect=self.gateway), self.assertRaises(RuntimeError):
            M.publish()
        self.assertEqual(self.source.read_bytes(), before)

    def test_failed_native_validation_does_not_replace_a_published_roster(self):
        with mock.patch.object(M, 'native', side_effect=self.gateway):
            M.publish()
        destination = self.sd / M.PUBLIC_RELATIVE
        before = destination.read_bytes()
        with mock.patch.object(M, 'native', return_value=[]), self.assertRaises(RuntimeError):
            M.publish()
        self.assertEqual(destination.read_bytes(), before)

    def test_compare_and_set_preserves_concurrent_edits(self):
        destination = self.sd / 'temporary.json'
        destination.write_bytes(b'Concurrent result')
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            M.atomic_owned_file(destination, b'Overwrite', b'Old result')
        self.assertEqual(destination.read_bytes(), b'Concurrent result')
        self.assertEqual(list(self.sd.glob('.bot-context-*')), [])

    def test_explicit_notice_update_preserves_role_identity_and_overrides(self):
        with mock.patch.object(M, 'native', side_effect=self.gateway):
            first = M.update_instructions()
            after = json.loads(self.path.read_text())
            second = M.update_instructions()
        self.assertEqual({k: v for k, v in after.items() if k != 'description'},
                         {k: v for k, v in self.profile.items() if k != 'description'})
        self.assertIn('Keep my specialist role.', after['description'])
        self.assertIn(M.PUBLIC_PATH, after['description'])
        self.assertNotIn(M.OLD_PATHS[0], after['description'])
        self.assertEqual(after['description'].count(M.START), 1)
        self.assertEqual(first['updatedInstructions'], [A])
        self.assertEqual(second['updatedInstructions'], [])
        backup = pathlib.Path(first['privateBackup']) / (A + '.json')
        self.assertEqual(json.loads(backup.read_bytes()), self.profile)
        updates = [(name, body) for name, body in self.calls if name == 'updateAgent']
        self.assertEqual(len(updates), 1)
        self.assertEqual(set(updates[0][1]['profile']), {'description', 'name'})
        self.assertEqual(updates[0][1]['profile']['name'], self.profile['name'])

    def test_unconfigured_installation_does_not_mutate_or_call_the_gateway(self):
        (self.sd / 'openbot-box-remint.json').unlink()
        with mock.patch.object(M, 'native') as gateway:
            self.assertEqual(M.publish(), {'status': 'not-configured'})
            gateway.assert_not_called()
        self.assertFalse((self.sd / 'workflows').exists())


if __name__ == '__main__':
    unittest.main()
