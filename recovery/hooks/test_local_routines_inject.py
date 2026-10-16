import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('inject', pathlib.Path(__file__).with_name('local-routines-inject.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

SOURCE = '''    const automationSyncFailureTrayIds = /* @__PURE__ */ new Map();
      fire: (args) => transcript.runServerScheduledAutomation(args),
    hub.start();
    const stopAuthenticatedReconcile = reconcileWhenAuthenticated({
    context2.onStop(async () => {
      stopAuthenticatedReconcile();
      suspendWakes: async () => {
        watcher.suspend();
      resumeWakes: () => {
        watcher.resume();
'''


class PatchTests(unittest.TestCase):
    def test_fixed_point_keeps_native_runner_and_every_schedule_parser(self):
        patched = module.patch(SOURCE)
        self.assertEqual(module.patch(patched), patched)
        self.assertIn('parseEverySchedule,', patched)
        self.assertIn('transcript.runServerScheduledAutomation(args)', patched)
        self.assertIn('openbotLocalRoutineScheduler?.suspend()', patched)
        self.assertIn('openbotLocalRoutineScheduler?.resume()', patched)
        self.assertIn('openbotLocalRoutineScheduler?.stop()', patched)
        self.assertIn('isPausingForUpgrade()', patched)

    def test_changed_or_ambiguous_anchor_does_not_partially_patch(self):
        for source in (SOURCE.replace('hub.start();', 'hub.begin();'), SOURCE + SOURCE):
            with self.assertRaises(RuntimeError):
                module.patch(source)

    def test_partial_marker_is_rejected(self):
        with self.assertRaises(RuntimeError):
            module.patch(SOURCE + module.MARK)


if __name__ == '__main__':
    unittest.main()
