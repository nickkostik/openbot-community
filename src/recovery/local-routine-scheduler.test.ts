import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { LocalRoutineScheduler, readPolicy, keyOf, MAX_CONCURRENT_BOTS } = require("../../recovery/hooks/local-routine-scheduler.cjs");
const SLOT = 600000;

function fixture(t: any) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-routines-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  let now = SLOT + 1000;
  let ready = true;
  let policy: any = { maxConcurrent: 2, graceMs: 120000 };
  let running = new Set<string>();
  const calls: any[] = [];
  const rows: any[] = [row("box-a", "poll")];
  const deps: any = {
    root, now: () => now, getPolicy: () => policy, isReady: () => ready,
    isLocalAgent: (id: string) => id.startsWith("box-"),
    cronSchedules: (a: any) => a.trigger?.type === "cron" ? [a.schedule] : [],
    computeNextRunAt: (_schedule: string, after: number) => (Math.floor(after / 300000) + 1) * 300000,
    listAutomations: async () => rows, runningAgentIds: async () => running,
    fire: async (args: any) => {
      calls.push(args);
      args.automation.lastRunAt = now;
      args.automation.runs.push({ id: args.runUuid, status: "ok", startedAt: now, finishedAt: now + 2000 });
      return "ok";
    },
  };
  const scheduler = new LocalRoutineScheduler(deps);
  scheduler.stopped = false;
  t.after(() => scheduler.stop());
  return { root, deps, scheduler, rows, calls,
    setNow: (value: number) => { now = value; },
    setReady: (value: boolean) => { ready = value; },
    setPolicy: (value: any) => { policy = value; },
    setRunning: (value: Set<string>) => { running = value; } };
}
function row(agentId: string, id: string) {
  return { agentId, automation: { id, schedule: "*/5 * * * *", trigger: { type: "cron" },
    createdAt: 0, lastRunAt: null, isEnabled: true, runs: [] } };
}
async function settle(scheduler: any) {
  await new Promise(resolve => setImmediate(resolve));
  await Promise.all([...scheduler.pending.values()]);
}

test("saved twelve-bot policy works in both modes and rejects larger or fractional caps", t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.root, "openbot-local-only.enable"), "");
  for (const mode of ["official", "custom"]) {
    fs.writeFileSync(path.join(f.root, "openbot-mode"), mode);
    fs.writeFileSync(path.join(f.root, "openbot-local-routines.json"), JSON.stringify({ enabled: true, maxConcurrent: 12 }));
    assert.equal(readPolicy(f.root)?.maxConcurrent, MAX_CONCURRENT_BOTS);
    for (const cap of [13, 12.5, 0, -1]) {
      fs.writeFileSync(path.join(f.root, "openbot-local-routines.json"), JSON.stringify({ enabled: true, maxConcurrent: cap }));
      assert.equal(readPolicy(f.root), null);
    }
  }
});

test("twelve pending native runs hold their durable lanes and the thirteenth waits without duplicate firing", async t => {
  const f = fixture(t);
  const immediateFire = f.deps.fire;
  f.setPolicy({ maxConcurrent: 12, graceMs: 120000 });
  f.rows.splice(0, f.rows.length, ...Array.from({ length: 13 }, (_, i) => row(`box-${String(i).padStart(2, "0")}`, "poll")));
  const finishers: Array<() => void> = [];
  f.deps.fire = async (args: any) => {
    f.calls.push(args);
    const run = { id: args.runUuid, status: "running", startedAt: SLOT + 1000, finishedAt: undefined as number | undefined };
    args.automation.runs.push(run);
    return new Promise(resolve => finishers.push(() => {
      run.status = "ok";
      run.finishedAt = SLOT + 2000;
      resolve("ok");
    }));
  };
  await f.scheduler.tick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 12);
  assert.equal(f.scheduler.pending.size, 12);
  assert.equal(f.scheduler.state.busyAgents, 12);
  assert.equal(f.scheduler.state.openReceipts, 12);
  assert.equal(Object.keys(f.scheduler.state.queue).length, 1);
  const durable = JSON.parse(fs.readFileSync(path.join(f.root, "local-routine-scheduler/state.json"), "utf8"));
  assert.equal(Object.keys(durable.receipts).length, 12);
  assert.ok(Object.values(durable.receipts).every((r: any) => r.executionVerified === false));
  await Promise.all(f.calls.map(args => f.scheduler.fireFromPlatform({ ...args, runUuid: "duplicate-platform-fire" })));
  await f.scheduler.tick();
  assert.equal(f.calls.length, 12);
  finishers.forEach(finish => finish());
  await settle(f.scheduler);
  f.deps.fire = immediateFire;
  await f.scheduler.tick();
  await settle(f.scheduler);
  assert.equal(f.calls.length, 13);
  assert.equal(new Set(f.calls.map(args => args.agentId)).size, 13);
  assert.ok(Object.values(f.scheduler.state.receipts).every((r: any) => r.executionVerified === true));
});

