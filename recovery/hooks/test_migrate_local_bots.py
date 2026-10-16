"""Room recovery and migration verification must preserve existing identities."""
import copy
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location('migration', pathlib.Path(__file__).with_name('migrate-local-bots.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ORIGINAL_PLAN = MODULE.plan


def aid(number):
    return '00000000-0000-4000-8000-' + f'{number:012d}'


OLD_ROOM, ROOM, OLD_MEMBER, MEMBER = aid(1), aid(2), aid(3), aid(4)
OLD_OTHER, OTHER = aid(5), aid(6)
OLD_ROOM_TWO, ROOM_TWO = aid(7), aid(8)


class RoomRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.backup = self.root / 'backup'
        self.backup.mkdir()
        self.journal_path = self.root / 'openbot-local-migration.json'
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.sd = mock.patch.object(MODULE, 'SD', self.root)
        self.journal_patch = mock.patch.object(MODULE, 'JOURNAL', self.journal_path)
        self.proc_patch = mock.patch.object(MODULE, 'PROC', self.proc)
        self.sd.start()
        self.journal_patch.start()
        self.proc_patch.start()
        self.addCleanup(self.sd.stop)
        self.addCleanup(self.journal_patch.stop)
        self.addCleanup(self.proc_patch.stop)
        self.journal = {'backup': str(self.backup), 'finalized': True,
                        'deliveryVerified': {'agentId': MEMBER},
                        'entries': [self.entry(OLD_ROOM, ROOM, 'ROOM', 'A room'),
                                    self.entry(OLD_MEMBER, MEMBER, 'AGENT', 'Coder'),
                                    self.entry(OLD_OTHER, OTHER, 'AGENT', 'Other')]}
        self.save(self.journal_path, self.journal)
        for old, current in [(OLD_ROOM, ROOM), (OLD_MEMBER, MEMBER), (OLD_OTHER, OTHER)]:
            self.save(self.path(current, 'profile.json'), {'agentId': current, 'harness': 'box', 'role': 'Preserve role'})
            self.save(self.path(old, 'profile.json'), {'agentId': old, 'harness': 'temporal'})
        self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': []})
        self.save(self.path(ROOM, 'settings.json'), {'hiddenFromSidebar': False})
        self.save(self.backup / 'server-roster.json', [
            {'agentId': OLD_ROOM, 'legacyAgentId': OLD_ROOM, 'kind': 'GROK_BOT_AGENT_KIND_ROOM'}])

    @staticmethod
    def entry(old, current, kind, name):
        return {'oldId': old, 'newId': current, 'kind': 'GROK_BOT_AGENT_KIND_' + kind,
                'name': name, 'staged': True, 'finalized': True, 'automationStates': {}}

    def path(self, ident, filename):
        return self.root / 'agents' / ident / filename

    @staticmethod
    def save(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) + '\n')

    def load(self, path):
        return json.loads(path.read_text())

    def original(self, values, backup=False):
        file = self.backup / 'room-groups' / (OLD_ROOM + '.json') if backup else self.path(OLD_ROOM, 'group.json')
        self.save(file, {'version': 1, 'memberIds': values})

    def transcript(self, authors):
        database = self.path(ROOM, 'store.db')
        with contextlib.closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY, entry TEXT)')
            conn.executemany('INSERT INTO transcript_entries VALUES(?,?)', [
                (i + 1, json.dumps({'author': author, 'text': 'Existing private content'}))
                for i, author in enumerate(authors)])
            conn.commit()
        return database

    def test_existing_valid_membership_is_preserved_without_rewriting(self):
        group = {'version': 1, 'memberIds': [OTHER, MEMBER], 'customSetting': 'Keep'}
        file = self.path(ROOM, 'group.json')
        self.save(file, group)
        before = file.read_bytes()
        before_mtime = file.stat().st_mtime_ns
        self.original([OLD_MEMBER])
        results = MODULE.restore_room_members(self.journal)
        self.assertEqual(results[0]['status'], 'preserved-current')
        self.assertEqual(file.read_bytes(), before)
        self.assertEqual(file.stat().st_mtime_ns, before_mtime)

    def test_user_only_transcript_cannot_finalize_an_empty_room(self):
        database = self.transcript([{'kind': 'user', 'name': 'User'}])
        before = database.read_bytes()
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['status'], 'unresolved')
        self.assertTrue(self.journal['entries'][0]['finalized'])
        self.assertTrue(self.load(self.journal_path)['finalized'])
        self.assertFalse(self.load(self.journal_path)['roomRecoveryComplete'])
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [])

    def test_unique_or_ambiguous_author_names_are_never_membership_authority(self):
        self.journal['entries'][2]['name'] = 'Coder'
        self.save(self.journal_path, self.journal)
        database = self.transcript([{'name': 'Coder', 'id': OLD_MEMBER}, {'name': 'Other', 'id': OLD_OTHER}])
        before = database.read_bytes()
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(database.read_bytes(), before)

    def test_room_name_is_never_split_into_members(self):
        self.journal['entries'][0]['name'] = 'Coder, Other'
        self.save(self.journal_path, self.journal)
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')

    def test_empty_retired_shell_is_left_untouched(self):
        group = self.path(ROOM, 'group.json')
        self.journal['entries'][0]['retired'] = True
        self.save(self.journal_path, self.journal)
        self.save(self.path(ROOM, 'settings.json'), {'hiddenFromSidebar': True})
        before = group.read_bytes()
        self.original([OLD_MEMBER])
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['status'], 'retired-shell')
        self.assertEqual(group.read_bytes(), before)

    def test_hidden_empty_current_room_is_not_assumed_retired(self):
        self.save(self.path(ROOM, 'settings.json'), {'hiddenFromSidebar': True})
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')

    def test_old_retired_empty_group_is_never_modified(self):
        self.original([])
        old = self.path(OLD_ROOM, 'group.json')
        self.save(self.path(OLD_ROOM, 'settings.json'), {'hiddenFromSidebar': True})
        before = old.read_bytes()
        self.original([OLD_MEMBER], backup=True)
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'restored-authority')
        self.assertEqual(old.read_bytes(), before)

    def test_explicit_original_members_are_remapped_with_other_group_fields_preserved(self):
        self.original([OLD_MEMBER, OLD_OTHER])
        self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [], 'policy': 'Original'})
        profile_before = self.path(ROOM, 'profile.json').read_bytes()
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['status'], 'restored-authority')
        self.assertEqual(self.load(self.path(ROOM, 'group.json')),
                         {'version': 1, 'memberIds': [MEMBER, OTHER], 'policy': 'Original'})
        self.assertEqual(self.path(ROOM, 'profile.json').read_bytes(), profile_before)
        self.assertEqual(self.load(self.path(OLD_ROOM, 'group.json'))['memberIds'], [OLD_MEMBER, OLD_OTHER])

    def test_saved_original_group_repairs_a_later_erased_old_shell(self):
        self.original([], backup=False)
        self.original([OLD_MEMBER], backup=True)
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['source'], 'original-group-backup')
        self.assertEqual(result['memberIds'], [MEMBER])

    def test_original_server_member_ids_are_explicit_authority(self):
        self.save(self.backup / 'server-roster.json', [{
            'agentId': OLD_ROOM, 'legacyAgentId': OLD_ROOM, 'kind': 'GROK_BOT_AGENT_KIND_ROOM',
            'memberAgentIds': [OLD_MEMBER]}])
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['source'], 'original-server-roster')
        self.assertEqual(result['memberIds'], [MEMBER])

    def test_saved_agent_kind_cannot_be_reinterpreted_as_a_room(self):
        self.original([OLD_MEMBER])
        self.save(self.backup / 'server-roster.json', [{
            'agentId': OLD_ROOM, 'kind': 'GROK_BOT_AGENT_KIND_AGENT'}])
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [])

    def test_missing_original_room_kind_does_not_trust_the_synthesized_journal(self):
        self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [MEMBER]})
        group_before = self.path(ROOM, 'group.json').read_bytes()
        (self.backup / 'server-roster.json').unlink()
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')
        self.assertEqual(self.path(ROOM, 'group.json').read_bytes(), group_before)

    def test_conflicting_original_sources_are_reported_instead_of_guessed(self):
        self.original([OLD_MEMBER])
        self.original([OLD_OTHER], backup=True)
        result = MODULE.restore_room_members(self.journal)[0]
        self.assertEqual(result['status'], 'unresolved')
        self.assertIn('disagree', result['reason'])
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [])

    def test_invalid_nonempty_current_members_are_never_overwritten(self):
        group = self.path(ROOM, 'group.json')
        self.save(group, {'version': 1, 'memberIds': [aid(99)]})
        before = group.read_bytes()
        self.original([OLD_MEMBER])
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')
        self.assertEqual(group.read_bytes(), before)

    def test_member_directory_identity_and_harness_must_match(self):
        self.original([OLD_MEMBER])
        for bad in [{'harness': 'temporal', 'agentId': MEMBER}, {'harness': 'box', 'agentId': OTHER}]:
            with self.subTest(profile=bad):
                self.save(self.path(MEMBER, 'profile.json'), bad)
                self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')
                self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [])

    def test_duplicates_and_self_membership_are_refused_without_partial_recovery(self):
        for values in [[OLD_MEMBER, OLD_MEMBER], [OLD_ROOM], [OLD_MEMBER, aid(99)]]:
            with self.subTest(values=values):
                self.original(values)
                self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'unresolved')
                self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [])

    def test_current_old_member_ids_can_be_remapped_once(self):
        self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [OLD_MEMBER]})
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'remapped-current')
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [MEMBER])

    def test_repeated_finalize_is_idempotent_and_does_not_contact_the_platform(self):
        self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [MEMBER]})
        with mock.patch.object(MODULE, 'rpc', side_effect=AssertionError('Unexpected RPC')), \
                mock.patch.object(MODULE, 'gateway', side_effect=AssertionError('Unexpected gateway')):
            MODULE.finalize()
            first = self.journal_path.read_bytes()
            first_mtime = self.journal_path.stat().st_mtime_ns
            MODULE.finalize()
        self.assertEqual(self.journal_path.read_bytes(), first)
        self.assertEqual(self.journal_path.stat().st_mtime_ns, first_mtime)

    def test_finalize_refuses_unknown_room_before_any_model_setting_or_platform_change(self):
        with mock.patch.object(MODULE, 'rpc', side_effect=AssertionError('Unexpected RPC')), \
                mock.patch.object(MODULE, 'gateway', side_effect=AssertionError('Unexpected gateway')):
            with self.assertRaisesRegex(RuntimeError, 'Room membership unresolved'):
                MODULE.finalize()
        self.assertFalse((self.root / 'openbot-bot-models.json').exists())
        self.assertTrue(self.load(self.journal_path)['entries'][0]['finalized'])
        self.assertFalse(self.load(self.journal_path)['roomRecoveryComplete'])

    def test_completed_room_debt_cannot_resume_a_paused_routine_or_replay_settings(self):
        relative = 'automations/paused/automation.json'
        self.journal['entries'][0]['automationStates'] = {relative: True}
        self.save(self.journal_path, self.journal)
        routine = self.path(ROOM, relative)
        self.save(routine, {'enabled': False, 'name': 'User paused this later'})
        models = self.root / 'openbot-bot-models.json'
        settings = self.root / 'settings.json'
        self.save(models, {'assignments': {ROOM: {'modelId': 'selected-later'}}})
        self.save(settings, {'pinnedAgentIds': [OTHER]})
        before = {path: path.read_bytes() for path in (routine, models, settings)}
        with mock.patch.object(MODULE, 'rpc', side_effect=AssertionError('Unexpected RPC')), \
                mock.patch.object(MODULE, 'gateway', side_effect=AssertionError('Unexpected gateway')):
            with self.assertRaisesRegex(RuntimeError, 'Room membership unresolved'):
                MODULE.finalize()
            debt = self.load(self.journal_path)
            self.assertTrue(debt['finalized'])
            self.assertTrue(debt['migrationEffectsFinalized'])
            self.assertTrue(debt['entries'][0]['finalized'])
            self.assertFalse(debt['roomRecoveryComplete'])
            self.original([OLD_MEMBER])
            MODULE.finalize()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertTrue(self.load(self.journal_path)['roomRecoveryComplete'])

    def test_completed_top_level_receipt_prevents_replay_even_if_entry_flag_was_changed(self):
        self.journal['entries'][0]['finalized'] = False
        self.save(self.journal_path, self.journal)
        self.original([OLD_MEMBER])
        with mock.patch.object(MODULE, 'rpc', side_effect=AssertionError('Unexpected RPC')), \
                mock.patch.object(MODULE, 'gateway', side_effect=AssertionError('Unexpected gateway')):
            MODULE.finalize()
        self.assertTrue(self.load(self.journal_path)['migrationEffectsFinalized'])

    def test_plan_is_read_only_and_stale_journal_is_rejected(self):
        self.original([OLD_MEMBER])
        group = self.path(ROOM, 'group.json').read_bytes()
        journal = self.journal_path.read_bytes()
        self.assertEqual(MODULE.room_recovery_plan(self.journal)['rooms'][0]['status'], 'restored-authority')
        self.assertEqual(self.journal_path.read_bytes(), journal)
        self.assertEqual(self.path(ROOM, 'group.json').read_bytes(), group)
        changed = copy.deepcopy(self.journal)
        changed['unrelatedChange'] = True
        self.save(self.journal_path, changed)
        with self.assertRaisesRegex(RuntimeError, 'journal changed'):
            MODULE.restore_room_members(self.journal)

    def test_journal_write_failure_rolls_back_room_bytes_and_mode(self):
        self.original([OLD_MEMBER])
        group = self.path(ROOM, 'group.json')
        os.chmod(group, 0o640)
        group_before, journal_before = group.read_bytes(), self.journal_path.read_bytes()
        original_atomic = MODULE.atomic_bytes

        def fail_journal(path, data, **kwargs):
            if path == self.journal_path:
                raise OSError('Injected disk error')
            return original_atomic(path, data, **kwargs)

        with mock.patch.object(MODULE, 'atomic_bytes', side_effect=fail_journal):
            with self.assertRaisesRegex(OSError, 'Injected disk error'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(group.read_bytes(), group_before)
        self.assertEqual(group.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.journal_path.read_bytes(), journal_before)
        self.assertNotIn('roomRecovery', self.journal['entries'][0])
        self.assertEqual(list(group.parent.glob('*.migration.tmp')), [])

    def test_concurrent_group_edit_is_not_overwritten(self):
        self.original([OLD_MEMBER])
        original_plan = MODULE.room_recovery_plan

        def changed_after_plan(journal):
            recovery = original_plan(journal)
            self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [OTHER]})
            return recovery

        with mock.patch.object(MODULE, 'room_recovery_plan', side_effect=changed_after_plan):
            with self.assertRaisesRegex(RuntimeError, 'changed concurrently'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [OTHER])

    def test_second_room_write_failure_rolls_back_first_room(self):
        self.original([OLD_MEMBER])
        self.journal['entries'].append(self.entry(OLD_ROOM_TWO, ROOM_TWO, 'ROOM', 'Second room'))
        self.save(self.journal_path, self.journal)
        self.save(self.path(ROOM_TWO, 'profile.json'), {'harness': 'box', 'agentId': ROOM_TWO})
        self.save(self.path(ROOM_TWO, 'group.json'), {'version': 1, 'memberIds': []})
        self.save(self.path(OLD_ROOM_TWO, 'group.json'), {'version': 1, 'memberIds': [OLD_OTHER]})
        roster = self.load(self.backup / 'server-roster.json')
        roster.append({'agentId': OLD_ROOM_TWO, 'kind': 'GROK_BOT_AGENT_KIND_ROOM'})
        self.save(self.backup / 'server-roster.json', roster)
        before = {self.path(ident, 'group.json'): self.path(ident, 'group.json').read_bytes()
                  for ident in (ROOM, ROOM_TWO)}
        journal_before = self.journal_path.read_bytes()
        original_atomic = MODULE.atomic_bytes

        def fail_second(path, data, **kwargs):
            if path == self.path(ROOM_TWO, 'group.json'):
                raise OSError('Second group failed')
            return original_atomic(path, data, **kwargs)

        with mock.patch.object(MODULE, 'atomic_bytes', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'Second group failed'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.journal_path.read_bytes(), journal_before)

    def test_rollback_never_overwrites_an_external_edit_after_our_write(self):
        self.original([OLD_MEMBER])
        original_atomic = MODULE.atomic_bytes

        def edit_group_and_fail_journal(path, data, **kwargs):
            if path == self.journal_path:
                self.save(self.path(ROOM, 'group.json'), {'version': 1, 'memberIds': [OTHER]})
                raise OSError('Journal failed after another writer edited group')
            return original_atomic(path, data, **kwargs)

        with mock.patch.object(MODULE, 'atomic_bytes', side_effect=edit_group_and_fail_journal):
            with self.assertRaisesRegex(RuntimeError, 'rollback conflicts'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(self.load(self.path(ROOM, 'group.json'))['memberIds'], [OTHER])

    def test_edit_during_temp_fsync_is_detected_immediately_before_replace(self):
        self.original([OLD_MEMBER])
        group = self.path(ROOM, 'group.json')
        journal_before = self.journal_path.read_bytes()
        actual_fsync = MODULE.os.fsync

        def edit_while_fsync(fd):
            actual_fsync(fd)
            self.save(group, {'version': 1, 'memberIds': [OTHER]})

        with mock.patch.object(MODULE.os, 'fsync', side_effect=edit_while_fsync):
            with self.assertRaisesRegex(RuntimeError, 'changed concurrently'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(self.load(group)['memberIds'], [OTHER])
        self.assertEqual(self.journal_path.read_bytes(), journal_before)
        self.assertFalse(any(group.parent.glob('*.migration.tmp')))

    def test_edit_during_rollback_fsync_is_not_overwritten(self):
        self.original([OLD_MEMBER])
        group = self.path(ROOM, 'group.json')
        actual_fsync = MODULE.os.fsync
        original_atomic = MODULE.atomic_bytes
        rolling_back = False

        def journal_failure(path, data, **kwargs):
            nonlocal rolling_back
            if path == self.journal_path:
                rolling_back = True
                raise OSError('Journal failed')
            return original_atomic(path, data, **kwargs)

        def edit_rollback_fsync(fd):
            actual_fsync(fd)
            if rolling_back:
                self.save(group, {'version': 1, 'memberIds': [OTHER]})

        with mock.patch.object(MODULE, 'atomic_bytes', side_effect=journal_failure), \
                mock.patch.object(MODULE.os, 'fsync', side_effect=edit_rollback_fsync):
            with self.assertRaisesRegex(RuntimeError, 'rollback conflicts'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(self.load(group)['memberIds'], [OTHER])

    def test_two_room_rollback_conflict_still_rolls_back_the_safely_owned_room(self):
        self.original([OLD_MEMBER])
        self.journal['entries'].append(self.entry(OLD_ROOM_TWO, ROOM_TWO, 'ROOM', 'Second room'))
        self.save(self.journal_path, self.journal)
        self.save(self.path(ROOM_TWO, 'profile.json'), {'harness': 'box', 'agentId': ROOM_TWO})
        self.save(self.path(ROOM_TWO, 'group.json'), {'version': 1, 'memberIds': []})
        self.save(self.path(OLD_ROOM_TWO, 'group.json'), {'version': 1, 'memberIds': [OLD_OTHER]})
        roster = self.load(self.backup / 'server-roster.json')
        roster.append({'agentId': OLD_ROOM_TWO, 'kind': 'GROK_BOT_AGENT_KIND_ROOM'})
        self.save(self.backup / 'server-roster.json', roster)
        first = self.path(ROOM, 'group.json')
        first_before = first.read_bytes()
        journal_before = self.journal_path.read_bytes()
        original_atomic = MODULE.atomic_bytes

        def edit_second_and_fail(path, data, **kwargs):
            if path == self.journal_path:
                self.save(self.path(ROOM_TWO, 'group.json'), {'version': 1, 'memberIds': [MEMBER]})
                raise OSError('Journal failed')
            return original_atomic(path, data, **kwargs)

        with mock.patch.object(MODULE, 'atomic_bytes', side_effect=edit_second_and_fail):
            with self.assertRaisesRegex(RuntimeError, 'rollback conflicts') as error:
                MODULE.restore_room_members(self.journal)
        self.assertIn(ROOM_TWO, str(error.exception))
        self.assertEqual(first.read_bytes(), first_before)
        self.assertEqual(self.load(self.path(ROOM_TWO, 'group.json'))['memberIds'], [MEMBER])
        self.assertEqual(self.journal_path.read_bytes(), journal_before)

    def test_running_native_host_blocks_room_writes_but_allows_read_only_plan(self):
        self.original([OLD_MEMBER])
        cmdline = self.proc / '123' / 'cmdline'
        cmdline.parent.mkdir()
        cmdline.write_bytes(b'/exec-daemon/node\0/opt/sand/sand-host/host-main.cjs\0')
        group_before = self.path(ROOM, 'group.json').read_bytes()
        journal_before = self.journal_path.read_bytes()
        self.assertEqual(MODULE.room_recovery_plan(self.journal)['rooms'][0]['status'], 'restored-authority')
        with self.assertRaisesRegex(RuntimeError, 'Native host is running'):
            MODULE.restore_room_members(self.journal)
        self.assertEqual(self.path(ROOM, 'group.json').read_bytes(), group_before)
        self.assertEqual(self.journal_path.read_bytes(), journal_before)

    def test_guard_daemon_with_host_argument_is_not_the_native_writer(self):
        self.original([OLD_MEMBER])
        cmdline = self.proc / '124' / 'cmdline'
        cmdline.parent.mkdir()
        cmdline.write_bytes(b'/usr/bin/node\0/openbot/daemon.cjs\0--host-main\0/opt/sand/sand-host/host-main.cjs\0')
        self.assertEqual(MODULE.restore_room_members(self.journal)[0]['status'], 'restored-authority')

    def test_native_host_with_a_preload_script_is_still_blocked(self):
        self.original([OLD_MEMBER])
        cmdline = self.proc / '125' / 'cmdline'
        cmdline.parent.mkdir()
        cmdline.write_bytes(b'/exec-daemon/node\0--require\0/preload.cjs\0/opt/sand/sand-host/host-main.cjs\0')
        with self.assertRaisesRegex(RuntimeError, 'Native host is running'):
            MODULE.restore_room_members(self.journal)

    def test_native_host_starting_during_fsync_blocks_the_commit(self):
        self.original([OLD_MEMBER])
        group_before = self.path(ROOM, 'group.json').read_bytes()
        actual_fsync = MODULE.os.fsync

        def start_host_during_fsync(fd):
            actual_fsync(fd)
            cmdline = self.proc / '126' / 'cmdline'
            cmdline.parent.mkdir(exist_ok=True)
            cmdline.write_bytes(b'/exec-daemon/node\0/opt/sand/sand-host/host-main.cjs\0')

        with mock.patch.object(MODULE.os, 'fsync', side_effect=start_host_during_fsync):
            with self.assertRaisesRegex(RuntimeError, 'Native host is running'):
                MODULE.restore_room_members(self.journal)
        self.assertEqual(self.path(ROOM, 'group.json').read_bytes(), group_before)

    def test_concurrent_migration_command_is_refused(self):
        with MODULE.migration_lock():
            with self.assertRaisesRegex(RuntimeError, 'Another local migration'):
                MODULE.restore_room_members(self.journal)

    def test_symlink_group_and_duplicate_identity_map_are_refused(self):
        file = self.path(ROOM, 'group.json')
        file.unlink()
        file.symlink_to(self.path(OLD_MEMBER, 'profile.json'))
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            MODULE.restore_room_members(self.journal)
        file.unlink()
        self.save(file, {'version': 1, 'memberIds': []})
        self.journal['entries'][2]['newId'] = MEMBER
        self.save(self.journal_path, self.journal)
        with self.assertRaisesRegex(ValueError, 'one-to-one'):
            MODULE.restore_room_members(self.journal)


class MigrationVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.journal_path = self.root / 'openbot-local-migration.json'
        self.journal = {'workingProfile': {'id': aid(99), 'harness': 'box', 'role': 'Working role'},
                        'entries': [RoomRecoveryTests.entry(OLD_MEMBER, MEMBER, 'AGENT', 'Coder'),
                                    RoomRecoveryTests.entry(OLD_OTHER, OTHER, 'AGENT', 'Other')]}
        self.save(self.journal_path, self.journal)
        self.save(self.path(aid(99), 'profile.json'), self.journal['workingProfile'])
        for old, current in ((OLD_MEMBER, MEMBER), (OLD_OTHER, OTHER)):
            self.save(self.path(old, 'profile.json'), {'id': old, 'harness': 'temporal', 'role': 'Original role'})
            self.save(self.path(current, 'profile.json'), {'id': current, 'agentId': current,
                'legacyAgentId': current, 'harness': 'box', 'role': 'Recovered role'})
            with contextlib.closing(sqlite3.connect(self.path(current, 'store.db'))) as conn:
                conn.execute('CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT)')
                conn.execute('INSERT INTO kv VALUES(?,?)', ('metadata', self.metadata(current)))
                conn.commit()
        self.rows = []
        for name, value in (('SD', self.root), ('JOURNAL', self.journal_path)):
            patch = mock.patch.object(MODULE, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.rpc = self.start_mock('rpc', side_effect=self.roster)
        self.gateway = self.start_mock('gateway', side_effect=AssertionError('Unexpected gateway'))
        self.plan = self.start_mock('plan', side_effect=AssertionError('Verification cannot create a plan'))
        self.write = self.start_mock('write', side_effect=AssertionError('Unexpected file write'))
        self.atomic = self.start_mock('atomic_bytes', side_effect=AssertionError('Unexpected file write'))
        uuid_patch = mock.patch.object(MODULE.uuid, 'uuid4', side_effect=AssertionError('Unexpected UUID creation'))
        self.new_uuid = uuid_patch.start()
        self.addCleanup(uuid_patch.stop)

    def start_mock(self, name, **kwargs):
        patch = mock.patch.object(MODULE, name, **kwargs)
        result = patch.start()
        self.addCleanup(patch.stop)
        return result

    save = staticmethod(RoomRecoveryTests.save)

    def path(self, ident, filename):
        return self.root / 'agents' / ident / filename

    @staticmethod
    def metadata(ident):
        return json.dumps({'agentId': ident}).encode().hex()

    @staticmethod
    def row(ident=MEMBER, server_id='12345'):
        return {'id': server_id, 'agentId': ident, 'legacyAgentId': ident, 'harness': 'box'}

    def roster(self, method, body):
        self.assertEqual((method, body), ('ListGrokBotAgents', {}))
        return {'agents': copy.deepcopy(self.rows)}

    def update_profile(self, ident=MEMBER, **fields):
        path = self.path(ident, 'profile.json')
        profile = json.loads(path.read_text())
        self.save(path, {**profile, **fields})

    def snapshot(self):
        # Read-only SQLite connections may coordinate through WAL/lock sidecars.
        return {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                for path in self.root.rglob('*') if path.is_file() and
                not path.name.endswith(('.db-wal', '.db-shm', '.db-journal'))}

    def verify(self, failure=None, lookups=0):
        before = self.snapshot()
        self.rpc.reset_mock()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            if failure is None:
                MODULE.verify()
            else:
                with self.assertRaises(SystemExit) as stopped:
                    MODULE.verify()
                self.assertEqual(stopped.exception.code, 1)
                self.assertIn(failure, output.getvalue())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.rpc.call_args_list, [mock.call('ListGrokBotAgents', {})] * lookups)
        for callback in (self.gateway, self.plan, self.write, self.atomic, self.new_uuid):
            callback.assert_not_called()
        return output.getvalue()

    def test_unregistered_box_copies_require_no_platform_lookup(self):
        self.verify()
        self.update_profile(serverId=None)
        self.verify()

    def test_matching_box_bindings_use_one_read_only_roster_lookup(self):
        self.update_profile(serverId='12345')
        self.rows = [self.row(), {**self.row(OLD_MEMBER, '99999'), 'harness': 'temporal'}]
        self.verify(lookups=1)
        self.update_profile(OTHER, serverId=23456)
        self.rows.append(self.row(OTHER, '23456'))
        self.verify(lookups=1)

    def test_bound_and_unbound_non_box_profiles_are_rejected(self):
        for server_id in (None, '12345'):
            with self.subTest(server_id=server_id):
                self.update_profile(harness='temporal', serverId=server_id)
                self.rows = [self.row()]
                self.verify('non-local profile', lookups=int(server_id is not None))

    def test_source_or_wrong_uuid_in_profile_is_rejected(self):
        original = json.loads(self.path(MEMBER, 'profile.json').read_text())
        for field in ('id', 'agentId', 'legacyAgentId'):
            for wrong_id in (OLD_MEMBER, OTHER):
                with self.subTest(field=field, wrong_id=wrong_id):
                    self.save(self.path(MEMBER, 'profile.json'), {**original, field: wrong_id})
                    self.verify('profile identity mismatch')

    def test_bound_row_requires_box_and_both_current_uuid_fields(self):
        self.update_profile(serverId='12345')
        for field, value in (('harness', 'temporal'), ('harness', None),
                             ('agentId', OLD_MEMBER), ('agentId', OTHER), ('agentId', None),
                             ('legacyAgentId', OLD_MEMBER), ('legacyAgentId', OTHER), ('legacyAgentId', None)):
            with self.subTest(field=field, value=value):
                self.rows = [{**self.row(), field: value}]
                self.verify('BOX server binding mismatch', lookups=1)

    def test_wrong_missing_and_ambiguous_bindings_are_rejected(self):
        self.update_profile(serverId='12345')
        for rows in ([], [self.row(server_id='23456')], [self.row(OTHER)],
                     [self.row(), self.row()], [self.row(), self.row(OTHER)],
                     [self.row(), self.row(server_id='23456')]):
            with self.subTest(rows=rows):
                self.rows = rows
                self.verify('BOX server binding mismatch', lookups=1)

    def test_numeric_server_id_must_be_positive_ascii_decimal(self):
        for server_id in ('', '0', '000', '-1', '1.0', '１２３', '12345 ', None, 12345, True):
            with self.subTest(server_id=server_id):
                # Wire IDs must be strings, while local integer bindings are supported.
                self.update_profile(serverId=str(server_id))
                self.rows = [self.row(server_id=server_id)]
                self.verify('BOX server binding mismatch', lookups=1)

    def test_roster_lookup_failure_is_reported_without_writes_or_response_content(self):
        self.update_profile(serverId='12345')
        for response in (RuntimeError('PRIVATE RESPONSE'), {'http': 401, 'body': 'PRIVATE RESPONSE'},
                         {'error': 'PRIVATE RESPONSE'}, {}, {'agents': None}, {'agents': [{}, None]}, []):
            with self.subTest(response=response):
                self.rpc.side_effect = response if isinstance(response, Exception) else None
                self.rpc.return_value = response
                output = self.verify('BOX binding lookup failed', lookups=1)
                self.assertNotIn('PRIVATE RESPONSE', output)

    def test_missing_journal_never_starts_a_migration(self):
        self.journal_path.unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, 'existing migration journal'):
            MODULE.verify()
        self.assertEqual(self.snapshot(), before)
        for callback in (self.rpc, self.gateway, self.plan, self.write, self.atomic, self.new_uuid):
            callback.assert_not_called()

    def test_store_metadata_must_keep_the_current_uuid(self):
        with contextlib.closing(sqlite3.connect(self.path(MEMBER, 'store.db'))) as conn:
            conn.execute("UPDATE kv SET value=? WHERE key='metadata'", (self.metadata(OLD_MEMBER),))
            conn.commit()
        for server_id in (None, '12345'):
            with self.subTest(server_id=server_id):
                self.update_profile(serverId=server_id)
                self.rows = [self.row()]
                self.verify('metadata identity mismatch', lookups=int(server_id is not None))

    def test_corrupt_or_missing_database_fails_verification_without_repair(self):
        self.path(MEMBER, 'store.db').write_bytes(b'Not a SQLite database')
        self.verify('SQLite verification failed')
        self.path(MEMBER, 'store.db').unlink()
        self.verify('SQLite verification failed')

    def test_wal_database_is_verified_while_writer_transaction_remains_active(self):
        database = self.path(MEMBER, 'store.db')

        def committed_rows():
            with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as reader:
                return reader.execute('SELECT key,value FROM kv ORDER BY key').fetchall()

        with contextlib.closing(sqlite3.connect(database)) as writer:
            # Put the source UUID in the main database, then the current UUID only
            # in WAL. Bypassing live WAL with immutable=1 would fail verification.
            writer.execute("UPDATE kv SET value=? WHERE key='metadata'", (self.metadata(OLD_MEMBER),))
            writer.commit()
            self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute("UPDATE kv SET value=? WHERE key='metadata'", (self.metadata(MEMBER),))
            writer.execute('INSERT INTO kv VALUES(?,?)', ('writer-state', 'committed'))
            writer.commit()
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('INSERT INTO kv VALUES(?,?)', ('pending-writer-state', 'uncommitted'))
            self.assertGreater(database.with_name(database.name + '-wal').stat().st_size, 0)
            before = committed_rows()
            self.assertEqual(before, [('metadata', self.metadata(MEMBER)), ('writer-state', 'committed')])

            self.verify()

            self.assertTrue(writer.in_transaction)
            self.assertEqual(committed_rows(), before)
            self.assertEqual(writer.execute("SELECT value FROM kv WHERE key='pending-writer-state'").fetchone(),
                             ('uncommitted',))
            writer.rollback()

    def test_optional_metadata_and_profile_identity_fields_remain_supported(self):
        self.save(self.path(MEMBER, 'profile.json'), {'harness': 'box', 'role': 'Recovered role'})
        with contextlib.closing(sqlite3.connect(self.path(MEMBER, 'store.db'))) as conn:
            conn.execute("DELETE FROM kv WHERE key='metadata'")
            conn.commit()
        self.verify()

    def test_working_profile_comparison_stays_strict(self):
        self.update_profile(aid(99), serverId='67890')
        self.verify('working bot')

    def test_generic_plan_protects_all_existing_owned_box_profiles(self):
        self.journal_path.unlink()
        (self.root / 'openbot-local-only.enable').touch()
        profiles = {aid(91): {'harness': 'box', 'serverId': '91', 'role': 'First'},
                    aid(92): {'harness': 'box', 'serverId': '92', 'role': 'Second'}}
        for identity, profile in profiles.items():
            self.save(self.path(identity, 'profile.json'), profile)
        rows = [{'agentId': identity, 'legacyAgentId': identity, 'harness': 'box',
                 'viewerIsOwner': True} for identity in profiles]
        def rpc(method, body):
            if method == 'ListGrokBotAgents': return {'agents': rows}
            if method == 'GetGrokBotUserRuntimeSettings': return {'settings': {}}
            raise AssertionError(method)
        with mock.patch.object(MODULE, 'rpc', rpc), mock.patch.object(MODULE, 'gateway', return_value=[]), \
                mock.patch.object(MODULE, 'write', self.save):
            result = ORIGINAL_PLAN()
        self.assertEqual(result['protectedProfiles'], profiles)
        self.assertNotIn('workingProfile', result)
        self.assertEqual(result['entries'], [])

    def test_legacy_profile_without_unique_saved_identity_is_refused(self):
        self.journal['workingProfile'] = {'harness': 'box', 'serverId': '91'}
        self.save(self.journal_path, self.journal)
        for identity in (aid(91), aid(92)):
            self.save(self.path(identity, 'profile.json'), self.journal['workingProfile'])
        self.verify('ambiguous saved identity')

    def test_unstaged_copy_is_rejected_without_creating_or_reading_its_store(self):
        self.journal['entries'][0]['staged'] = False
        self.save(self.journal_path, self.journal)
        self.path(MEMBER, 'store.db').unlink()
        self.verify('not staged')


if __name__ == '__main__':
    unittest.main()
