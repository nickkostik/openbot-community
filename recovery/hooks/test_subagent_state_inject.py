import importlib.util
import pathlib
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('subagent_state', pathlib.Path(__file__).with_name('subagent-state-inject.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NATIVE = '''function sample(host) {
  function createSubagentRunner(agentId, args, directionEpoch, loopDetection, inherited) {
    host.subagentOwnership?.assertOpen();
    return host.constructRunner({
      transcriptMirror: host.transcriptMirror,
      subagentTranscriptId: agentId
    });
  }
  function dispatchAutomationSubagent(args) { return args; }
  return {
      setLocalState: (state) => {
        this.state = state;
      },
      run(options2, settle, baseState, privacyMode) {
        const transcriptPersistence = options2.prewarm === true ? "disabled" : settle.noteBaseState(baseState, privacyMode);
        return transcriptPersistence;
      }
  };
}
function journalGuard(previous, current) {
  if (previous !== current) throw new Error('turn started from a checkpoint other than the journal cursor');
}
'''


class WorkerStateInjectionTests(unittest.TestCase):
    def test_native_runner_and_settle_are_patched_without_weakening_journal_guard(self):
        patched = m.patch(NATIVE)
        checked = subprocess.run(['node', '--check'], input=patched, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('runner.fallbackBlobStore = openbotSavedWorker.blobStore', patched)
        self.assertIn('setPersistenceAllowed?.(transcriptPersistence === "enabled")', patched)
        self.assertEqual(patched[patched.index('function journalGuard'):], NATIVE[NATIVE.index('function journalGuard'):])
        self.assertEqual(m.patch(patched), patched)

    def test_anchor_drift_and_partial_existing_patch_are_refused(self):
        patched = m.patch(NATIVE)
        for value in [NATIVE.replace('return host.constructRunner', 'return another'),
                      NATIVE.replace('dispatchAutomationSubagent', 'other'),
                      NATIVE + NATIVE, patched.replace('saveCheckpoint(state.toBinary())', 'saveSomething(state)'),
                      patched.replace('runner.state = ConversationStateStructure.fromBinary(openbotCheckpoint);', ''),
                      patched + m.MARKER]:
            with self.assertRaises(RuntimeError):
                m.patch(value)


if __name__ == '__main__':
    unittest.main()