test("active native chat owners share the twelve-bot budget with routine claims", async t => {
  const f = fixture(t);
  f.setPolicy({ maxConcurrent: 12, graceMs: 120000 });
  f.setRunning(new Set(["box-chat-a", "box-chat-b"]));
  f.rows.splice(0, f.rows.length, ...Array.from({ length: 15 }, (_, i) => row(`box-${String(i).padStart(2, "0")}`, "poll")));
  const finishers: Array<() => void> = [];
  f.deps.fire = (args: any) => {
    f.calls.push(args);
    return new Promise(resolve => finishers.push(() => resolve(undefined)));
  };
  await f.scheduler.tick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 10);
  assert.equal(f.scheduler.state.busyAgents, 12);
  assert.equal(f.scheduler.state.runningAgentIds.length, 2);
  assert.equal(f.scheduler.state.openReceipts, 10);
  finishers.forEach(finish => finish());
  await settle(f.scheduler);
});

test("due local schedule executes and verifies native completion, not an HTTP/null success", async t => {
  const f = fixture(t);
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].scheduledForMs, SLOT);
  assert.equal(f.calls[0].runAsSubagent, true);
  const receipt = f.scheduler.state.receipts[keyOf("box-a", "poll")];
  assert.equal(receipt.phase, "finished");
  assert.equal(receipt.executionVerified, true);
  assert.equal(fs.statSync(path.join(f.root, "local-routine-scheduler/state.json")).mode & 0o777, 0o600);
});

test("disabled routines, temporal copies and event-only listeners are never clock-fired", async t => {
  const f = fixture(t);
  f.rows[0].automation.isEnabled = false;
  f.rows.push(row("temporal-a", "poll"), { ...row("box-b", "listener"), automation: { id: "listener", isEnabled: true, trigger: { type: "email" } } });
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 0);
});

test("clock waits for local ownership/readiness and respects configured bot pauses", async t => {
  const f = fixture(t);
  f.setReady(false); await f.scheduler.tick();
  assert.equal(f.scheduler.state.schedulerStatus, "waiting-for-local-readiness");
  f.setReady(true); f.setPolicy(null); await f.scheduler.tick();
  f.setPolicy({ maxConcurrent: 2, graceMs: 120000, pausedBotIds: ["box-a"] }); await f.scheduler.tick();
  assert.equal(f.calls.length, 0);
});

test("readiness yielding cannot ignore a scheduler pause made during that yield", async t => {
  const f = fixture(t);
  f.deps.isReady = async () => { f.setPolicy(null); return true; };
  await f.scheduler.dispatch({ agentId: "box-a", automation: f.rows[0].automation, scheduledForMs: SLOT, runUuid: "race" });
  assert.equal(f.calls.length, 0);
});

test("old downtime is skipped; a clock only consolidates a recent due slot", async t => {
  const f = fixture(t);
  f.setNow(SLOT + 180000);
  await f.scheduler.tick(); assert.equal(f.calls.length, 0);
  f.setNow(SLOT + 301000); await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].scheduledForMs, SLOT + 300000);
});

test("unphased every interval remains anchored after downtime instead of postponing every tick", async t => {
  const f = fixture(t);
  f.rows[0].automation.schedule = "@every 5m";
  f.deps.parseEverySchedule = () => ({ intervalMs: 300000 });
  f.deps.computeNextRunAt = (_s: string, after: number) => after + 300000;
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls[0].scheduledForMs, SLOT);
  f.setNow(SLOT + 301001); await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls[1].scheduledForMs, SLOT + 301000);
});

test("normal clock and platform retry share persistent slot claims across host restart", async t => {
  const f = fixture(t);
  await f.scheduler.tick(); await settle(f.scheduler);
  await f.scheduler.fireFromPlatform({ agentId: "box-a", automation: f.rows[0].automation, scheduledForMs: SLOT, runUuid: "different-server-id" });
  f.scheduler.stop();
  const restarted = new LocalRoutineScheduler(f.deps);
  restarted.stopped = false;
  await restarted.fireFromPlatform({ agentId: "box-a", automation: f.rows[0].automation, scheduledForMs: SLOT, runUuid: "another-server-id" });
  await restarted.tick(); await settle(restarted); restarted.stop();
  assert.equal(f.calls.length, 1);
});

