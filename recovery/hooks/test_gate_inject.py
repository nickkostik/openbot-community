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


if __name__ == '__main__':
    unittest.main()
