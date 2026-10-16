"use strict";

// The native host parses schedules, but delegates clock dispatch to the server.
// Keep that parser and the native run path; replace only who owns the clock.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const MAX_CONCURRENT_BOTS = 12;

function atomicJson(file, value) {
  const temp = file + "." + crypto.randomUUID() + ".tmp";
  const fd = fs.openSync(temp, "wx", 0o600);
  try {
    fs.writeFileSync(fd, JSON.stringify(value) + "\n");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  try {
    fs.renameSync(temp, file);
    const dir = fs.openSync(path.dirname(file), "r");
    try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
  } finally {
    try { fs.unlinkSync(temp); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}

function readPolicy(root) {
  try {
    if (!fs.existsSync(path.join(root, "openbot-local-only.enable"))) return null;
    const mode = fs.readFileSync(path.join(root, "openbot-mode"), "utf8").trim();
    if (mode !== "custom" && mode !== "official") return null;
    const config = JSON.parse(fs.readFileSync(path.join(root, "openbot-local-routines.json"), "utf8"));
    if (config.enabled !== true) return null;
    try {
      if (JSON.parse(fs.readFileSync(path.join(root, "openbot-pause.json"), "utf8")).paused === true) return null;
    } catch (error) { if (error.code !== "ENOENT") return null; }
    const maxConcurrent = config.maxConcurrent ?? 2;
    const graceMs = config.graceMs ?? 120000;
    const queueTtlMs = config.queueTtlMs ?? 3600000;
    if (!Number.isInteger(maxConcurrent) || maxConcurrent < 1 || maxConcurrent > MAX_CONCURRENT_BOTS) return null;
    if (!Number.isInteger(graceMs) || graceMs < 0 || graceMs > 300000) return null;
    if (!Number.isInteger(queueTtlMs) || queueTtlMs < graceMs || queueTtlMs > 3600000) return null;
    let pausedBotIds = [];
    try {
      const paused = JSON.parse(fs.readFileSync(path.join(root, "openbot-pause-bots.json"), "utf8"));
      if (!Array.isArray(paused.pausedBotIds)) return null;
      pausedBotIds = paused.pausedBotIds;
    } catch (error) { if (error.code !== "ENOENT") return null; }
    return { maxConcurrent, graceMs, queueTtlMs, pausedBotIds };
  } catch { return null; }
}

function keyOf(agentId, automationId) { return agentId + "\0" + automationId; }
function finiteTime(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0; }

class LocalRoutineScheduler {
  constructor(deps) {
    this.deps = deps;
    this.now = deps.now ?? Date.now;
    this.root = deps.root ?? "/home/box/sand-data";
    this.dir = path.join(this.root, "local-routine-scheduler");
    fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    this.file = path.join(this.dir, "state.json");
    try {
      this.state = JSON.parse(fs.readFileSync(this.file, "utf8"));
      if (this.state.version !== 1 || !this.state.receipts || Array.isArray(this.state.receipts)) {
        throw new Error("invalid-scheduler-state");
      }
      for (const value of Object.values(this.state.receipts)) {
        if (!value || !finiteTime(value.scheduledForMs) || typeof value.runUuid !== "string") {
          throw new Error("invalid-scheduler-receipt");
        }
      }
      this.state.queue ??= {};
      if (!this.state.queue || typeof this.state.queue !== "object" || Array.isArray(this.state.queue)) throw new Error("invalid-scheduler-queue");
      for (const value of Object.values(this.state.queue)) {
        if (!value || !finiteTime(value.scheduledForMs) || !finiteTime(value.queuedAtMs) ||
            !finiteTime(value.expiresAtMs) || value.expiresAtMs < value.queuedAtMs ||
            value.expiresAtMs - value.queuedAtMs > 3600000 || typeof value.scheduleKey !== "string") throw new Error("invalid-scheduler-queued-slot");
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error; // Corrupt dedupe state must never cause a replay.
      this.state = { version: 1, receipts: {}, queue: {} };
    }
    this.pending = new Map();
    this.stopped = true;
    this.ticking = false;
    this.timer = null;
    this.faulted = false;
  }

  policy() { return (this.deps.getPolicy ?? (() => readPolicy(this.root)))(); }
  owns(agentId, automation) {
    return this.policy() !== null && this.deps.isLocalAgent(agentId) &&
      this.deps.cronSchedules(automation).length > 0;
  }
  isBotPaused(agentId, policy) { return policy.pausedBotIds?.includes(agentId) === true; }
  save() {
    try { atomicJson(this.file, this.state); }
    catch (error) { this.faulted = true; throw error; }
  }
  log(event, receipt = {}) {
    // No prompts, credentials, provider errors, or third-party message bodies.
    this.deps.log?.("[openbot:local-routines] " + JSON.stringify({ event, ...receipt }));
  }
  start() {
    if (this.timer !== null) return;
    this.stopped = false;
    this.timer = setInterval(() => { void this.tick(); }, this.deps.intervalMs ?? 15000);
    this.timer.unref();
    void this.tick();
  }
  stop() { this.stopped = true; clearInterval(this.timer); this.timer = null; }
  suspend() { this.stop(); }
  resume() { this.start(); }

  reconcile(rows, running) {
    const byKey = new Map(rows.map(row => [keyOf(row.agentId, row.automation.id), row.automation]));
    for (const [key, receipt] of Object.entries(this.state.receipts)) {
      if (receipt.phase === "finished") continue;
      const run = byKey.get(key)?.runs?.find(entry => entry.id === receipt.runUuid);
      if (run?.status === "running") receipt.phase = "running";
      else if (run && finiteTime(run.finishedAt)) {
        receipt.phase = "finished";
        receipt.status = run.status;
        receipt.finishedAtMs = run.finishedAt;
        receipt.executionVerified = run.status === "ok";
      } else if (!this.pending.has(key)) {
        receipt.phase = "uncertain";
        receipt.executionVerified = false;
        // A settled/restarted claim with no native run and an idle owner must
        // not occupy a lane forever. Keep its consumed slot; never replay it.
        if (!run && !running.has(key.split("\0")[0]) &&
            this.now() - (receipt.dispatchedAtMs ?? receipt.scheduledForMs) >= 300000) {
          receipt.phase = "finished";
          receipt.status = "unverified";
          receipt.finishedAtMs = this.now();
          receipt.completionReason = "idle-owner-without-native-run";
          this.log("claim-released", { agentId: key.split("\0")[0], runUuid: receipt.runUuid, ...receipt });
        }
      }
    }
  }

  recordCapacity(running, now, policy) {
    const claims = Object.entries(this.state.receipts).filter(([, receipt]) => receipt.phase !== "finished");
    const claimIds = new Set(claims.map(([key]) => key.split("\0")[0]));
    const pendingIds = new Set([...this.pending.keys()].map(key => key.split("\0")[0]));
    const busy = new Set([...running, ...claimIds, ...pendingIds]);
    this.state.runningAgentIds = [...running].sort();
    this.state.claimedAgentIds = [...claimIds].sort();
    this.state.pendingClaims = this.pending.size;
    this.state.openReceipts = claims.length;
    this.state.busyAgents = busy.size;
    this.state.capacityObservedAtMs = now;
    this.state.maxConcurrent = policy.maxConcurrent;
    this.state.queuedRoutines = Object.keys(this.state.queue).length;
    this.state.oldestQueuedAtMs = Math.min(...Object.values(this.state.queue).map(value => value.queuedAtMs), now);
    this.state.gateReason = !this.state.queuedRoutines ? "idle" : busy.size >= policy.maxConcurrent ? "at-capacity" : "awaiting-dispatch";
    return busy;
  }

  queuedSlot(key, slot, scheduleKey, now, policy) {
    const queue = this.state.queue;
    let entry = queue[key];
    if (entry && (entry.scheduleKey !== scheduleKey || entry.expiresAtMs < now ||
        entry.queuedAtMs + (policy.queueTtlMs ?? 3600000) < now ||
        (this.state.receipts[key]?.scheduledForMs ?? -1) >= entry.scheduledForMs)) {
      if (entry.expiresAtMs < now) this.log("queue-expired", { routineKey: key, scheduledForMs: entry.scheduledForMs });
      delete queue[key];
      entry = undefined;
    }
    if (slot !== null) {
      if (entry) entry.scheduledForMs = Math.max(entry.scheduledForMs, slot); // Consolidate recurring work, retaining its place.
      else entry = queue[key] = { scheduledForMs: slot, queuedAtMs: now,
        expiresAtMs: now + (policy.queueTtlMs ?? 3600000), scheduleKey };
    }
    return entry;
  }

  dueSlot(agentId, automation, now, policy) {
    const receipt = this.state.receipts[keyOf(agentId, automation.id)];
    const anchor = Math.max(automation.createdAt ?? 0, automation.lastRunAt ?? 0,
      receipt?.scheduledForMs ?? 0, now - policy.graceMs - 1);
    let latest = null;
    for (const schedule of this.deps.cronSchedules(automation)) {
      const every = this.deps.parseEverySchedule?.(schedule);
      if (every && every.phaseMs == null) {
        // An unphased interval is relative to the last run, not to each poll.
        // Advancing the anchor to now would perpetually postpone it.
        const base = Math.max(automation.lastRunAt ?? automation.createdAt ?? 0, receipt?.scheduledForMs ?? 0);
        const slot = base + Math.floor((now - base) / every.intervalMs) * every.intervalMs;
        if (slot > base && slot >= now - policy.graceMs) latest = latest === null ? slot : Math.max(latest, slot);
        continue;
      }
      let cursor = anchor;
      // Native authored schedules have a five-minute minimum. The bound also
      // protects against unexpected future formats returning a non-advancing slot.
      for (let i = 0; i < 256; i++) {
        const slot = this.deps.computeNextRunAt(schedule, cursor);
        if (!finiteTime(slot) || slot <= cursor || slot > now) break;
        latest = latest === null ? slot : Math.max(latest, slot);
        cursor = slot;
      }
    }
    return latest;
  }

  async tick() {
    if (this.ticking || this.stopped || this.faulted) return;
    this.ticking = true;
    try {
      const policy = this.policy();
      if (policy === null || !await this.deps.isReady()) {
        this.state.heartbeatMs = this.now();
        this.state.schedulerStatus = policy === null ? "disabled" : "waiting-for-local-readiness";
        this.state.gateReason = this.state.schedulerStatus;
        this.save();
        return;
      }
      const [rows, running] = await Promise.all([this.deps.listAutomations(), this.deps.runningAgentIds()]);
      if (this.stopped) return;
      this.reconcile(rows, running);
      const now = this.now();
      const due = [];
      let eligibleRoutines = 0;
      const eligibleKeys = new Set();
      for (const row of rows) {
        if (!row.automation.isEnabled || !this.owns(row.agentId, row.automation) || this.isBotPaused(row.agentId, policy)) continue;
        eligibleRoutines++;
        const key = keyOf(row.agentId, row.automation.id);
        eligibleKeys.add(key);
        const slot = this.dueSlot(row.agentId, row.automation, now, policy);
        const queued = this.queuedSlot(key, slot, JSON.stringify(this.deps.cronSchedules(row.automation)), now, policy);
        if (queued) due.push({ ...row, slot: queued.scheduledForMs, queuedAtMs: queued.queuedAtMs,
          lastDispatchedAtMs: this.state.receipts[key]?.dispatchedAtMs ?? -1 });
      }
      for (const key of Object.keys(this.state.queue)) if (!eligibleKeys.has(key)) delete this.state.queue[key];
      // Waiting work goes first. Rotate tied siblings by actual last dispatch,
      // so a frequent alphabetically earlier routine cannot consume every slot.
      due.sort((a, b) => a.queuedAtMs - b.queuedAtMs || a.slot - b.slot ||
        a.lastDispatchedAtMs - b.lastDispatchedAtMs || a.agentId.localeCompare(b.agentId) || a.automation.id.localeCompare(b.automation.id));
      this.state.heartbeatMs = now;
      this.state.schedulerStatus = "running";
      this.state.eligibleRoutines = eligibleRoutines;
      this.state.dueRoutines = due.length;
      const busy = this.recordCapacity(running, now, policy);
      this.save();
      for (const row of due) {
        if (busy.size >= policy.maxConcurrent) break;
        if (busy.has(row.agentId)) continue;
        if (this.policy() === null || this.stopped || !await this.deps.isReady()) break;
        busy.add(row.agentId);
        void this.dispatch({ agentId: row.agentId, automation: row.automation,
          scheduledForMs: row.slot, runUuid: crypto.randomUUID(), runAsSubagent: true }).catch(error => {
          this.log("dispatch-failed", { errorCode: String(error.code ?? "dispatch-error").slice(0, 64) });
        });
      }
    } catch (error) {
      this.log("tick-failed", { errorCode: String(error.code ?? "scheduler-error").slice(0, 64) });
    } finally { this.ticking = false; }
  }

  async fireFromPlatform(args) {
    if (!this.owns(args.agentId, args.automation)) return this.deps.fire(args);
    // Server retries can arrive with a different run UUID for the same slot.
    // Normalize a legacy fire lacking its slot to the current due occurrence.
    const scheduledForMs = args.scheduledForMs ?? this.dueSlot(args.agentId, args.automation, this.now(), this.policy());
    if (!finiteTime(scheduledForMs)) return "ok";
    return this.dispatch({ ...args, scheduledForMs });
  }

  async dispatch(args) {
    const key = keyOf(args.agentId, args.automation.id);
    const previous = this.state.receipts[key];
    if (this.pending.has(key)) return undefined;
    if (Object.entries(this.state.receipts).some(([other, receipt]) =>
      other.split("\0")[0] === args.agentId && receipt.phase !== "finished")) return undefined;
    if (previous && previous.scheduledForMs >= args.scheduledForMs) {
      return previous.phase === "finished" ? (previous.status === "ok" ? "ok" : "error") : undefined;
    }
    const policy = this.policy();
    if (this.stopped || this.faulted || policy === null || this.isBotPaused(args.agentId, policy) || !await this.deps.isReady()) return undefined;
    const running = await this.deps.runningAgentIds();
    const livePolicy = this.policy();
    if (this.stopped || livePolicy === null || this.faulted || this.isBotPaused(args.agentId, livePolicy) || !this.owns(args.agentId, args.automation) || !args.automation.isEnabled) return undefined;
    const now = this.now();
    const queued = this.state.queue[key];
    const queuedWithinDeadline = queued?.scheduledForMs === args.scheduledForMs && queued.expiresAtMs >= now &&
      queued.queuedAtMs + (livePolicy.queueTtlMs ?? 3600000) >= now;
    if (args.scheduledForMs > now || (args.scheduledForMs < now - livePolicy.graceMs && !queuedWithinDeadline)) return "error";
    // Awaiting readiness yields; re-check the claim after it so competing server
    // fires or the local timer cannot both acquire the same routine.
    if (this.pending.has(key) || (this.state.receipts[key]?.scheduledForMs ?? -1) >= args.scheduledForMs) return undefined;
    const busy = this.recordCapacity(running, now, livePolicy);
    if (busy.has(args.agentId) || busy.size >= livePolicy.maxConcurrent) return undefined;
    const receipt = { scheduledForMs: args.scheduledForMs, runUuid: args.runUuid,
      phase: "dispatched", dispatchedAtMs: now, executionVerified: false };
    this.state.receipts[key] = receipt;
    delete this.state.queue[key];
    this.recordCapacity(running, now, livePolicy);
    try { this.save(); } catch { this.log("claim-write-failed"); return undefined; }
    this.log("dispatched", { agentId: args.agentId, automationId: args.automation.id, ...receipt });
    const task = Promise.resolve().then(async () => {
      try {
        // This is trigger:schedule, not the manual endpoint that ignores a pause
        // made while the run is queued. Native code rechecks enabled at start.
        const outcome = await this.deps.fire(args);
        const rows = await this.deps.listAutomations();
        const run = rows.find(row => row.agentId === args.agentId && row.automation.id === args.automation.id)
          ?.automation.runs?.find(entry => entry.id === args.runUuid);
        const suppressed = outcome === undefined && run === undefined;
        receipt.phase = (run && finiteTime(run.finishedAt)) || suppressed ? "finished" : "uncertain";
        receipt.status = run?.status ?? (suppressed ? "dropped" : outcome === "ok" ? "unverified" : "error");
        receipt.executionVerified = receipt.phase === "finished" && run?.status === "ok" && outcome === "ok";
        if (finiteTime(run?.finishedAt)) receipt.finishedAtMs = run.finishedAt;
        else if (suppressed) {
          receipt.finishedAtMs = this.now();
          receipt.completionReason = "native-returned-without-run";
        }
        this.save();
        this.log(receipt.phase, { agentId: args.agentId, automationId: args.automation.id, ...receipt });
        return receipt.executionVerified ? "ok" : "error";
      } catch (error) {
        receipt.phase = "uncertain";
        receipt.executionVerified = false;
        try { this.save(); } catch { /* Fault latch already stops future claims. */ }
        this.log("run-unconfirmed", { agentId: args.agentId, automationId: args.automation.id,
          runUuid: args.runUuid, errorCode: String(error.code ?? "run-error").slice(0, 64) });
        return "error";
      } finally { this.pending.delete(key); }
    });
    this.pending.set(key, task);
    return task;
  }
}

module.exports = { LocalRoutineScheduler, readPolicy, atomicJson, keyOf, MAX_CONCURRENT_BOTS };