test("simultaneous clock/server readiness yields claim exactly once", async t => {
  const f = fixture(t);
  f.deps.isReady = async () => true;
  await Promise.all(["one", "two", "three"].map(runUuid => f.scheduler.dispatch({ agentId: "box-a", automation: f.rows[0].automation, scheduledForMs: SLOT, runUuid })));
  assert.equal(f.calls.length, 1);
});

test("concurrency includes other running bots and allows only one new run per owner", async t => {
  const f = fixture(t);
  f.rows.push(row("box-a", "another-poll"), row("box-b", "poll"), row("box-c", "poll"));
  f.setRunning(new Set(["box-a", "box-other"]));
  await f.scheduler.tick(); assert.equal(f.calls.length, 0);
  f.setRunning(new Set());
  let release: (() => void) | undefined;
  const wait = new Promise<void>(resolve => { release = resolve; });
  f.deps.fire = async (args: any) => { f.calls.push(args); await wait; return undefined; };
  await f.scheduler.tick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 2);
  assert.equal(new Set(f.calls.map(call => call.agentId)).size, 2);
  release!(); await settle(f.scheduler);
});

test("undefined no-op and zero-second native error are not reported as successful runs", async t => {
  const f = fixture(t);
  f.deps.fire = async (args: any) => {
    f.calls.push(args);
    args.automation.runs.push({ id: args.runUuid, status: "error", startedAt: SLOT, finishedAt: SLOT });
    return undefined;
  };
  await f.scheduler.tick(); await settle(f.scheduler);
  const receipt = f.scheduler.state.receipts[keyOf("box-a", "poll")];
  assert.equal(receipt.executionVerified, false);
  assert.equal(receipt.status, "error");
  await f.scheduler.tick(); assert.equal(f.calls.length, 1);
});

test("uncertain execution is preserved on restart and never blindly replayed", async t => {
  const f = fixture(t);
  f.deps.fire = async () => "ok"; // A claimed success without a native completion cannot be trusted.
  await f.scheduler.tick(); await settle(f.scheduler);
  f.scheduler.stop();
  const restarted = new LocalRoutineScheduler(f.deps); restarted.stopped = false;
  await restarted.tick(); assert.equal(restarted.state.receipts[keyOf("box-a", "poll")].phase, "uncertain");
  f.setNow(SLOT + 301000);
  await restarted.fireFromPlatform({ agentId: "box-a", automation: f.rows[0].automation,
    scheduledForMs: SLOT + 300000, runUuid: "later-slot" });
  restarted.stop();
  assert.equal(f.calls.length, 0);
});

test("an intentional native no-run does not permanently wedge the next scheduled occurrence", async t => {
  const f = fixture(t);
  f.deps.fire = async (args: any) => { f.calls.push(args); return undefined; };
  await f.scheduler.tick(); await settle(f.scheduler);
  const receipt = f.scheduler.state.receipts[keyOf("box-a", "poll")];
  assert.equal(receipt.phase, "finished");
  assert.equal(receipt.status, "dropped");
  assert.equal(receipt.finishedAtMs, SLOT + 1000);
  assert.equal(receipt.completionReason, "native-returned-without-run");
  assert.equal(receipt.executionVerified, false);
  f.setNow(SLOT + 301000); await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 2);
});

test("corrupt receipt state fails closed without recreating the dedupe file", t => {
  const f = fixture(t);
  const file = path.join(f.root, "local-routine-scheduler/state.json");
  fs.writeFileSync(file, "broken private state");
  assert.throws(() => new LocalRoutineScheduler(f.deps));
  assert.equal(fs.readFileSync(file, "utf8"), "broken private state");
});

test("claim write failure stops dispatch and cannot run without durable dedupe", async t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.root, "local-routine-scheduler/state.json"));
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 0);
  assert.equal(f.scheduler.faulted, true);
});

