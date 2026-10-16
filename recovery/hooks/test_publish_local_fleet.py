"""Safety and recovery tests for publishing existing BOX identities."""
import copy
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock


PATH = pathlib.Path(__file__).with_name('publish-local-fleet.py')
SPEC = importlib.util.spec_from_file_location('publish_local_fleet', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
AID = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
ROOM = '00000000-0000-4000-8000-000000000003'


def box_row(aid=AID):
    return {'id': '12345', 'agentId': aid, 'legacyAgentId': aid,
            'harness': 'box', 'description': 'existing specialist role'}


class FakePlatform:
    def __init__(self, rows=None, created=None, transcript=None):
        self.roster = copy.deepcopy(rows or [])
        self.created = copy.deepcopy(created if created is not None else box_row())
        self.transcript = {'entries': [{'body': 'PRIVATE MESSAGE'}], 'generation': 1} if transcript is None else transcript
        self.calls = []

    def __call__(self, method, body):
        self.calls.append((method, copy.deepcopy(body)))
        if method == 'ListGrokBotAgents':
            return {'agents': copy.deepcopy(self.roster)}
        if method == 'CreateGrokBotAgent':
            self.roster.append(copy.deepcopy(self.created))
            return {'agent': copy.deepcopy(self.created)}
        if method == 'ListGrokBotTranscriptEntries':
            return copy.deepcopy(self.transcript)
        raise AssertionError('Unexpected API: ' + method)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile_path = self.root / 'agents' / AID / 'profile.json'
        self.profile_path.parent.mkdir(parents=True)
        self.profile = {'id': AID, 'harness': 'box', 'name': 'Specialist',
                        'description': 'existing specialist role', 'title': 'Useful role',
                        'avatarShape': 'square', 'avatarColor': 'blue',
                        'namedBy': 'user', 'customSettings': {'context': 8192}}
        self.save_profile(self.profile)
        os.chmod(self.profile_path, 0o640)
        self.run = self.root / 'snapshot'
        self.run.mkdir(mode=0o700)
        self.gateway_calls = []
        self.allowed = {AID: {'id': AID, 'isGroup': False}}

    def save_profile(self, profile):
        self.profile_path.write_text(json.dumps(profile, indent=2) + '\n')

    def gateway(self, method, body):
        self.gateway_calls.append((method, copy.deepcopy(body)))
        return {'outcome': 'exported', 'clientStateSeed': {'lastMessagePreview': 'PRIVATE MESSAGE'}}

    def publisher(self, platform):
        return MODULE.Publisher(self.root, self.allowed, platform, self.gateway)

    def assert_unchanged(self, before):
        self.assertEqual(self.profile_path.read_bytes(), before)
        self.assertEqual(list(self.run.glob('*.profile.before.json')), [])
        self.assertEqual(self.gateway_calls, [])

    def test_temporal_creation_is_refused_before_profile_write(self):
        created = {**box_row(), 'harness': 'temporal'}
        before = self.profile_path.read_bytes()
        with self.assertRaisesRegex(MODULE.SafetyError, 'not BOX'):
            self.publisher(FakePlatform(created=created)).publish(AID, self.run)
        self.assert_unchanged(before)

    def test_wrong_identity_creation_is_refused_before_profile_write(self):
        for field in ('agentId', 'legacyAgentId'):
            with self.subTest(field=field):
                created = {**box_row(), field: OTHER}
                before = self.profile_path.read_bytes()
                with self.assertRaisesRegex(MODULE.SafetyError, 'identity fields differ'):
                    self.publisher(FakePlatform(created=created)).publish(AID, self.run)
                self.assert_unchanged(before)

    def test_publication_preserves_uuid_role_and_existing_profile_fields(self):
        platform = FakePlatform()
        result = self.publisher(platform).publish(AID, self.run)
        after = json.loads(self.profile_path.read_text())
        self.assertEqual(after, {**self.profile, 'serverId': '12345'})
        create_calls = [body for method, body in platform.calls if method == 'CreateGrokBotAgent']
        self.assertEqual(len(create_calls), 1)
        request = create_calls[0]
        self.assertEqual(request['agentId'], AID)
        self.assertEqual(request['legacyAgentId'], AID)
        self.assertEqual(request['description'], self.profile['description'])
        self.assertEqual(request['harness'], 1)
        self.assertEqual(request['createIntent'], 2)
        self.assertEqual(request['createCaller'], 4)
        self.assertTrue(request['introductionSuppressed'])
        self.assertFalse(request['kickstartRequested'])
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(self.gateway_calls, [('exportGrokBotWorkingState',
            {'agentId': AID, 'expectedServerId': '12345', 'trigger': 'manual'})])

    def test_verified_existing_box_is_reused_on_repeated_runs(self):
        platform = FakePlatform(rows=[box_row()])
        publisher = self.publisher(platform)
        first = publisher.publish(AID, self.run)
        bound = self.profile_path.read_bytes()
        second = publisher.publish(AID, self.run)
        self.assertEqual(first['registration'], 'reused-box')
        self.assertTrue(first['profileChanged'])
        self.assertFalse(second['profileChanged'])
        self.assertEqual(self.profile_path.read_bytes(), bound)
        self.assertFalse(any(method == 'CreateGrokBotAgent' for method, _ in platform.calls))
        backup = json.loads((self.run / (AID + '.profile.before.json')).read_text())
        self.assertEqual(backup, self.profile)

    def test_existing_temporal_row_cannot_be_adopted_or_replaced(self):
        before = self.profile_path.read_bytes()
        platform = FakePlatform(rows=[{**box_row(), 'harness': 'temporal'}])
        with self.assertRaisesRegex(MODULE.SafetyError, 'not BOX'):
            self.publisher(platform).publish(AID, self.run)
        self.assert_unchanged(before)
        self.assertFalse(any(method == 'CreateGrokBotAgent' for method, _ in platform.calls))

    def test_existing_temporal_binding_to_another_id_is_refused(self):
        profile = {**self.profile, 'serverId': '12345'}
        self.save_profile(profile)
        before = self.profile_path.read_bytes()
        platform = FakePlatform(rows=[{**box_row(OTHER), 'harness': 'temporal'}])
        with self.assertRaisesRegex(MODULE.SafetyError, 'not BOX'):
            self.publisher(platform).publish(AID, self.run)
        self.assert_unchanged(before)

    def test_out_of_allowlist_uuid_is_not_read_or_published(self):
        platform = FakePlatform()
        with self.assertRaisesRegex(MODULE.SafetyError, 'outside'):
            self.publisher(platform).publish(OTHER, self.run)
        self.assertEqual(platform.calls, [])
        self.assertEqual(list(self.run.iterdir()), [])

    def test_plan_does_not_write_and_ignores_unrelated_temporal_rows(self):
        platform = FakePlatform(rows=[{**box_row(OTHER), 'harness': 'temporal'}])
        before = self.profile_path.read_bytes()
        plan = self.publisher(platform).plan()
        self.assertEqual(plan, [{'id': AID, 'status': 'register-existing',
                                 'serverId': None, 'profileBound': False}])
        self.assert_unchanged(before)
        self.assertEqual([method for method, _ in platform.calls], ['ListGrokBotAgents'])
        self.assertEqual(list(self.run.iterdir()), [])

    def test_groups_are_skipped_without_registering_or_exporting(self):
        self.allowed[AID]['isGroup'] = True
        platform = FakePlatform()
        result = self.publisher(platform).publish(AID, self.run)
        self.assertEqual(result, {'id': AID, 'status': 'skipped-group'})
        self.assertEqual(platform.calls, [])
        self.assertEqual(self.gateway_calls, [])

    def test_atomic_binding_backs_up_before_replacing_and_preserves_permissions(self):
        original = self.profile_path.read_bytes()
        real_replace = os.replace
        observed = []

        def record_replace(source, target):
            if pathlib.Path(target) == self.profile_path:
                observed.append(self.profile_path.read_bytes())
                self.assertEqual(json.loads((self.run / (AID + '.profile.before.json')).read_text()), self.profile)
            return real_replace(source, target)

        with mock.patch.object(MODULE.os, 'replace', side_effect=record_replace):
            MODULE.bind_profile(self.profile_path, box_row(), AID, self.run, self.profile)
        self.assertEqual(observed, [original])
        self.assertEqual(stat.S_IMODE(self.profile_path.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE((self.run / (AID + '.profile.before.json')).stat().st_mode), 0o600)
        self.assertFalse(list(self.profile_path.parent.glob('.openbot-profile-*')))

    def test_changed_profile_does_not_get_overwritten(self):
        changed = {**self.profile, 'description': 'new user role'}
        self.save_profile(changed)
        with self.assertRaisesRegex(MODULE.SafetyError, 'changed during'):
            MODULE.bind_profile(self.profile_path, box_row(), AID, self.run, self.profile)
        self.assertEqual(json.loads(self.profile_path.read_text()), changed)
        self.assertEqual(list(self.run.iterdir()), [])

    def test_change_while_staging_prevents_replacement(self):
        real_fsync = os.fsync
        calls = 0

        def change_during_fsync(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:  # The backup is flushed first, then the staged profile.
                self.save_profile({**self.profile, 'title': 'user edited while publishing'})
            return real_fsync(descriptor)

        with mock.patch.object(MODULE.os, 'fsync', side_effect=change_during_fsync):
            with self.assertRaisesRegex(MODULE.SafetyError, 'changed before'):
                MODULE.bind_profile(self.profile_path, box_row(), AID, self.run, self.profile)
        self.assertEqual(json.loads(self.profile_path.read_text())['title'], 'user edited while publishing')
        self.assertFalse(list(self.profile_path.parent.glob('.openbot-profile-*')))

    def test_platform_flip_before_binding_leaves_profile_untouched(self):
        platform = FakePlatform()
        count = 0

        def flip(method, body):
            nonlocal count
            if method == 'ListGrokBotAgents':
                count += 1
                if count == 2:
                    platform.roster[0]['harness'] = 'temporal'
            return platform(method, body)

        before = self.profile_path.read_bytes()
        with self.assertRaisesRegex(MODULE.SafetyError, 'not BOX'):
            self.publisher(flip).publish(AID, self.run)
        self.assert_unchanged(before)

    def test_export_does_not_claim_transcript_publication(self):
        platform = FakePlatform(transcript={'entries': [], 'generation': 0})
        result = self.publisher(platform).publish(AID, self.run)
        self.assertEqual(result['exportOutcome'], 'exported')
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(result['transcriptAccessible'])
        self.assertEqual(result['transcriptEntriesSampled'], 0)

    def test_skipped_export_is_incomplete_even_with_old_visible_rows(self):
        publisher = self.publisher(FakePlatform())
        publisher.gateway_rpc = lambda method, body: {'outcome': 'skipped', 'reason': 'turn-inflight'}
        result = publisher.publish(AID, self.run)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['exportOutcome'], 'skipped')

    def test_ledger_and_results_do_not_include_message_bodies(self):
        result = self.publisher(FakePlatform()).publish(AID, self.run)
        serialized = json.dumps(result) + (self.run / (AID + '.publication.json')).read_text()
        self.assertNotIn('PRIVATE MESSAGE', serialized)
        self.assertNotIn('clientStateSeed', serialized)
        self.assertNotIn('entries', serialized)

    def test_credentials_in_profiles_are_not_written_to_backups(self):
        self.save_profile({**self.profile, 'settings': {'api_key': 'DO NOT COPY'}})
        with self.assertRaisesRegex(MODULE.SafetyError, 'credential fields'):
            self.publisher(FakePlatform()).publish(AID, self.run)
        self.assertEqual(list(self.run.iterdir()), [])

    def test_duplicate_claims_and_mismatched_existing_identity_are_refused(self):
        for rows in ([box_row(), {**box_row(), 'id': '67890'}],
                     [{**box_row(), 'legacyAgentId': OTHER}]):
            with self.subTest(rows=rows):
                with self.assertRaises(MODULE.SafetyError):
                    self.publisher(FakePlatform(rows=rows)).publish(AID, self.run)
                self.assertEqual(list(self.run.iterdir()), [])


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.roster = pathlib.Path(self.temporary.name) / 'roster.json'

    def load(self, rows):
        self.roster.write_text(json.dumps(rows))
        return MODULE.load_allowlist(self.roster, expected_count=None)

    def test_only_current_ids_are_allowed_not_retired_id(self):
        allowed = self.load([{'id': AID, 'retiredId': OTHER, 'isGroup': False},
                             {'newId': ROOM, 'isGroup': True}])
        self.assertEqual(set(allowed), {AID, ROOM})
        self.assertNotIn(OTHER, allowed)
        self.assertTrue(allowed[ROOM]['isGroup'])

    def test_duplicate_wrong_ids_and_non_boolean_groups_are_refused(self):
        cases = ([{'id': AID}, {'id': AID}], [{'id': '../../outside'}],
                 [{'id': AID, 'newId': OTHER}], [{'id': AID, 'isGroup': 'false'}])
        for records in cases:
            with self.subTest(records=records):
                with self.assertRaises(MODULE.SafetyError):
                    self.load(records)

    def test_default_cardinality_guard(self):
        self.roster.write_text(json.dumps([{'id': AID}]))
        with self.assertRaisesRegex(MODULE.SafetyError, 'size changed'):
            MODULE.load_allowlist(self.roster)


if __name__ == '__main__':
    unittest.main()
