import { type SupervisorDeps } from "./observe.ts";
import { joinAbs, parseAbsPath } from "./paths.ts";

export type NetworkRecoveryOutcome = "disabled" | "invalid-policy" | "uninstalled" | "running" | "starting" | "started";
export type NetworkRecoveryWatchState = { notBeforeMs: number; lastOutcome?: NetworkRecoveryOutcome | "failed" };

/** The saved network opt-in is independent of mode and agent harness. */
export function runNetworkRecoveryWatchTick(
  deps: SupervisorDeps,
  state: NetworkRecoveryWatchState,
  nowMs = Date.now(),
): NetworkRecoveryOutcome {
  const data = deps.paths.sandData;
  const raw = deps.fs.read(joinAbs(data, "openbot-tailscale.json"));
  if (raw === undefined) return "disabled";
  if (Buffer.byteLength(raw, "utf8") > 4096) return "invalid-policy";
  let settings: unknown;
  try { settings = JSON.parse(raw); }
  catch { return "invalid-policy"; }
  if (settings === null || typeof settings !== "object" || Array.isArray(settings) ||
      !("schemaVersion" in settings) || settings.schemaVersion !== 1 ||
      !("enabled" in settings) || typeof settings.enabled !== "boolean") return "invalid-policy";
  if (!settings.enabled) return "disabled";

  const helpers = [
    joinAbs(deps.paths.repoRoot, "recovery/hooks/tailscale-recovery.py"),
    joinAbs(data, "openbot-hooks/tailscale-recovery.py"),
  ];
  const script = helpers.find(path => deps.fs.exists(path));
  if (script === undefined) return "uninstalled";
  const pidFile = joinAbs(data, "tailscale/recovery-watch.pid");
  const pid = deps.procs.readPidFile(pidFile);
  if (pid !== undefined && deps.procs.pidAlive(pid)) {
    let argv: string[] = [];
    try { argv = (deps.fs.read(parseAbsPath(`/proc/${String(pid)}/cmdline`)) ?? "").split("\0"); }
    catch { /* Process exit during inspection is retried below. */ }
    if (argv[0] === "/usr/bin/python3" && helpers.some(path => path === argv[1]) &&
        argv[2] === "--sand-data" && argv[3] === data && argv[4] === "--watch" &&
        argv.slice(5).every(value => value === "")) return "running";
  }
  if (nowMs < state.notBeforeMs) return "starting";
  state.notBeforeMs = nowMs + 60_000;
  deps.procs.start({ command: "/usr/bin/python3", argv: [script, "--sand-data", data, "--watch"], env: {},
    log: joinAbs(data, "openbot-network-recovery-watch.log"), pidFile, writePidFile: false });
  // The Python watcher owns its lifetime lock, validates owned paths/policy,
  // and publishes its own PID. A spawn alone is not proof of recovery.
  return "started";
}