test("policy requires local opt-in in either mode and fails closed on malformed pause or budget", t => {
  const f = fixture(t);
  const write = (name: string, value: any) => fs.writeFileSync(path.join(f.root, name), JSON.stringify(value));
  write("openbot-local-routines.json", { enabled: true });
  assert.equal(readPolicy(f.root), null);
  fs.writeFileSync(path.join(f.root, "openbot-local-only.enable"), "");
  fs.writeFileSync(path.join(f.root, "openbot-mode"), "custom");
  assert.equal(readPolicy(f.root).maxConcurrent, 2);
  fs.writeFileSync(path.join(f.root, "openbot-mode"), "official");
  assert.equal(readPolicy(f.root).queueTtlMs, 3600000);
  fs.writeFileSync(path.join(f.root, "openbot-mode"), "invalid");
  assert.equal(readPolicy(f.root), null);
  fs.writeFileSync(path.join(f.root, "openbot-mode"), "custom");
  write("openbot-pause.json", { paused: true }); assert.equal(readPolicy(f.root), null);
  fs.rmSync(path.join(f.root, "openbot-pause.json"));
  write("openbot-pause-bots.json", { pausedBotIds: ["box-a"] });
  assert.deepEqual(readPolicy(f.root).pausedBotIds, ["box-a"]);
  write("openbot-local-routines.json", { enabled: true, maxConcurrent: 99 });
  assert.equal(readPolicy(f.root), null);
});

test("an observed daily slot waits past the two-minute grace window without replaying downtime", async t => {
  const f = fixture(t);
  f.deps.computeNextRunAt = (_s: string, after: number) => after < SLOT ? SLOT : SLOT + 86400000;
  f.setRunning(new Set(["box-other-1", "box-other-2"]));
  await f.scheduler.tick();
  assert.equal(f.calls.length, 0);
  assert.equal(f.scheduler.state.queuedRoutines, 1);
  f.setNow(SLOT + 663000);
  f.setRunning(new Set());
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].scheduledForMs, SLOT);
  assert.equal(Object.keys(f.scheduler.state.queue).length, 0);
});

test("queued work survives reload, consolidates recurring slots and expires after its deadline", async t => {
  const f = fixture(t);
  f.setPolicy({ maxConcurrent: 2, graceMs: 120000, queueTtlMs: 900000 });
  f.setRunning(new Set(["box-other-1", "box-other-2"]));
  await f.scheduler.tick();
  const queuedAt = f.scheduler.state.queue[keyOf("box-a", "poll")].queuedAtMs;
  f.scheduler.stop();
  const restarted = new LocalRoutineScheduler(f.deps); restarted.stopped = false;
  t.after(() => restarted.stop());
  f.setNow(SLOT + 601000);
  await restarted.tick();
  assert.equal(restarted.state.queue[keyOf("box-a", "poll")].queuedAtMs, queuedAt);
  assert.equal(restarted.state.queue[keyOf("box-a", "poll")].scheduledForMs, SLOT + 600000);
  f.setNow(SLOT + 1080000); f.setRunning(new Set());
  await restarted.tick(); await settle(restarted);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(restarted.state.queue, {});
});

test("a newer recurring slot cannot steal the place of an older waiting daily sibling", async t => {
  const f = fixture(t);
  const daily = row("box-a", "z-daily"); daily.automation.schedule = "daily";
  f.rows.push(daily);
  f.deps.computeNextRunAt = (s: string, after: number) => s === "daily" ? (after < SLOT ? SLOT : SLOT + 86400000) : (Math.floor(after / 300000) + 1) * 300000;
  f.setRunning(new Set(["box-other-1", "box-other-2"]));
  await f.scheduler.tick();
  f.setNow(SLOT + 301000); f.setRunning(new Set());
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls[0].automation.id, "z-daily");
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls[1].automation.id, "poll");
});

test("equal due slots favour the sibling that has waited longer since its last dispatch", async t => {
  const f = fixture(t);
  f.rows[0].automation.id = "a-sweep";
  f.rows.push(row("box-a", "z-scanner"));
  for (const [id, stamp] of [["a-sweep", SLOT - 1000], ["z-scanner", SLOT - 300000]] as const) {
    f.scheduler.state.receipts[keyOf("box-a", id)] = { scheduledForMs: SLOT - 300000, runUuid: id, phase: "finished", dispatchedAtMs: stamp, status: "ok" };
  }
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls[0].automation.id, "z-scanner");
});

