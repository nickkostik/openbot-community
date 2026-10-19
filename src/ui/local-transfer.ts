import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { SupervisorDeps } from "../supervisor/observe.ts";
import { joinAbs, parseAbsPath } from "../supervisor/paths.ts";

const execute = promisify(execFile);
const PHASES = new Set(["idle", "disabled", "starting", "checking-bindings", "copying", "registering", "waiting-for-publication", "waiting-for-rate-limit", "waiting-for-control-plane", "activating", "bound-box", "review-needed", "failed", "cancelled-mode-change"]);
export type LocalTransfer = {
  enabled: boolean; available: boolean; phase: string; workerRunning: boolean; lockHeld: boolean; publicationVerified: boolean; rosterComplete: boolean;
  untrackedSourceAgents?: number; untrackedVisibleAgents?: number; unknownSourceAgents?: number; hiddenArchiveAgents?: number; rosterCheckedAtMs?: number;
  jobId?: string; agentCount?: number; verifiedAgents?: number; updatedAtMs?: number;
  counts?: Record<string, number>; refusalPhase?: string; diagnosticCode?: string;
  diagnosticAssistant: "unavailable" | "running" | "available" | "failed" | "disabled"; diagnosticReason: string; diagnosticAdviceCode?: string;
  nativeAppDeliveryVerified: false; billingVerified: false;
};
const diagnostics = {
  diagnosticAssistant: "unavailable" as const,
  diagnosticReason: "No bounded local diagnostic assistant adapter is installed.",
  nativeAppDeliveryVerified: false as const, billingVerified: false as const,
};

/** Whitelist the wire contract: private error artifacts and identity IDs never leave the Computer. */
export function publicTransfer(raw: Record<string, unknown>, available = true): LocalTransfer {
  const value: LocalTransfer = { ...diagnostics, enabled: raw.enabled === true, available,
    phase: typeof raw.phase === "string" && PHASES.has(raw.phase) ? raw.phase : "idle", workerRunning: raw.workerRunning === true && raw.lockHeld === true, lockHeld: raw.lockHeld === true, publicationVerified: raw.publicationVerified === true, rosterComplete: raw.rosterComplete === true };
  if (value.phase === "bound-box" && !value.publicationVerified) value.phase = "waiting-for-publication";
  if (value.phase === "bound-box" && !value.rosterComplete) value.phase = "checking-bindings";
  if (["running", "available", "failed", "disabled"].includes(String(raw.diagnosticAssistant))) {
    value.diagnosticAssistant = raw.diagnosticAssistant as LocalTransfer["diagnosticAssistant"];
    value.diagnosticReason = "AI diagnostic review has no transfer or delivery authority.";
  }
  if (value.diagnosticAssistant === "available" && ["publication-backlog", "identity-review", "history-review", "provider-backoff", "control-plane-review", "insufficient-evidence"].includes(String(raw.diagnosticAdviceCode))) {
    value.diagnosticAdviceCode = String(raw.diagnosticAdviceCode);
  }
  if (typeof raw.jobId === "string" && /^[0-9a-f-]{36}$/.test(raw.jobId)) value.jobId = raw.jobId;
  for (const key of ["agentCount", "verifiedAgents", "updatedAtMs", "untrackedSourceAgents", "untrackedVisibleAgents", "unknownSourceAgents", "hiddenArchiveAgents", "rosterCheckedAtMs"] as const) {
    if (Number.isSafeInteger(raw[key]) && Number(raw[key]) >= 0) value[key] = Number(raw[key]);
  }
  if (typeof raw.refusalPhase === "string" && PHASES.has(raw.refusalPhase)) value.refusalPhase = raw.refusalPhase;
  if (["history-conflict", "identity-conflict", "preflight-refused"].includes(String(raw.diagnosticCode))) value.diagnosticCode = String(raw.diagnosticCode);
  if (raw.counts && typeof raw.counts === "object") {
    value.counts = {};
    for (const key of ["planned", "copied", "registered-box", "active"]) {
      const n = (raw.counts as Record<string, unknown>)[key];
      if (Number.isSafeInteger(n) && Number(n) >= 0) value.counts[key] = Number(n);
    }
  }
  return value;
}

export async function localTransfer(current: SupervisorDeps, action?: "start" | "resume",
  run: (command: string, argv: string[], options: { timeout: number; maxBuffer: number; encoding: "utf8" }) => Promise<{ stdout: string }> = execute): Promise<LocalTransfer> {
  const data = current.paths.sandData;
  const owned: NonNullable<typeof current.fs.readOwned> = (file, limit, directories) => {
    try { return current.fs.readOwned?.(file, limit, directories); }
    catch { return undefined; }
  };
  // Exact mode only: missing/invalid mode must never authorize a host mutation.
  const enabled = owned(current.paths.mode, 128, [data])?.trim() === "custom" &&
    owned(joinAbs(data, "openbot-local-only.enable"), 1024, [data]) !== undefined;
  if (action && !enabled) throw new Error("Select Custom mode on an opted-in Local installation before starting a transfer.");
  const helpers = [joinAbs(current.paths.repoRoot, "recovery/hooks/custom-dispatch-recovery.py"), joinAbs(data, "openbot-hooks/custom-dispatch-recovery.py")];
  const script = helpers.find(file => owned(file, 1024 * 1024, [parseAbsPath(path.dirname(file))]) !== undefined);
  if (!script) return publicTransfer({ enabled, phase: "disabled" }, false);
  const result = await run("/usr/bin/python3", [script, "--root", data, action ? "--request" : "--status"],
    { timeout: 10_000, maxBuffer: 64 * 1024, encoding: "utf8" });
  return publicTransfer(JSON.parse(String(result.stdout)) as Record<string, unknown>);
}
