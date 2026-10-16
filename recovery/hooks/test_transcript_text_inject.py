import importlib.util
import json
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('transcript_text_inject', HERE / 'transcript-text-inject.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NATIVE = '''function withTimestampMs(entry) {
  if (isValidTimestampMs(entry.timestampMs)) return entry;
  return { ...entry, timestampMs: Date.now() };
}
function transcriptEntryOfJson(parsed2) {
  if (!isValidTranscriptEntry(parsed2)) return null;
  return withEmailDraftFrom(withVoiceCallNudgeCounts(withBoxEnvSecretTargetKeys(parsed2)));
}
'''


class TranscriptTextCompatibilityTests(unittest.TestCase):
    def execute(self, script):
        source = m.patch(NATIVE) + '''
function isValidTimestampMs(value) { return typeof value === "number" && value > 0; }
function isValidTranscriptEntry(value) { return value != null && typeof value.id === "string"; }
const withEmailDraftFrom = value => value;
const withVoiceCallNudgeCounts = value => value;
const withBoxEnvSecretTargetKeys = value => value;
''' + script
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_recovered_text_is_readable_and_push_preview_has_required_type(self):
        result = self.execute('''
const original = {kind:"send-message",id:"history-42",timestampMs:123,
  author:{id:"member",name:"Coder"},replyTo:"history-41",batchId:"batch",
  message:{content:"Recovered reply",channel:"chat",task_id:"task"}};
const loaded = transcriptEntryOfJson(JSON.parse(JSON.stringify(original)));
const preview = {v:1,predecessorMessageId:"history-41",message:{...loaded,message:structuredClone(loaded.message)}};
console.log(JSON.stringify({original,loaded,preview}));
''')
        self.assertNotIn('type', result['original']['message'])
        expected = {**result['original'], 'message': {**result['original']['message'], 'type': 'text'}}
        self.assertEqual(result['loaded'], expected)
        self.assertEqual(result['preview']['message'], expected)

    def test_live_write_normalizes_before_storing_and_publishing_without_mutating_input(self):
        result = self.execute('''
const original = {kind:"send-message",id:"live-1",timestampMs:456,message:{content:""}};
Object.freeze(original.message); Object.freeze(original);
const published = withTimestampMs(original);
const stored = JSON.parse(JSON.stringify(published));
console.log(JSON.stringify({stored,published,inputChanged:Object.hasOwn(original.message,"type")}));
''')
        self.assertEqual(result['stored'], result['published'])
        self.assertEqual(result['stored']['message'], {'content': '', 'type': 'text'})
        self.assertEqual(result['stored']['timestampMs'], 456)
        self.assertFalse(result['inputChanged'])

    def test_typed_messages_cards_attachments_and_nonmessage_entries_are_unchanged(self):
        result = self.execute('''
const inputs = [
 {kind:"send-message",id:"text",timestampMs:1,message:{type:"text",content:"OK",images:[{url:"image"}]}},
 {kind:"send-message",id:"attachment",timestampMs:1,message:{type:"attachment",url:"attachment"}},
 {kind:"send-message",id:"approval",timestampMs:1,message:{type:"permission-request",permission:{},content:"Keep"}},
 {kind:"message",id:"incoming",timestampMs:1,message:{content:"User message"}},
 {kind:"tool-call",id:"tool",timestampMs:1,message:{content:"Tool result"}}
];
console.log(JSON.stringify({same:inputs.every(x=>withTimestampMs(x)===x),inputs,loaded:inputs.map(transcriptEntryOfJson)}));
''')
        self.assertTrue(result['same'])
        self.assertEqual(result['loaded'], result['inputs'])

    def test_unknown_or_malformed_bodies_are_not_inferred_or_hidden(self):
        result = self.execute('''
const inputs = [null,{},[],
 {kind:"send-message",message:null},
 {kind:"send-message",message:[]},
 {kind:"send-message",message:{type:null,content:"Malformed"}},
 {kind:"send-message",message:{url:"Not text"}},
 {kind:"send-message",message:{content:42}}
];
console.log(JSON.stringify(inputs.map(x=>openbotNormalizeLegacyTextEntry(x)===x)));
''')
        self.assertTrue(all(result))

    def test_checked_patch_is_idempotent_and_accepts_only_complete_markers(self):
        patched = m.patch(NATIVE)
        self.assertEqual(m.patch(patched), patched)
        for bad in [patched.replace(m.READ_PATCH, m.READ_ANCHOR),
                    patched.replace(m.WRITE_PATCH, m.WRITE_ANCHOR),
                    patched.replace('type: "text"', 'type: "attachment"'),
                    patched + m.HELPER]:
            with self.assertRaisesRegex(RuntimeError, 'incomplete or drifted'):
                m.patch(bad)

    def test_changed_or_ambiguous_native_anchors_fail_before_writing(self):
        for bad in [NATIVE.replace(m.READ_ANCHOR, 'function differentRead(parsed2) {'),
                    NATIVE + NATIVE, NATIVE + 'const openbotNormalizeLegacyTextEntry = null;']:
            with self.assertRaises(RuntimeError):
                m.patch(bad)


if __name__ == '__main__':
    unittest.main()