test("disabled, rescheduled and paused routines cannot execute previously queued work", async t => {
  const f = fixture(t);
  f.setRunning(new Set(["box-other-1", "box-other-2"])); await f.scheduler.tick();
  f.rows[0].automation.isEnabled = false;
  f.setRunning(new Set()); await f.scheduler.tick();
  assert.deepEqual(f.scheduler.state.queue, {});
  assert.equal(f.calls.length, 0);
  f.rows[0].automation.isEnabled = true;
  f.setRunning(new Set(["box-other-1", "box-other-2"])); await f.scheduler.tick();
  f.setNow(SLOT + 180000); f.rows[0].automation.schedule = "new-schedule";
  f.setRunning(new Set()); await f.scheduler.tick();
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.scheduler.state.queue, {});
  f.rows[0].automation.schedule = "*/5 * * * *";
  f.setNow(SLOT + 301000);
  f.setRunning(new Set(["box-other-1", "box-other-2"])); await f.scheduler.tick();
  assert.equal(Object.keys(f.scheduler.state.queue).length, 1);
  f.setPolicy({ maxConcurrent: 2, graceMs: 120000, pausedBotIds: ["box-a"] });
  f.setRunning(new Set()); await f.scheduler.tick();
  assert.deepEqual(f.scheduler.state.queue, {});
  assert.equal(f.calls.length, 0);
});

test("four lanes admit four distinct owners, with durable claims covering yielding dispatch", async t => {
  const f = fixture(t);
  f.setPolicy({ maxConcurrent: 4, graceMs: 120000 });
  f.rows.push(...["box-b", "box-c", "box-d", "box-e"].map(id => row(id, "poll")));
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  f.deps.fire = async (args: any) => { f.calls.push(args); await wait; return undefined; };
  await f.scheduler.tick(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls.length, 4);
  assert.equal(new Set(f.calls.map(call => call.agentId)).size, 4);
  await f.scheduler.tick();
  assert.equal(f.calls.length, 4);
  assert.equal(f.scheduler.state.pendingClaims, 4);
  assert.equal(f.scheduler.state.openReceipts, 4);
  assert.equal(f.scheduler.state.busyAgents, 4);
  assert.equal(f.scheduler.state.queuedRoutines, 1);
  release(); await settle(f.scheduler);
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 5);
});

test("a long native turn or pending fire is never mistaken for an idle orphan", async t => {
  const f = fixture(t);
  f.scheduler.state.receipts[keyOf("box-a", "poll")] = {
    scheduledForMs: SLOT, runUuid: "long-run", phase: "running", dispatchedAtMs: SLOT,
  };
  f.rows[0].automation.runs.push({ id: "long-run", status: "running" });
  f.setNow(SLOT + 900000);
  await f.scheduler.tick();
  assert.equal(f.scheduler.state.receipts[keyOf("box-a", "poll")].phase, "running");
  assert.equal(f.calls.length, 0);
  f.rows[0].automation.runs.length = 0;
  f.setRunning(new Set(["box-a"])); await f.scheduler.tick();
  assert.notEqual(f.scheduler.state.receipts[keyOf("box-a", "poll")].phase, "finished");
  f.setRunning(new Set());
  f.scheduler.pending.set(keyOf("box-a", "poll"), Promise.resolve());
  await f.scheduler.tick();
  assert.notEqual(f.scheduler.state.receipts[keyOf("box-a", "poll")].phase, "finished");
  assert.equal(f.calls.length, 0);
  f.scheduler.pending.clear();
});

test("capacity telemetry separates whole native turns from durable routine claims", async t => {
  const f = fixture(t);
  f.setRunning(new Set(["box-other-1", "box-other-2"]));
  await f.scheduler.tick();
  assert.equal(f.scheduler.state.busyAgents, 2);
  assert.equal(f.scheduler.state.openReceipts, 0);
  assert.equal(f.scheduler.state.pendingClaims, 0);
  assert.deepEqual(f.scheduler.state.runningAgentIds, ["box-other-1", "box-other-2"]);
  assert.equal(f.scheduler.state.gateReason, "at-capacity");
});

test("an idle orphan frees future slots while its consumed uncertain slot remains deduplicated", async t => {
  const f = fixture(t);
  f.deps.fire = async () => "ok";
  await f.scheduler.tick(); await settle(f.scheduler);
  const consumed = f.scheduler.state.receipts[keyOf("box-a", "poll")].runUuid;
  f.setNow(SLOT + 181000); await f.scheduler.tick();
  assert.equal(f.scheduler.state.receipts[keyOf("box-a", "poll")].phase, "uncertain");
  f.setNow(SLOT + 300999); await f.scheduler.tick();
  assert.equal(f.scheduler.state.receipts[keyOf("box-a", "poll")].runUuid, consumed);
  f.setNow(SLOT + 301000);
  f.deps.fire = async (args: any) => { f.calls.push(args); args.automation.runs.push({ id: args.runUuid, status: "ok", finishedAt: SLOT + 302000 }); return "ok"; };
  await f.scheduler.tick(); await settle(f.scheduler);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].scheduledForMs, SLOT + 300000);
});
