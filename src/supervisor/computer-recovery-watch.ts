import { SAND_DATA, type AbsPath } from "../domain/types.ts";
import { type SupervisorDeps } from "./observe.ts";
import { joinAbs, parseAbsPath } from "./paths.ts";
import { runNetworkRecoveryWatchTick, type NetworkRecoveryWatchState } from "./network-recovery-watch.ts";

/** Owned network recovery plus the explicitly opted-in local-engine watcher. */
export const COMPUTER_RECOVERY_INTERVAL_MS = 15_000;
export type ComputerRecoveryWatchState = { notBeforeMs: number; network?: NetworkRecoveryWatchState };
export type ComputerRecoveryOutcome = "disabled" | "unsupported-path" | "uninstalled" | "conflict" | "running" | "starting" | "started";

export function runComputerRecoveryWatchTick(
  deps: SupervisorDeps,
  state: ComputerRecoveryWatchState,
  nowMs = Date.now(),
): ComputerRecoveryOutcome {
  const network = state.network ??= { notBeforeMs: 0 };
  try { network.lastOutcome = runNetworkRecoveryWatchTick(deps, network, nowMs); }
  catch { network.lastOutcome = "failed"; }
  // An optional network failure cannot prevent local-engine recovery.
  const data = deps.paths.sandData;
  const mode = deps.fs.read(deps.paths.mode)?.trim();
  if ((mode !== "custom" && mode !== "official") ||
      !deps.fs.exists(joinAbs(data, "openbot-local-only.enable"))) return "disabled";
  // The native injectors still embed the factory data root. Do not start a
  // watcher that would repair a different installation from the one selected.
  if (data !== SAND_DATA) return "unsupported-path";
  const helpers = [
    joinAbs(deps.paths.repoRoot, "recovery/hooks/computer-recovery.py"),
    joinAbs(data, "openbot-hooks/computer-recovery.py"),
  ];
  const script = helpers.find(path => deps.fs.exists(path));
  if (script === undefined) return "uninstalled";
  const pidFile = joinAbs(data, "computer-recovery.pid");
  const pid = deps.procs.readPidFile(pidFile);
  if (pid !== undefined && deps.procs.pidAlive(pid)) {
    // A recycled pidfile must not make a foreign process count as our watcher.
    let argv: string[] = [];
    try { argv = (deps.fs.read(parseAbsPath(`/proc/${String(pid)}/cmdline`)) ?? "").split("\0"); }
    catch { /* A missing process is retried below; the Python lock excludes duplicate daemons. */ }
    while (argv.at(-1) === "") argv.pop();
    if (argv[0] === "/usr/bin/python3" && helpers.some(path => path === argv[1])) {
      const args = argv.slice(2);
      const configured = args.length === 3 && args[0] === "--host" &&
        args[1] === deps.paths.hostMain && args[2] === "--watch";
      // Existing factory daemons predate --host. Keep their lifetime lock and
      // PID intact only when their default host is the selected native host.
      const legacy = deps.paths.hostMain === "/opt/sand/sand-host/host-main.cjs" &&
        args.length === 1 && args[0] === "--watch";
      return configured || legacy ? "running" : "conflict";
    }
  }
  if (nowMs < state.notBeforeMs) return "starting";
  state.notBeforeMs = nowMs + 60_000;
  deps.procs.start({ command: "/usr/bin/python3", argv: [script, "--host", deps.paths.hostMain, "--watch"], env: {},
    log: joinAbs(data, "computer-recovery.log") as AbsPath, pidFile, writePidFile: false });
  return "started";
}
