import importlib.util
import fcntl
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('retired_windows', pathlib.Path(__file__).with_name('reap-retired-windows.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
A = '00000000-0000-4000-8000-000000000001'
B = '00000000-0000-4000-8000-000000000002'
C = '00000000-0000-4000-8000-000000000003'
D = '00000000-0000-4000-8000-000000000004'


class RetiredWindowTests(unittest.TestCase):
    def journal(self):
        return {'entries': [{'oldId': A, 'newId': B, 'phase': 'active'}],
                'previousGenerations': [{'entries': [{'oldId': C, 'newId': A}]}]}

    def table(self):
        return {'assignments': {A: 2, B: 3, C: 4, D: 5},
                'tokens': {A: 'old-owner', B: 'current-owner', C: 'older-owner', D: 'unknown-owner'}}

    def rows(self):
        return [{'id': aid, 'isHiddenFromSidebar': True, 'isRunning': False} for aid in (A, B, C, D)]

    def test_only_recorded_hidden_retired_inactive_unique_desktops(self):
        self.assertEqual([x['id'] for x in m.candidates(self.journal(), self.table(), self.rows())], [A, C])
        for flag, value in [('isRunning', True), ('isRunning', None), ('isHiddenFromSidebar', False)]:
            rows = self.rows(); rows[0][flag] = value
            self.assertNotIn(A, [x['id'] for x in m.candidates(self.journal(), self.table(), rows)])
        table = self.table(); table['assignments'][D] = 2
        self.assertNotIn(A, [x['id'] for x in m.candidates(self.journal(), table, self.rows())])
        table = self.table(); table['assignments'][A] = 1
        self.assertNotIn(A, [x['id'] for x in m.candidates(self.journal(), table, self.rows())])
        table = self.table(); table['tokens'][A] = 'owner; command'
        self.assertNotIn(A, [x['id'] for x in m.candidates(self.journal(), table, self.rows())])

    def test_in_progress_new_copies_are_protected_and_unknown_metadata_refused(self):
        journal = self.journal(); journal['entries'].append({'newId': C, 'phase': 'copying'})
        self.assertEqual([x['id'] for x in m.candidates(journal, self.table(), self.rows())], [A])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            m.candidates(self.journal(), self.table(), self.rows() + [self.rows()[0]])
        table = self.table(); table['assignments'][A] = True
        with self.assertRaisesRegex(RuntimeError, 'index'):
            m.candidates(self.journal(), table, self.rows())

    def test_native_shutdown_preserves_profile_and_refuses_script_drift(self):
        source = ('#!/bin/bash\n# refusing to stop primary desktop\nWINDOW_TOKEN_DIR="/tmp/native"\n'
                  'sand_desktop_unregister_group "d${DISPLAY_NUM}"\n'
                  'rm -rf \\\n\t"${CHROME_PROFILE}" \\\n\t"/tmp/native-${DISPLAY_NUM}"\n')
        preserved = m.preserving_stop(source)
        self.assertNotIn('"${CHROME_PROFILE}"', preserved)
        self.assertIn('sand_desktop_unregister_group', preserved)
        self.assertIn('"/tmp/native-${DISPLAY_NUM}"', preserved)
        for changed in [source + '\t"${CHROME_PROFILE}" \\\n', source.replace('WINDOW_TOKEN_DIR=', 'UNKNOWN='),
                        source + 'rm "${CHROME_PROFILE}"\n']:
            with self.assertRaises(RuntimeError):
                m.preserving_stop(changed)

    def test_owner_and_native_running_state_are_revalidated_under_window_lock(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); locks = sd / 'locks'; locks.mkdir(); tokens = sd / 'tokens'; tokens.mkdir()
            seats = sd / 'seats.json'; seats.write_text(json.dumps(self.table()))
            (sd / 'recovery-snapshots').mkdir()
            (sd / 'openbot-box-remint.json').write_text(json.dumps(self.journal()))
            item = m.candidates(self.journal(), self.table(), self.rows())[0]
            owner = tokens / '2'; owner.write_text('a-new-owner')
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'SEATS', seats), \
                    mock.patch.object(m, 'LOCKS', locks), mock.patch.object(m, 'TOKENS', tokens), \
                    mock.patch.object(m, 'window_alive', return_value=False), \
                    mock.patch.object(m, 'native_rows', return_value=self.rows()) as rows, \
                    mock.patch.object(m.subprocess, 'run') as run:
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'owner-changed'); run.assert_not_called()
                owner.write_text(item['token'])
                changed = self.rows(); changed[0]['isRunning'] = True; rows.return_value = changed
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'changed'); run.assert_not_called()
                rows.return_value = self.rows(); run.return_value.returncode = 0
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'closed')
                self.assertEqual(run.call_args.args[0], ['/bin/bash', str(sd / 'stop.sh'), '2'])
                table = self.table(); del table['assignments'][A]; del table['tokens'][A]
                self.assertEqual(json.loads(seats.read_text()), table)
                self.assertEqual(len(list((sd / 'recovery-snapshots').glob('retired-seat-before-*'))), 1)

    def test_capacity_accounts_for_missing_current_bots_without_reclaiming_them(self):
        table = self.table()
        self.assertTrue(m.capacity(self.journal(), table, max_windows=5)['pressured'])
        self.assertFalse(m.capacity(self.journal(), table, max_windows=100)['pressured'])
        journal = {'entries': [{'newId': '00000000-0000-4000-8000-' + str(i).zfill(12)}
                               for i in range(1, 31)]}
        self.assertEqual(m.capacity(journal, table)['requiredFreeWindows'], 26)

    def test_sufficient_memory_and_capacity_do_not_read_native_roster_or_stop(self):
        with mock.patch.object(m, 'available_memory', return_value=m.LOW_MEMORY_BYTES), \
                mock.patch.object(pathlib.Path, 'exists', return_value=True), \
                mock.patch.object(m, 'owned_json', side_effect=[self.journal(), self.table()]), \
                mock.patch.object(m, 'native_rows') as rows, mock.patch.object(m, 'close_candidate') as close:
            self.assertEqual(m.reap()['state'], 'sufficient-capacity')
            rows.assert_not_called(); close.assert_not_called()

    def test_healthy_ram_does_not_hide_seat_exhaustion(self):
        with mock.patch.object(m, 'available_memory', return_value=8 * m.LOW_MEMORY_BYTES), \
                mock.patch.object(pathlib.Path, 'exists', return_value=True), \
                mock.patch.object(m, 'owned_json', side_effect=[self.journal(), self.table()]), \
                mock.patch.object(m, 'capacity', return_value={'pressured': True}), \
                mock.patch.object(m, 'native_rows', side_effect=RuntimeError('roster consulted')):
            with self.assertRaisesRegex(RuntimeError, 'roster consulted'):
                m.reap()

    def fixture(self, root):
        sd = pathlib.Path(root); (sd / 'locks').mkdir(); (sd / 'tokens').mkdir()
        (sd / 'recovery-snapshots').mkdir()
        table = self.table(); table['unstarted'] = {'old-owner': 1, 'current-owner': 2}
        table['otherNativeMetadata'] = {'preserve': True}
        (sd / 'seats.json').write_text(json.dumps(table))
        (sd / 'openbot-box-remint.json').write_text(json.dumps(self.journal()))
        return sd, table, m.candidates(self.journal(), table, self.rows())[0]

    def patches(self, sd):
        from contextlib import ExitStack
        stack = ExitStack()
        for name, value in [('SD', sd), ('SEATS', sd / 'seats.json'), ('LOCKS', sd / 'locks'), ('TOKENS', sd / 'tokens')]:
            stack.enter_context(mock.patch.object(m, name, value))
        stack.enter_context(mock.patch.object(m, 'native_rows', return_value=self.rows()))
        return stack

    def test_absent_owner_only_releases_a_dead_retired_display_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            sd, table, item = self.fixture(root)
            with self.patches(sd), mock.patch.object(m, 'window_alive', return_value=True):
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'unowned-live-window')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)
            with self.patches(sd), mock.patch.object(m, 'window_alive', return_value=False):
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'released')
                new = json.loads((sd / 'seats.json').read_text())
                del table['assignments'][A]; del table['tokens'][A]; del table['unstarted']['old-owner']
                self.assertEqual(new, table)
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'changed')
                backup = next((sd / 'recovery-snapshots').glob('retired-seat-before-*'))
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual((sd / 'seats.json').stat().st_mode & 0o777, 0o600)

    def test_table_lock_contention_and_shared_token_never_release(self):
        with tempfile.TemporaryDirectory() as root:
            sd, table, item = self.fixture(root)
            with self.patches(sd):
                lock = sd / 'locks' / ('file' + str(sd / 'seats.json').replace('/', '_'))
                with lock.open('a') as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertEqual(m.release_seat(item), 'table-busy')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)
                table['tokens'][D] = item['token']; (sd / 'seats.json').write_text(json.dumps(table))
                self.assertEqual(m.release_seat(item), 'shared-token')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)

    def test_cas_detects_an_external_writer_and_does_not_overwrite_their_data(self):
        with tempfile.TemporaryDirectory() as root:
            sd, table, item = self.fixture(root)
            read = m.owned_json; calls = 0
            def concurrent(path):
                nonlocal calls
                if path == sd / 'seats.json':
                    calls += 1
                    if calls == 2:
                        table['externalWriter'] = 'kept'
                        path.write_text(json.dumps(table))
                return read(path)
            with self.patches(sd), mock.patch.object(m, 'owned_json', side_effect=concurrent):
                self.assertEqual(m.release_seat(item), 'table-changed')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)
                self.assertFalse(list(sd.glob('.retired-seat-cas-*')))

    def test_shutdown_still_live_or_failed_keeps_seat(self):
        with tempfile.TemporaryDirectory() as root:
            sd, table, item = self.fixture(root)
            (sd / 'tokens' / '2').write_text(item['token'])
            with self.patches(sd), mock.patch.object(m, 'window_alive', return_value=True), \
                    mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=0)):
                self.assertEqual(m.close_candidate(item, sd / 'stop.sh'), 'shutdown-pending')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)
            with self.patches(sd), mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=1)):
                with self.assertRaisesRegex(RuntimeError, 'shutdown failed'):
                    m.close_candidate(item, sd / 'stop.sh')
                self.assertEqual(json.loads((sd / 'seats.json').read_text()), table)


if __name__ == '__main__':
    unittest.main()
