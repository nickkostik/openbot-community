import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('fleet_state_merge', pathlib.Path(__file__).with_name('fleet-state-merge.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
A = '00000000-0000-4000-8000-000000000001'
B = '00000000-0000-4000-8000-000000000002'
C = '00000000-0000-4000-8000-000000000003'


class MergeTests(unittest.TestCase):
    def test_identity_lineage_is_not_name_based(self):
        j = {'previousGenerations': [{'entries': [{'oldId': A, 'newId': B, 'name': 'Q'}]}],
             'entries': [{'oldId': B, 'newId': C, 'name': 'Different'}]}
        self.assertEqual(M.lineages(j), {C: [A, B, C]})
        for extra in ({'oldId': A, 'newId': C}, {'oldId': C, 'newId': A}):
            with self.assertRaises(ValueError):
                M.lineages({**j, 'entries': j['entries'] + [extra]})

    def test_same_id_different_user_messages_are_both_preserved(self):
        first = {'id': 't1u', 'timestampMs': 1, 'text': 'first'}
        second = {**first, 'text': 'second'}
        rows, conflicts = M.merge_entries({A: [first], B: [second]})
        self.assertEqual(conflicts, 1)
        self.assertEqual({r['entry']['text'] for r in rows}, {'first', 'second'})
        self.assertEqual(len({r['entry']['id'] for r in rows}), 2)
        self.assertEqual((rows, conflicts), M.merge_entries({B: [second], A: [first]}))
        self.assertEqual(first['id'], 't1u')

    def test_identical_payloads_coalesce_only_after_identity_mapping(self):
        first = {'id': 't1u', 'timestampMs': 1, 'agentId': A, 'text': 'keep tool output'}
        rows, conflicts = M.merge_entries({A: [first], C: [{**first, 'agentId': C}]}, {A: C})
        self.assertEqual(len(rows), 1)
        self.assertEqual(conflicts, 0)
        self.assertEqual(rows[0]['sources'], [A, C])

    def test_variant_alias_does_not_overwrite_an_existing_entry(self):
        a = {'id': 't1', 'timestampMs': 1, 'text': 'a'}
        b = {**a, 'text': 'b'}
        initial, _ = M.merge_entries({A: [a, b]})
        alias = next(r['entry']['id'] for r in initial if r['entry']['id'] != 't1')
        rows, _ = M.merge_entries({A: [a, b, {'id': alias, 'text': 'other', 'timestampMs': 2}]})
        self.assertEqual(len({r['entry']['id'] for r in rows}), 3)

    def test_owner_normalization_keeps_message_text_and_historical_tool_payloads(self):
        entry = {'id':'t1u', 'agentId':A, 'timestampMs':1,
                 'content':'Quoted bot ID ' + A,
                 'toolResult':{'agentId':A, 'output':A},
                 'richText':json.dumps({'text':A})}
        before = json.loads(json.dumps(entry))
        rows, conflicts = M.merge_entries({A:[entry], C:[{**entry,'agentId':C}]}, {A:C})
        self.assertEqual(conflicts, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['entry'], {**before, 'agentId':C})
        self.assertEqual(entry, before)

    def test_different_quoted_ids_are_not_deduplicated_as_owner_changes(self):
        first = {'id':'t1u', 'agentId':A, 'timestampMs':1, 'text':A}
        second = {**first, 'agentId':C, 'text':C}
        rows, conflicts = M.merge_entries({A:[first], C:[second]}, {A:C})
        self.assertEqual(conflicts, 1)
        self.assertEqual({row['entry']['text'] for row in rows}, {A,C})

    def test_jsonl_owner_remapping_preserves_payloads_and_unclassified_lines(self):
        entry = {'id':'t1u', 'agentId':A, 'content':{'agentId':A, 'text':A}}
        untouched = json.dumps({'text':A}) + '\n'
        text = json.dumps(entry) + '\r\n' + untouched + '{partial\n\n'
        remapped = M.remap_transcript_jsonl(text, {A:C})
        first, rest = remapped.split('\r\n', 1)
        self.assertEqual(json.loads(first), {**entry, 'agentId':C})
        self.assertEqual(rest, untouched + '{partial\n\n')
        self.assertEqual(M.remap_transcript_jsonl(remapped, {A:C}), remapped)

    def test_snapshot_preserves_database_sessions_instructions_and_disabled_routines(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = pathlib.Path(tmp)
            j = {'entries': [{'oldId': A, 'newId': B}]}
            (sd / 'openbot-box-remint.json').write_text(json.dumps(j))
            for aid, text in ((A, 'old'), (B, 'new')):
                d = sd / 'agents' / aid
                d.mkdir(parents=True)
                (d / 'instructions.md').write_text('Keep this complete role')
                (d / 'automation.json').write_text('{"enabled":false}')
                (d / 'external').symlink_to('/does-not-exist')
                with sqlite3.connect(d / 'store.db') as c:
                    c.execute('CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT,entry TEXT)')
                    c.execute('INSERT INTO transcript_entries VALUES(1,?,?)', ('t1', json.dumps({'id': 't1', 'timestampMs': 1, 'text': text})))
                    c.execute('CREATE TABLE automation_completion_inbox(id TEXT,text TEXT,acknowledged INTEGER)')
                    c.execute('INSERT INTO automation_completion_inbox VALUES("pending","no replay",0)')
                sessions = sd / 'agent-transcripts' / aid
                sessions.mkdir(parents=True)
                (sessions / 'session.jsonl').write_text('private session state')
            result = M.preserve(sd)
            root = pathlib.Path(result['snapshot'])
            self.assertEqual(result['branches'], 2)
            self.assertEqual(result['entries'], 2)
            self.assertFalse(result['pruneAllowed'])
            self.assertTrue(M.verify(root)['verified'])
            self.assertEqual((root / 'session-journals' / A / 'session.jsonl').read_text(), 'private session state')
            self.assertTrue((root / 'branches' / A / 'external').is_symlink())
            with sqlite3.connect(sd / 'agents' / A / 'store.db') as c:
                self.assertEqual(c.execute('SELECT acknowledged FROM automation_completion_inbox').fetchone()[0], 0)
            (root / 'branches' / A / 'instructions.md').write_text('lost')
            with self.assertRaises(RuntimeError):
                M.verify(root)

    def test_rejects_malformed_entry_instead_of_dropping_it(self):
        with self.assertRaises(ValueError):
            M.merge_entries({A: [{'text': 'user message without id'}]})


if __name__ == '__main__':
    unittest.main()
