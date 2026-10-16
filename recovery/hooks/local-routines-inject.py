"""Install the local clock beside native automation events, preserving the runner."""
import pathlib
import subprocess
import sys

MARK = 'openbot-local-routines-v1'
MODULE = '/home/box/sand-data/openbot-hooks/local-routine-scheduler.cjs'

START = '''    /* openbot-local-routines-v1: native clock adapter */
    try {
      const { LocalRoutineScheduler } = require("/home/box/sand-data/openbot-hooks/local-routine-scheduler.cjs");
      openbotLocalRoutineScheduler = new LocalRoutineScheduler({
        listAutomations: () => transcript.listAllAutomations(),
        runningAgentIds: async () => new Set((await transcript.listAgents()).filter((agent) => agent.isRunning).map((agent) => agent.id)),
        isLocalAgent: (agentId) => readHosting(agentId).harness === "box" && !readHosting(agentId).onSessionBox,
        cronSchedules: (automation) => triggerCronSchedules(automation.trigger),
        parseEverySchedule,
        computeNextRunAt: (schedule, afterMs) => computeNextRunAt(schedule, afterMs, context2.deps.settings.getUserTimeZone()),
        isReady: () => context2.deps["turn-execution"].canExecute && context2.deps["turn-execution"].isLocalWorkAllowed && !transcript.isPausingForUpgrade(),
        fire: (args) => transcript.runServerScheduledAutomation(args),
        log: context2.host.log
      });
      openbotLocalRoutineScheduler.start();
    } catch {
      context2.host.log("[openbot:local-routines] adapter unavailable; local clock remains stopped");
    }
'''


def patch(source):
    if MARK in source:
        if source.count(MARK) != 1 or MODULE not in source:
            raise RuntimeError('Incomplete local routine adapter')
        return source
    edits = [
        ('    const automationSyncFailureTrayIds = /* @__PURE__ */ new Map();',
         '    let openbotLocalRoutineScheduler;\n    const automationSyncFailureTrayIds = /* @__PURE__ */ new Map();'),
        ('      fire: (args) => transcript.runServerScheduledAutomation(args),',
         '      fire: (args) => openbotLocalRoutineScheduler?.owns(args.agentId, args.automation) ? openbotLocalRoutineScheduler.fireFromPlatform(args) : transcript.runServerScheduledAutomation(args),'),
        ('    hub.start();\n    const stopAuthenticatedReconcile = reconcileWhenAuthenticated({',
         '    hub.start();\n' + START + '    const stopAuthenticatedReconcile = reconcileWhenAuthenticated({'),
        ('    context2.onStop(async () => {\n      stopAuthenticatedReconcile();',
         '    context2.onStop(async () => {\n      openbotLocalRoutineScheduler?.stop();\n      stopAuthenticatedReconcile();'),
        ('      suspendWakes: async () => {\n        watcher.suspend();',
         '      suspendWakes: async () => {\n        openbotLocalRoutineScheduler?.suspend();\n        watcher.suspend();'),
        ('      resumeWakes: () => {\n        watcher.resume();',
         '      resumeWakes: () => {\n        openbotLocalRoutineScheduler?.resume();\n        watcher.resume();'),
    ]
    for old, new in edits:
        if source.count(old) != 1:
            raise RuntimeError('Native local routine adapter anchor changed')
        source = source.replace(old, new, 1)
    return source


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        file = pathlib.Path(arg)
        source = file.read_text(encoding='utf8')
        updated = patch(source)
        if updated == source:
            print(file, 'already patched')
            continue
        checked = subprocess.run(['node', '--check'], input=updated, text=True, capture_output=True)
        if checked.returncode:
            raise RuntimeError('Patched native bundle failed syntax check')
        temp = file.with_name(file.name + '.local-routines.tmp')
        with temp.open('w', encoding='utf8') as output:
            output.write(updated)
            output.flush()
            __import__('os').fsync(output.fileno())
        temp.chmod(file.stat().st_mode)
        temp.replace(file)
        print(file, 'local routine adapter installed')
