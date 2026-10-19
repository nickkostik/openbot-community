import importlib.util
import json
import pathlib
import shutil
import subprocess
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('gate_inject', HERE / 'gate-inject.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
local_spec = importlib.util.spec_from_file_location('gate_local_mode', HERE / 'local-mode-inject.py')
local_mode = importlib.util.module_from_spec(local_spec)
local_spec.loader.exec_module(local_mode)

FEATURE_READER = '''class Experiments {
  checkFeatureGate(name17, options2) { return true; }
}
'''
HARD_GATE = '''const boxHarnessTurnGate = {
      isBlocked: () => true,
      reportBlocked: () => {},
      reportServerHostedSkipped: () => {}
    };
'''
LEGACY_GATE = HARD_GATE.replace('() => true,', '() => experiments.checkFeatureGate("grok_bot_box_harness_turns_blocked"),')
FEATURE_KEYS = ('grok_bot_box_harness_turns_blocked', 'grok_bot_temporal_harness',
                'sand_create_temporal_agents', 'sand_working_state_warming')
UNRELATED_KEYS = ('unrelated', 'sand_working_state_warming_extra',
                  'sand_working_state_export', 'sand_working_state_snapshot')
PREVIOUS_FEATURE_LINE = ('\n    if (openbotLocalFleetEnabled() && '
    '(name17 === "grok_bot_box_harness_turns_blocked" || '
    'name17 === "grok_bot_temporal_harness" || name17 === "sand_create_temporal_agents")) '
    '{ return false; /* openbot-force-local-infer */ }\n')
CURRENT_FEATURE_LINE = PREVIOUS_FEATURE_LINE.replace(
    'name17 === "sand_create_temporal_agents"',
    'name17 === "sand_create_temporal_agents" || name17 === "sand_working_state_warming"')

# Test-owned callers isolate the optional background task from direct user work.
WARMER_CALLERS = '''
function runOptionalWarmer(experiments, events) {
  if (!experiments.checkFeatureGate("sand_working_state_warming", {upstream: true})) return "disabled";
  events.push("background-warm");
  return "warmed";
}
function exportWorkingState(events) { events.push("export"); return "exported"; }
function snapshotWorkingState(events) { events.push("snapshot"); return "snapshotted"; }
function openAgentSession(events) { events.push("session"); return "opened"; }
'''


class GateInjectTests(unittest.TestCase):
    def test_old_marker_does_not_skip_new_hardcoded_gate(self):
        old_patch = m.patch(FEATURE_READER + LEGACY_GATE)
        updated_host = old_patch.replace(LEGACY_GATE, HARD_GATE)
        result = m.patch(updated_host)
        self.assertIn(m.TURN_MARK, result)
        self.assertEqual(result.count(m.MARK), 1)
        self.assertEqual(m.patch(result), result)

    def test_legacy_feature_gated_host_is_preserved(self):
        result = m.patch(FEATURE_READER + LEGACY_GATE)
        self.assertIn(LEGACY_GATE, result)
        self.assertNotIn(m.TURN_MARK, result)
        self.assertEqual(m.patch(result), result)

    def test_ambiguous_unknown_or_drifted_gates_are_refused(self):
        for source in (FEATURE_READER, FEATURE_READER + HARD_GATE * 2,
                       FEATURE_READER + HARD_GATE.replace('() => true,', '() => newGate(),')):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                m.patch(source)
        patched = m.patch(FEATURE_READER + HARD_GATE)
        for source in (patched.replace('return false;', 'return true;', 1),
                       patched + ' /* ' + m.TURN_MARK + ' */'):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                m.patch(source)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for native gate execution')
    def test_explicit_custom_local_mode_accepts_sends_and_runs(self):
        source = m.patch(FEATURE_READER + HARD_GATE)
        # Execute the native send/run gate semantics with a simulated filesystem.
        # This catches a cosmetic marker patch that still refuses actual turns.
        script = '''const vm = require('node:vm');
const source = SOURCE;
const result = [];
for (const test of [
  { flag: true, mode: 'custom', blocked: false },
  { flag: true, mode: 'custom\\n', blocked: false },
  { flag: false, mode: 'custom', blocked: true },
  { flag: true, mode: 'official', blocked: true },
  { flag: true, mode: 'other', blocked: true },
  { flag: true, mode: 'custom', readFails: true, blocked: true }
]) {
  const context = { require: (name) => {
    if (name !== 'node:fs') throw Error('unexpected module');
    return { existsSync: () => test.flag, readFileSync: () => {
      if (test.readFails) throw Error('read failed');
      return test.mode;
    }};
  }};
  vm.runInNewContext(source + `
    function refuseBlockedBoxHarnessSend(gate) {
      if (!gate.isBlocked()) return;
      throw new Error('This Bot is unavailable');
    }
    let accepted = false;
    try { refuseBlockedBoxHarnessSend(boxHarnessTurnGate); accepted = true; } catch {}
    const runner = { run: () => boxHarnessTurnGate.isBlocked() ? 'aborted' : 'executed' };
    globalThis.result = { blocked: boxHarnessTurnGate.isBlocked(), accepted, run: runner.run() };
  `, context);
  if (context.result.blocked !== test.blocked || context.result.accepted === test.blocked ||
      context.result.run !== (test.blocked ? 'aborted' : 'executed')) throw Error('incorrect gate behavior');
  result.push(context.result);
}
console.log(JSON.stringify(result));
'''.replace('SOURCE', json.dumps(source))
        completed = subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)
        self.assertEqual(len(json.loads(completed.stdout)), 6)

    def test_existing_both_mode_gate_upgrades_once_to_custom_only(self):
        legacy = m.patch(FEATURE_READER + HARD_GATE).replace(m.TURN_REPLACEMENT, m.BOTH_MODE_TURN_REPLACEMENT)
        updated = m.patch(legacy)
        self.assertIn(m.TURN_REPLACEMENT, updated)
        self.assertNotIn(m.BOTH_MODE_TURN_REPLACEMENT, updated)
        self.assertEqual(m.patch(updated), updated)

    def test_official_feature_reads_keep_upstream_temporal_and_retirement_values(self):
        source = m.patch(FEATURE_READER + HARD_GATE)
        script = """const vm=require('node:vm'),assert=require('node:assert/strict');
for(const local of [true,false]){
 const context={openbotLocalFleetEnabled:()=>local};
 vm.runInNewContext(SOURCE + 'globalThis.experiments=new Experiments();',context);
 for(const key of ['grok_bot_box_harness_turns_blocked','grok_bot_temporal_harness','sand_create_temporal_agents'])
   assert.equal(context.experiments.checkFeatureGate(key,{}),!local);
 assert.equal(context.experiments.checkFeatureGate('unrelated',{}),true);
}
""".replace('SOURCE',json.dumps(source))
        subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)

    def test_exact_previous_three_flag_line_upgrades_once_without_touching_turn_gate(self):
        current = m.patch(FEATURE_READER + HARD_GATE)
        self.assertEqual(current.count(CURRENT_FEATURE_LINE), 1)
        previous = current.replace(CURRENT_FEATURE_LINE, PREVIOUS_FEATURE_LINE)
        upgraded = m.patch(previous)
        self.assertEqual(upgraded, current)
        self.assertEqual(m.patch(upgraded), upgraded)
        self.assertEqual(upgraded.count(m.MARK), 1)
        self.assertEqual(upgraded.count('"sand_working_state_warming"'), 1)
        # This feature-reader extension must preserve the existing native send/run
        # gate replacement and its two notification callbacks byte for byte.
        turn = current[current.index(m.TURN_ANCHOR):]
        self.assertEqual(previous[previous.index(m.TURN_ANCHOR):], turn)
        self.assertEqual(upgraded[upgraded.index(m.TURN_ANCHOR):], turn)
        self.assertIn(m.TURN_REPLACEMENT, turn)
        self.assertIn('reportBlocked: () => {},\n      reportServerHostedSkipped: () => {}', turn)

    def test_exact_unscoped_legacy_line_upgrades_to_four_mode_scoped_flags(self):
        current = m.patch(FEATURE_READER + HARD_GATE)
        unscoped = PREVIOUS_FEATURE_LINE.replace('openbotLocalFleetEnabled() && (', '', 1).replace(
            ')) { return false;', ') { return false;', 1)
        previous = current.replace(CURRENT_FEATURE_LINE, unscoped)
        self.assertEqual(m.patch(previous), current)
        self.assertEqual(m.patch(m.patch(previous)), current)

    def test_feature_upgrade_refuses_drift_duplicates_and_partial_markers(self):
        patched = m.patch(FEATURE_READER + HARD_GATE)
        previous = patched.replace(CURRENT_FEATURE_LINE, PREVIOUS_FEATURE_LINE)
        variants = (
            patched.replace('"sand_working_state_warming"', '"sand_working_state_warming_wrong"'),
            patched.replace('openbotLocalFleetEnabled() && (', 'true && (', 1),
            patched.replace(')) { return false;', ')) { return true;', 1),
            patched.replace('/* ' + m.MARK + ' */', '/* ' + m.MARK + '-incomplete */'),
            patched.replace('/* ' + m.MARK + ' */', '/* ' + m.MARK),
            patched + '\n/* ' + m.MARK + ' */\n',
            previous.replace(PREVIOUS_FEATURE_LINE, PREVIOUS_FEATURE_LINE * 2),
            previous.replace('name17 === "grok_bot_temporal_harness"', 'name17 === "other_flag"'),
            patched + FEATURE_READER,
        )
        for source in variants:
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                m.patch(source)

    def test_feature_upgrade_tracks_renamed_valid_reader_parameters(self):
        reader = FEATURE_READER.replace('name17, options2', 'gate$name, opts_2')
        current = m.patch(reader + HARD_GATE)
        previous_line = PREVIOUS_FEATURE_LINE.replace('name17', 'gate$name')
        current_line = CURRENT_FEATURE_LINE.replace('name17', 'gate$name')
        self.assertEqual(current.count(current_line), 1)
        self.assertEqual(m.patch(current.replace(current_line, previous_line)), current)
        self.assertEqual(m.patch(current), current)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for feature gate execution')
    def test_four_flags_obey_custom_local_mode_and_unrelated_flags_keep_upstream_values(self):
        reader = FEATURE_READER.replace('return true;',
            'globalThis.upstreamCalls.push({name:name17, options:options2}); return options2.upstream;')
        source = m.patch(reader + HARD_GATE)
        script = r'''const vm = require('node:vm'), assert = require('node:assert/strict');
const cases = [
  {name:'custom-local', flag:true, mode:'custom', enabled:true},
  {name:'custom-local-newline', flag:true, mode:'custom\n', enabled:true},
  {name:'custom-local-off', flag:false, mode:'custom', enabled:false},
  {name:'mode-missing', flag:true, modeError:'ENOENT', enabled:false},
  {name:'mode-unreadable', flag:true, modeError:'EACCES', enabled:false},
  {name:'official-local', flag:true, mode:'official', enabled:false},
  {name:'official-local-off', flag:false, mode:'official', enabled:false},
  {name:'unknown-mode', flag:true, mode:'other', enabled:false},
  {name:'uppercase-custom', flag:true, mode:'Custom', enabled:false}
];
for (const test of cases) {
  const context = {upstreamCalls:[], require: module => {
    assert.equal(module, 'node:fs');
    return {
      existsSync: path => { assert.equal(path, '/home/box/sand-data/openbot-local-only.enable'); return test.flag; },
      readFileSync: (path, encoding) => {
        assert.equal(path, '/home/box/sand-data/openbot-mode'); assert.equal(encoding, 'utf8');
        if (test.modeError) throw Object.assign(new Error('mode read failed'), {code:test.modeError});
        return test.mode;
      }
    };
  }};
  vm.runInNewContext(MODE_HELPER + SOURCE + '\nglobalThis.experiments = new Experiments(); globalThis.blocked = boxHarnessTurnGate.isBlocked();', context);
  assert.equal(context.blocked, !test.enabled, test.name + ' existing BOX turn gate');
  for (const upstream of [true, false]) {
    for (const key of FEATURE_KEYS) {
      const options = {upstream}, count = context.upstreamCalls.length;
      assert.equal(context.experiments.checkFeatureGate(key, options), test.enabled ? false : upstream,
        test.name + ': ' + key);
      assert.equal(context.upstreamCalls.length, count + (test.enabled ? 0 : 1));
      if (!test.enabled) assert.equal(context.upstreamCalls.at(-1).options, options);
    }
    for (const key of UNRELATED_KEYS) {
      const options = {upstream}, count = context.upstreamCalls.length;
      assert.equal(context.experiments.checkFeatureGate(key, options), upstream,
        test.name + ': unrelated ' + key);
      assert.equal(context.upstreamCalls.length, count + 1);
      assert.equal(context.upstreamCalls.at(-1).options, options);
    }
  }
}
console.log(JSON.stringify({cases:cases.length, keys:FEATURE_KEYS.length}));
'''.replace('SOURCE', json.dumps(source)).replace('FEATURE_KEYS', json.dumps(FEATURE_KEYS)).replace(
            'UNRELATED_KEYS', json.dumps(UNRELATED_KEYS)).replace('MODE_HELPER', json.dumps(local_mode.MODE_POLICY_STOCK))
        result = subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {'cases': 9, 'keys': 4})

    @unittest.skipUnless(shutil.which('node'), 'Node is required for isolated warmer execution')
    def test_synthetic_optional_warmer_is_gated_while_direct_work_remains_unchanged(self):
        reader = FEATURE_READER.replace('return true;', 'return options2.upstream;')
        original = reader + HARD_GATE + WARMER_CALLERS
        source = m.patch(original)
        self.assertTrue(source.endswith(WARMER_CALLERS))
        self.assertEqual(source.count('sand_working_state_warming'), original.count('sand_working_state_warming') + 1)
        script = r'''const vm = require('node:vm'), assert = require('node:assert/strict');
for (const local of [true, false]) {
  const context = {openbotLocalFleetEnabled:()=>local};
  vm.runInNewContext(SOURCE + `
    const events = [], experiments = new Experiments();
    globalThis.result = {warm:runOptionalWarmer(experiments, events),
      exported:exportWorkingState(events), snapshot:snapshotWorkingState(events),
      session:openAgentSession(events), events};`, context);
  assert.equal(context.result.warm, local ? 'disabled' : 'warmed');
  assert.equal(context.result.exported, 'exported');
  assert.equal(context.result.snapshot, 'snapshotted');
  assert.equal(context.result.session, 'opened');
  assert.deepEqual(Array.from(context.result.events), local ? ['export','snapshot','session'] :
    ['background-warm','export','snapshot','session']);
}
'''.replace('SOURCE', json.dumps(source))
        subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)


if __name__ == '__main__':
    unittest.main()
