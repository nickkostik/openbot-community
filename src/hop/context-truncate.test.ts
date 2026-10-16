import assert from "node:assert/strict";
import crypto from "node:crypto";
import { EventEmitter } from "node:events";
import * as fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

type Model = { id: string; providerId: string; slug: string; contextTokens: number; maxOutputTokens?: number; reasoningLevels?: string[]; activeReasoning?: string };
type Plan = { catalog: { providers: { id: string }[]; models: Model[] } };
type Body = { conversationId?: string; reasoning_effort?: string; openbot_internal_auxiliary?: boolean | string; tools?: unknown[]; messages: { role: string; content: string; reasoning_content?: string; tool_call_id?: string; tool_calls?: { id: string; type: string; function: { name: string; arguments: string } }[] }[] };
type SummaryRoute = { modelId: string; contextTokens: number } | null;
type Compactor = {
  resolveSummarizer: (plan: Plan, target: Model) => SummaryRoute;
  resolveSummarizers: (plan: Plan, target: Model) => { modelId: string; contextTokens: number; reasoningEffort?: string }[];
  applyContextTruncation: (body: Body, model: Model) => string;
  chatKey: (body: Body) => string;
  estimateTokens: (messages: Body["messages"]) => number;
};

const source = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/context-truncate.cjs"), "utf8");
const target: Model = { id: "opencode-2:deepseek-v4.1-flash", providerId: "opencode-2", slug: "deepseek-v4.1-flash", contextTokens: 32768, reasoningLevels: ["default", "low", "high", "max"], activeReasoning: "high" };
const kimi: Model = { id: "opencode-2:kimi-k3", providerId: "opencode-2", slug: "kimi-k3", contextTokens: 1000000 };

function fixture(t: TestContext) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-compactor-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const planFile = path.join(dir, "plan.json");
  const plan: Plan = { catalog: { providers: [{ id: "opencode" }, { id: "opencode-2" }], models: [target, kimi, { ...target, id: "opencode-2:muse-spark-1.3-contributor", slug: "muse-spark-1.3-contributor", contextTokens: 1000000 },
    { ...kimi, id: "opencode:kimi-k3", providerId: "opencode" }] } };
  const writePlan = (value: Plan) => fs.writeFileSync(planFile, JSON.stringify(value));
  writePlan(plan);
  let now = 1790000000000;
  type Timer = { callback: () => void; at: number; cancelled: boolean; unref: () => void };
  const timers: Timer[] = [];
  const clock = { advance(ms: number) {
    now += ms;
    for (const timer of timers) if (!timer.cancelled && timer.at <= now) {
      timer.cancelled = true;
      timer.callback();
    }
  } };
  class ClockDate extends Date { static override now() { return now; } }
  const requests: { payload: Record<string, unknown>; timeoutMs: number; destroyed: boolean;
    respond: (status: number, text: string, code?: string, finishReason?: string) => void;
    fail: () => void }[] = [];
  const mockHttp = {
    request(options: { timeout: number }, callback: (response: EventEmitter & { statusCode: number }) => void) {
      const req = new EventEmitter() as EventEmitter & { write: (value: string) => void; end: () => void; destroy: () => void };
      let payload: Record<string, unknown> = {};
      req.write = (value) => { payload = JSON.parse(value) as Record<string, unknown>; };
      const entry = { payload, timeoutMs: options.timeout, destroyed: false, respond(status: number, text: string, code?: string, finishReason = "stop") {
        const response = Object.assign(new EventEmitter(), { statusCode: status });
        callback(response);
        response.emit("data", Buffer.from(JSON.stringify(status >= 200 && status < 300 ? { choices: [{ message: { content: text }, finish_reason: finishReason }] } : { error: { message: text, code } })));
        response.emit("end");
      }, fail() { req.emit("error", new Error("connection reset")); } };
      req.end = () => { entry.payload = payload; requests.push(entry); };
      req.destroy = () => { entry.destroyed = true; req.emit("error", new Error("timeout")); };
      return req;
    },
  };
  function reload() {
    const exports: { exports: Partial<Compactor> } = { exports: {} };
    vm.runInNewContext(source, {
    require(name: string) {
      if (name === "fs") return fs;
      if (name === "path") return path;
      if (name === "http") return mockHttp;
      if (name === "crypto") return crypto;
      throw new Error("unexpected import");
    },
    module: exports,
    Buffer,
    Date: ClockDate,
    setTimeout(callback: () => void, ms: number) {
      const timer: Timer = { callback, at: now + ms, cancelled: false, unref() {} };
      timers.push(timer);
      return timer;
    },
    clearTimeout(timer?: Timer) { if (timer) timer.cancelled = true; },
    process: { env: { OPENBOT_PLAN: planFile, OPENBOT_SAND_DATA: dir }, stderr: { write() {} } },
    });
    return exports.exports as Compactor;
  }
  const compactor = reload();
  const state = (body: Body): Record<string, unknown> => JSON.parse(fs.readFileSync(path.join(dir, "openbot/compact-state", compactor.chatKey(body) + ".json"), "utf8"));
  const request = (index = 0) => {
    const entry = requests[index];
    assert.ok(entry, "missing summarizer request " + index);
    return entry;
  };
  return { compactor, requests, request, plan, writePlan, state, dir, clock, reload };
}

function history(count = 20, conversationId = "chat-a"): Body {
  return { conversationId, messages: [{ role: "system", content: "Keep the task state." },
    ...Array.from({ length: count }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: String(i) + "x".repeat(6000) })),
    { role: "user", content: "Continue from the current task." }] };
}

test("compactor keeps the selected canonical model and never selects Kimi K3", (t) => {
  const f = fixture(t);
  assert.equal(f.compactor.resolveSummarizer(f.plan, target)?.modelId, target.id);
  const routes = f.compactor.resolveSummarizers(f.plan, target);
  assert.ok(!routes.some(r => r.modelId.includes("kimi-k3")));
  assert.equal(routes[1]?.modelId, "opencode-2:muse-spark-1.3-contributor");
  assert.equal(routes[1]?.reasoningEffort, "max");
});

test("fallbacks keep account families distinct and exclude forbidden endpoints", (t) => {
  const f = fixture(t);
  const foreign = { ...target, id: "opencode:deepseek-v4.1-flash", providerId: "opencode", contextTokens: 1000000 };
  f.plan.catalog.models.push(foreign);
  assert.equal(f.compactor.resolveSummarizers(f.plan, target)[2]?.modelId, foreign.id);
  f.plan.catalog.providers.push({ id: "n5" });
  f.plan.catalog.models.push({ ...foreign, providerId: "n5", id: "n5:deepseek-v4.1-flash" });
  assert.ok(!f.compactor.resolveSummarizers(f.plan, target).some(r => r.modelId.startsWith("n5:")));
});

test("compactor rejects a removed target, malformed catalog ID, or missing provider", (t) => {
  const f = fixture(t);
  assert.equal(f.compactor.resolveSummarizer(f.plan, { ...target, id: "opencode-2:removed" }), null);
  f.plan.catalog.models = [{ ...target, id: "modelhub:go-plan-2/deepseek-v4.1-flash" }];
  assert.equal(f.compactor.resolveSummarizer(f.plan, target), null);
  f.plan.catalog.models = [target, kimi];
  f.plan.catalog.providers = [{ id: "opencode" }];
  assert.equal(f.compactor.resolveSummarizer(f.plan, target), null);
});

test("conversation identity isolates identical transcripts and survives system prompt changes", (t) => {
  const f = fixture(t);
  const a = history();
  const key = f.compactor.chatKey(a);
  assert.ok(a.messages[0]);
  a.messages[0].content = "Updated clock and runtime metadata.";
  assert.equal(f.compactor.chatKey(a), key);
  assert.notEqual(f.compactor.chatKey(history(20, "chat-b")), key);
});

test("real summary uses the selected internal model and stable auxiliary session", (t) => {
  const f = fixture(t);
  const body = history();
  body.reasoning_effort = "low";
  assert.match(f.compactor.applyContextTruncation(body, target), /fallback$/);
  assert.equal(f.requests.length, 1);
  assert.equal(f.request().payload.model, target.id);
  assert.equal(f.request().payload.reasoning_effort, "low");
  assert.equal(f.request().payload.openbot_internal_model, true);
  assert.equal(f.request().payload.conversationId, "openbot-compaction:" + f.compactor.chatKey(history()));
  f.request().respond(200, "The task is unfinished. Continue the implementation and tests.");
  const state = f.state(history());
  assert.equal(state.summarySource, target.id);
  assert.equal(state.summaryContextTokens, target.contextTokens);
  assert.match(f.compactor.applyContextTruncation(history(), target), /cached$/);
  assert.equal(f.requests.length, 1);
  assert.equal(state.lastTrigger, "pct-85");
  assert.equal(fs.statSync(path.join(f.dir, "openbot/compact-state", f.compactor.chatKey(body) + ".json")).mode & 0o777, 0o600);
});

test("cache preserves the exact compacted prefix as new messages arrive", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Stable task summary.");
  const first = history();
  f.compactor.applyContextTruncation(first, target);
  const next = history(21);
  f.compactor.applyContextTruncation(next, target);
  assert.deepEqual(next.messages.slice(0, -2), first.messages.slice(0, -1));
  assert.equal(f.requests.length, 1);
  assert.equal(f.state(history()).lastDropped, f.state(history()).summaryForDropped);
});

test("a same-length edited history invalidates its summary rather than hiding changed facts", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Old fact.");
  const edited = history();
  assert.ok(edited.messages[1]);
  edited.messages[1].content = "A corrected fact." + "z".repeat(6000);
  assert.match(f.compactor.applyContextTruncation(edited, target), /fallback$/);
  assert.equal(f.requests.length, 2);
  assert.ok(!edited.messages.some(m => m.content.includes("Old fact.")));
});

test("pending compactions share one request and record only the prefix actually summarized", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  const actual = f.state(history()).lastDropped;
  f.compactor.applyContextTruncation(history(35), target);
  f.compactor.applyContextTruncation(history(40), target);
  assert.equal(f.requests.length, 1);
  f.request().respond(200, "Only the original prefix was summarized.");
  assert.equal(f.state(history()).summaryForDropped, actual);
  assert.notEqual(f.state(history()).summaryForDropped, f.state(history()).lastDropped);
});

test("native auxiliary calls cannot cancel, overwrite or restart their parent's compaction", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  const before = f.state(history());
  const auxiliary: Body = { conversationId: "chat-a", openbot_internal_auxiliary: true,
    messages: [{ role: "system", content: "Classify this tool action." }, { role: "user", content: "Review the pending command." }] };
  assert.equal(f.compactor.applyContextTruncation(auxiliary, target), "skip-auxiliary");
  const largeAuxiliary = { ...history(100), openbot_internal_auxiliary: true };
  const messages = largeAuxiliary.messages;
  assert.equal(f.compactor.applyContextTruncation(largeAuxiliary, target), "skip-auxiliary");
  assert.equal(largeAuxiliary.messages, messages);
  assert.deepEqual(f.state(history()), before);
  assert.equal(f.requests.length, 1);
  assert.equal(f.request().destroyed, false);
  f.request().respond(200, "The parent's summary finished while its review call ran.");
  assert.match(f.compactor.applyContextTruncation(history(), target), /cached$/);
  assert.equal(f.requests.length, 1);
});

test("a string auxiliary flag cannot suppress cancellation for a real shortened history", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.compactor.applyContextTruncation({ conversationId: "chat-a", openbot_internal_auxiliary: "true",
    messages: [{ role: "user", content: "Start over." }] }, target), "skip-few");
  assert.equal(f.request().destroyed, true);
  f.request().respond(200, "Stale response.");
  assert.equal(f.state(history()).summary, undefined);
});

test("incremental compaction preserves unsummarized messages until its result arrives", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Previous task summary.");
  const oldCount = Number(f.state(history()).summaryForDropped);
  const grown = history(40);
  const fact = grown.messages[oldCount + 1]?.content;
  assert.ok(fact);
  f.compactor.applyContextTruncation(grown, target);
  assert.equal(f.requests.length, 2);
  assert.ok(grown.messages.some(m => m.content.includes(fact)));
  const prompt = JSON.stringify(f.request(1).payload.messages);
  assert.ok(prompt.includes("Previous task summary."));
  assert.ok(!prompt.includes("### user\\n0" + "x".repeat(6000)));
  for (let i = 1; i < f.requests.length && i < 20; i++) f.request(i).respond(200, "Extended summary.");
  assert.match(f.compactor.applyContextTruncation(history(40), target), /cached$/);
});

test("context overflow retries an approved wider route and excludes Kimi", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(400, "Maximum context length exceeded", "context_length_exceeded");
  assert.equal(f.requests.length, 2);
  assert.equal(f.request(1).payload.model, "opencode-2:muse-spark-1.3-contributor");
  assert.equal(f.request(1).payload.reasoning_effort, "max");
  f.request(1).respond(200, "Wide route summary.");
  assert.equal(f.state(history()).summarySource, "opencode-2:muse-spark-1.3-contributor");
});

test("oversized history stays on the selected model through bounded consecutive chunks", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  assert.equal(f.requests.length, 1);
  for (let i = 0; i < f.requests.length && i < 20; i++) {
    assert.equal(f.request(i).payload.model, target.id);
    const prompt = JSON.stringify(f.request(i).payload.messages);
    if (i > 0) assert.ok(prompt.includes("Progress " + (i - 1)));
    f.request(i).respond(200, "Progress " + i);
  }
  assert.ok(f.requests.length > 1 && f.requests.length < 20);
  assert.equal(f.state(history(40)).summarySource, target.id);
  assert.equal(f.state(history(40)).summaryForDropped, f.state(history(40)).lastDropped);
  assert.equal(f.state(history(40)).summaryJob, undefined);
});

test("non-context provider errors do not change accounts or poison the cache", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(503, "This must not be cached as a summary.");
  assert.equal(f.requests.length, 1);
  assert.equal(f.state(history()).summary, undefined);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.requests.length, 1);
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.requests.length, 2);
});

test("catalog edits reload the selected route for the next compaction", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(503, "unavailable");
  f.plan.catalog.models = [{ ...target, activeReasoning: "max" }];
  f.writePlan(f.plan);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.request(1).payload.model, target.id);
  assert.equal(f.request(1).payload.reasoning_effort, "max");
});

test("a large transcript needs no fallback account when only the selected route is approved", (t) => {
  const f = fixture(t);
  f.plan.catalog.models = [target, { ...kimi, id: "opencode:kimi-k3", providerId: "opencode" }];
  f.writePlan(f.plan);
  assert.match(f.compactor.applyContextTruncation(history(40), target), /fallback$/);
  assert.equal(f.requests.length, 1);
  assert.equal(f.request().payload.model, target.id);
});

test("a summary can finish after 60 seconds without cancellation or account changes", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.clock.advance(65000);
  assert.equal(f.request().destroyed, false);
  f.request().respond(200, "Completed after the former deadline.");
  assert.equal(f.state(history()).summary, "Completed after the former deadline.");
  assert.equal(f.requests.length, 1);
});

test("the initial trim receipt preserves the pending checkpoint", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  const job = f.state(history(40)).summaryJob as { cursor: number; chunks: number; fingerprint: string };
  assert.equal(job.cursor, 0);
  assert.equal(job.chunks, 0);
  assert.match(job.fingerprint, /^[a-f0-9]{64}$/);
});

test("a timed-out chunk closes its request and backs off without poisoning an existing summary", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Prior complete summary.");
  f.compactor.applyContextTruncation(history(40), target);
  f.clock.advance(180001);
  assert.equal(f.request(1).destroyed, true);
  assert.equal(f.state(history()).summary, "Prior complete summary.");
  const job = f.state(history()).summaryJob as { lastError: string; nextAttemptAtMs: number };
  assert.equal(job.lastError, "timeout");
  f.compactor.applyContextTruncation(history(40), target);
  assert.equal(f.requests.length, 2);
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(40), target);
  assert.equal(f.requests.length, 3);
  assert.equal(f.request(2).payload.model, target.id);
});

test("chunk checkpoints resume after a transport failure without resending completed input", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "First chunk preserved.");
  assert.equal(f.requests.length, 2);
  assert.equal(f.state(history(40)).summary, undefined, "partial progress is not a complete prefix summary");
  const secondPrompt = f.request(1).payload.messages;
  f.request(1).fail();
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(40), target);
  assert.deepEqual(f.request(2).payload.messages, secondPrompt);
  assert.ok(JSON.stringify(secondPrompt).includes("First chunk preserved."));
});

test("resuming clears stale cooldown status and repeated failures back off longer", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().fail();
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(), target);
  const resumed = f.state(history()).summaryJob as { lastError?: string; nextAttemptAtMs: number; failures: number };
  assert.equal(resumed.lastError, undefined);
  assert.equal(resumed.nextAttemptAtMs, 0);
  assert.equal(resumed.failures, 1);
  f.request(1).fail();
  assert.equal((f.state(history()).summaryJob as { failures: number }).failures, 2);
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.requests.length, 2);
  f.clock.advance(60000);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.requests.length, 3);
});

test("progress and retry cooldown survive a service reload", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Facts from completed input.");
  const pending = f.request(1).payload.messages;
  f.request(1).fail();
  const reloaded = f.reload();
  reloaded.applyContextTruncation(history(40), target);
  assert.equal(f.requests.length, 2);
  f.clock.advance(60000);
  reloaded.applyContextTruncation(history(40), target);
  assert.deepEqual(f.request(2).payload.messages, pending);
  assert.equal((f.state(history(40)).summaryJob as { chunks: number }).chunks, 1);
});

test("paid chunks and their cooldown survive reload, append and native history eviction", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Completed facts from the now-evicted first messages.");
  const pending = f.request(1).payload.messages;
  f.request(1).fail();
  const shifted = history(45);
  shifted.messages.splice(1, 2);
  const reloaded = f.reload();
  reloaded.applyContextTruncation(structuredClone(shifted), target);
  assert.equal(f.requests.length, 2, "history movement must not bypass the retry cooldown");
  f.clock.advance(60000);
  reloaded.applyContextTruncation(structuredClone(shifted), target);
  assert.deepEqual(f.request(2).payload.messages, pending, "resume exactly the unpaid chunk, retaining evicted facts");
  assert.equal((f.state(shifted).summaryJob as { chunks: number }).chunks, 1);
  for (let i = 2; i < f.requests.length; i++) {
    assert.ok(i < 20);
    f.request(i).respond(200, "Completed original task state, including earlier evicted facts.");
  }
  const completed = f.state(shifted);
  assert.equal(completed.summaryJob, undefined);
  assert.equal(completed.summaryForDropped, (completed.summaryPrefixHashes as string[]).length);
  assert.equal(completed.summarySource, target.id);
  assert.equal(fs.readdirSync(path.join(f.dir, "openbot/compact-state")).filter(name => name.endsWith(".transcript")).length, 0);
});

test("a total-job timeout resumes completed chunks after native eviction rather than paying for them twice", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(240), target);
  for (let i = 0; i < 6; i++) {
    f.clock.advance(99999);
    f.request(i).respond(200, "Paid completed chunk " + i);
  }
  const pending = f.request(6).payload.messages;
  f.clock.advance(10);
  assert.equal((f.state(history(240)).summaryJob as { lastError: string }).lastError, "job-timeout");
  const shifted = history(244);
  shifted.messages.splice(1, 4);
  f.clock.advance(60000);
  f.reload().applyContextTruncation(shifted, target);
  assert.deepEqual(f.request(7).payload.messages, pending);
  assert.equal((f.state(history(244)).summaryJob as { chunks: number }).chunks, 6);
});

test("a real covered-history edit rejects a saved checkpoint and removes its private transcript", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Obsolete facts from the first chunk.");
  f.request(1).fail();
  const dir = path.join(f.dir, "openbot/compact-state");
  const oldSource = fs.readdirSync(dir).find(name => name.endsWith(".transcript"))!;
  const edited = history(40);
  edited.messages[3]!.content = "These facts changed; never trust the old summary.";
  f.reload().applyContextTruncation(edited, target);
  assert.equal((f.state(edited).summaryJob as { chunks: number }).chunks, 0);
  assert.ok(!JSON.stringify(f.request(2).payload.messages).includes("Obsolete facts"));
  assert.equal(fs.existsSync(path.join(dir, oldSource)), false);
});

test("a damaged private transcript cannot advance a saved cursor or promote incorrect coverage", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Old partial state.");
  f.request(1).fail();
  const dir = path.join(f.dir, "openbot/compact-state");
  const sourceFile = path.join(dir, fs.readdirSync(dir).find(name => name.endsWith(".transcript"))!);
  assert.equal(fs.statSync(sourceFile).mode & 0o777, 0o600);
  fs.writeFileSync(sourceFile, "wrong transcript");
  f.clock.advance(60000);
  f.reload().applyContextTruncation(history(40), target);
  assert.equal((f.state(history(40)).summaryJob as { cursor: number }).cursor, 0);
  assert.ok(!JSON.stringify(f.request(2).payload.messages).includes("Old partial state."));
  assert.equal(f.state(history(40)).summary, undefined);
});

test("an exact legacy checkpoint upgrades without replaying a completed chunk", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Legacy paid facts.");
  const pending = f.request(1).payload.messages;
  f.request(1).fail();
  const state = f.state(history(40));
  const saved = state.summaryJob as Record<string, unknown>;
  saved.version = 1;
  for (const key of ["routingKey", "coveredCount", "coveredHash", "coveredHashes", "transcriptHash"]) delete saved[key];
  fs.writeFileSync(path.join(f.dir, "openbot/compact-state", f.compactor.chatKey(history(40)) + ".json"), JSON.stringify(state));
  f.clock.advance(60000);
  f.reload().applyContextTruncation(history(40), target);
  assert.deepEqual(f.request(2).payload.messages, pending);
  assert.equal((f.state(history(40)).summaryJob as { version: number }).version, 2);
});

test("the selected thinking model gets a reasoning allowance without changing its effort or account", (t) => {
  const f = fixture(t);
  const wide = { ...target, contextTokens: 128000, maxOutputTokens: 65536 };
  f.writePlan({ catalog: { providers: [{ id: target.providerId }], models: [wide] } });
  const body = history(100);
  body.reasoning_effort = "max";
  f.compactor.applyContextTruncation(body, wide);
  assert.equal(f.request().payload.model, wide.id);
  assert.equal(f.request().payload.reasoning_effort, "max");
  assert.equal(f.request().payload.max_tokens, 32000);
  assert.ok(JSON.stringify(f.request().payload.messages).length / 4 + 32000 < wide.contextTokens);
});

test("summary generation honors the catalog output limit and uses a smaller allowance with reasoning off", (t) => {
  const f = fixture(t);
  const wide = { ...target, contextTokens: 128000, maxOutputTokens: 12000, reasoningLevels: ["none", "high"] };
  f.writePlan({ catalog: { providers: [{ id: target.providerId }], models: [wide] } });
  f.compactor.applyContextTruncation(history(100), wide);
  assert.equal(f.request().payload.max_tokens, 12000);
  const other = history(100, "other-chat");
  other.reasoning_effort = "none";
  f.compactor.applyContextTruncation(other, wide);
  assert.equal(f.request(1).payload.max_tokens, 8192);
  assert.equal(f.request(1).payload.reasoning_effort, "none");
});

test("the latest tool result and its assistant call stay together across compaction", (t) => {
  const f = fixture(t);
  const body = history();
  body.messages.pop();
  const call = { role: "assistant", content: "", tool_calls: [{ id: "latest-call", type: "function", function: { name: "ReadFile", arguments: '{"path":"/work/critical-state.json"}' } }] };
  const reply = { role: "tool", tool_call_id: "latest-call", content: "Critical current facts " + "x".repeat(70000) };
  body.messages.push(call, reply);
  f.compactor.applyContextTruncation(body, target);
  assert.equal(body.messages.at(-2), call, "keep the latest assistant tool call unchanged");
  assert.equal(body.messages.at(-1), reply, "keep the latest tool result unchanged");
  assert.equal(f.request().payload.model, target.id);
});

test("parallel historical tools are summarized as one complete group with arguments and IDs", (t) => {
  const f = fixture(t);
  const body: Body = { conversationId: "parallel-tools", messages: [
    { role: "system", content: "Keep the task state." },
    { role: "assistant", content: "", tool_calls: [
      { id: "call-a", type: "function", function: { name: "WriteFile", arguments: '{"path":"/work/state.json","value":"precise-v1"}' } },
      { id: "call-b", type: "function", function: { name: "ReadFile", arguments: '{"path":"/work/settings.json"}' } },
    ] },
    { role: "tool", tool_call_id: "call-a", content: "x".repeat(120000) },
    { role: "tool", tool_call_id: "call-b", content: "y".repeat(10000) },
    { role: "user", content: "Continue." },
  ] };
  f.compactor.applyContextTruncation(body, target);
  assert.equal(body.messages.at(-1)?.content, "Continue.");
  assert.equal(body.messages.some(m => m.role === "tool"), false);
  const prompt = JSON.stringify(f.request().payload.messages);
  for (const identifier of ["call-a", "call-b", "/work/state.json", "precise-v1", "/work/settings.json"]) assert.ok(prompt.includes(identifier));
});

test("reasoning history and large tool schemas count toward the compaction trigger", (t) => {
  const f = fixture(t);
  assert.equal(f.compactor.estimateTokens([{ role: "assistant", content: "visible", reasoning_content: "r".repeat(4000) }]), 1002);
  const body = history(10);
  body.tools = [{ type: "function", function: { name: "LargeSchema", description: "schema".repeat(11000) } }];
  assert.match(f.compactor.applyContextTruncation(body, target), /^truncated-/);
  assert.equal(f.request().payload.model, target.id);
});

test("a late error from a completed chunk cannot stop the next chunk", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  f.request().respond(200, "Completed first input.");
  f.request().fail();
  assert.equal(f.request(1).destroyed, false);
  f.request(1).respond(200, "Completed next input.");
  assert.equal((f.state(history(40)).summaryJob as { chunks: number }).chunks, 2);
});

test("an incomplete HTTP 200 response is never cached as a finished summary", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Cut off halfway through the task.", undefined, "length");
  assert.equal(f.state(history()).summary, undefined);
  assert.equal((f.state(history()).summaryJob as { lastError: string }).lastError, "incomplete-summary");
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.requests.length, 1);
});

test("a reasoning effort change cancels stale compaction and ignores its late completion", (t) => {
  const f = fixture(t);
  const body = history();
  body.reasoning_effort = "low";
  f.compactor.applyContextTruncation(body, target);
  const next = history();
  next.reasoning_effort = "max";
  f.compactor.applyContextTruncation(next, target);
  assert.equal(f.request().destroyed, true);
  assert.equal(f.request(1).payload.reasoning_effort, "max");
  f.request().respond(200, "Stale effort result.");
  assert.equal(f.state(history()).summary, undefined);
  f.request(1).respond(200, "Current effort result.");
  assert.equal(f.state(history()).summary, "Current effort result.");
});

test("editing a covered message cancels obsolete work while its late result stays uncached", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  const edited = history();
  edited.messages[1]!.content = "Corrected task identifier. " + edited.messages[1]!.content;
  f.compactor.applyContextTruncation(edited, target);
  assert.equal(f.request().destroyed, true);
  assert.ok(JSON.stringify(f.request(1).payload.messages).includes("Corrected task identifier."));
  f.request().respond(200, "Obsolete task summary.");
  assert.equal(f.state(history()).summary, undefined);
  f.request(1).respond(200, "Corrected task summary.");
  assert.equal(f.state(history()).summary, "Corrected task summary.");
});

test("switching models on a short request cancels the prior summarizer", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  const selected = f.plan.catalog.models.find(model => model.slug === "muse-spark-1.3-contributor")!;
  const short: Body = { conversationId: "chat-a", messages: [
    { role: "system", content: "System instructions." },
    { role: "user", content: "Continue on the new model." },
  ] };
  assert.equal(f.compactor.applyContextTruncation(short, selected), "skip-few");
  assert.equal(f.request().destroyed, true);
  assert.equal(f.requests.length, 1);
  f.request().respond(200, "Previous model result.");
  assert.equal(f.state(history()).summary, undefined);
});

test("removing covered history cancels the job even below the compaction threshold", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  assert.equal(f.compactor.applyContextTruncation(history(2), target), "none");
  assert.equal(f.request().destroyed, true);
  assert.equal(f.requests.length, 1);
  f.request().respond(200, "Removed history result.");
  assert.equal(f.state(history()).summary, undefined);
});

test("rolling summaries retain recorded identifiers without another provider call", (t) => {
  const f = fixture(t);
  const original = history(40);
  f.compactor.applyContextTruncation(original, target);
  f.request().respond(200, "## Facts and identifiers\n- Early request: `request-early-123`.\n- Obsolete route: `route-v1`.\n## Task state\nContinue the current task.");
  f.request(1).respond(200, "## Facts and identifiers\n- Current request: `request-late-456`.\n- Current route: `route-v2`.\n## Task state\nContinue on the new route.");
  assert.ok(JSON.stringify(f.request(2).payload.messages).includes("request-early-123"));
  assert.ok(JSON.stringify(f.request(2).payload.messages).includes("route-v1"));
  for (let i = 2; i < f.requests.length; i++) {
    assert.ok(i < 10, "unexpected additional requests");
    f.request(i).respond(200, "## Facts and identifiers\n- Current route: `route-v2`.\n## Task state\nContinue on the new route.");
  }
  const summary = f.state(history(40)).summary as string;
  assert.match(summary, /request-early-123/);
  assert.match(summary, /request-late-456/);
  assert.match(summary, /Historical identifiers.*may supersede/);
  assert.equal(summary.split("Early request:").length - 1, 1, "carried facts do not multiply");
  assert.equal(f.state(history(40)).summarySource, target.id);
  const before = f.requests.length;
  assert.match(f.compactor.applyContextTruncation(history(40), target), /cached$/);
  assert.equal(f.requests.length, before);
});

test("an oversized identifier ledger pauses without overwriting its completed checkpoint", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(40), target);
  const first = "## Facts and identifiers\n- Historical value: `" + "a".repeat(70000) + "`.";
  f.request().respond(200, first);
  const before = f.state(history(40)).summaryJob as { cursor: number; text: string };
  f.request(1).respond(200, "## Facts and identifiers\n- New value: `" + "b".repeat(70000) + "`.");
  const after = f.state(history(40)).summaryJob as { cursor: number; text: string; lastError: string };
  assert.equal(after.lastError, "identifier-budget");
  assert.equal(after.cursor, before.cursor);
  assert.equal(after.text, first);
  assert.equal(f.state(history(40)).summary, undefined);
  assert.equal(f.requests.length, 2);
});

test("one total job deadline bounds many successful chunks and saves completed progress", (t) => {
  const f = fixture(t);
  const huge = history(240);
  f.compactor.applyContextTruncation(huge, target);
  for (let i = 0; i < 6; i++) {
    f.clock.advance(99999);
    f.request(i).respond(200, "Chunk " + i);
  }
  f.clock.advance(10);
  assert.equal(f.requests.length, 7);
  assert.equal(f.state(history(240)).summary, undefined);
  const job = f.state(history(240)).summaryJob as { chunks: number; lastError: string };
  assert.equal(job.chunks, 6);
  assert.equal(job.lastError, "job-timeout");
});

test("an invalid primary context cannot silently move compaction to another route", (t) => {
  const f = fixture(t);
  f.plan.catalog.models[0] = { ...target, contextTokens: 0 };
  assert.equal(f.compactor.resolveSummarizer(f.plan, target), null);
});

test("a selected Kimi K3 bot still uses an approved non-Kimi compactor", (t) => {
  const f = fixture(t);
  assert.equal(f.compactor.resolveSummarizer(f.plan, kimi)?.modelId, target.id);
});

test("native oldest-message eviction retains the exact summary and provider prefix", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Keep all task facts, including those evicted by the native window.");
  const before = history();
  f.compactor.applyContextTruncation(before, target);
  const previousCount = Number(f.state(history()).summaryForDropped);
  const next = history(21);
  next.messages.splice(1, 2);
  assert.match(f.compactor.applyContextTruncation(next, target), /cached$/);
  assert.deepEqual(next.messages.slice(0, -2), before.messages.slice(0, -1));
  assert.equal(f.requests.length, 1, "eviction must not resummarize the old transcript");
  assert.equal(f.state(history()).summaryForDropped, previousCount - 2);
  const reloaded = f.reload();
  const again = history(21); again.messages.splice(1, 2);
  assert.match(reloaded.applyContextTruncation(again, target), /cached$/);
  assert.equal(JSON.stringify(again.messages), JSON.stringify(next.messages));
});

test("an in-flight summary finishes through native eviction without cancelling paid work", (t) => {
  const f = fixture(t);
  const before = history();
  f.compactor.applyContextTruncation(before, target);
  const previousCount = Number(f.state(history()).lastDropped);
  const next = history(21); next.messages.splice(1, 2);
  f.compactor.applyContextTruncation(next, target);
  assert.equal(f.request().destroyed, false);
  assert.equal(f.requests.length, 1);
  assert.deepEqual(next.messages.slice(0, -2), before.messages.slice(0, -1), "the fallback prefix also stays stable");
  f.request().respond(200, "Finished the original prefix, including its evicted facts.");
  assert.equal(f.state(history()).summaryForDropped, previousCount - 2);
  const after = history(21); after.messages.splice(1, 2);
  assert.match(f.compactor.applyContextTruncation(after, target), /cached$/);
  assert.equal(f.requests.length, 1);
});

test("an edited suffix after eviction invalidates the summary rather than concealing new facts", (t) => {
  const f = fixture(t);
  f.compactor.applyContextTruncation(history(), target);
  f.request().respond(200, "Obsolete fact.");
  const changed = history(23); changed.messages.splice(1, 2);
  assert.ok(changed.messages[2]); changed.messages[2].content = "This fact was corrected." + "z".repeat(6000);
  assert.match(f.compactor.applyContextTruncation(changed, target), /fallback$/);
  assert.equal(f.requests.length, 2);
  assert.ok(!changed.messages.some(m => m.content.includes("Obsolete fact.")));
});


test("legacy tool transcript offsets are rejected when the renderer gains IDs and arguments", (t) => {
  const f = fixture(t);
  const body = history(40);
  body.messages[1]!.tool_calls = [{ id: "legacy-tool", type: "function", function: { name: "ReadFile", arguments: '{"path":"/work/precise-state.json"}' } }];
  f.compactor.applyContextTruncation(body, target);
  f.request().respond(200, "Partial tool facts.");
  f.request(1).fail();
  const state = f.state(body);
  const saved = state.summaryJob as Record<string, unknown>;
  saved.version = 1;
  for (const key of ["routingKey", "coveredCount", "coveredHash", "coveredHashes", "transcriptHash"]) delete saved[key];
  fs.writeFileSync(path.join(f.dir, "openbot/compact-state", f.compactor.chatKey(body) + ".json"), JSON.stringify(state));
  f.clock.advance(60000);
  const fresh = history(40);
  fresh.messages[1]!.tool_calls = body.messages.find(m => m.tool_calls)?.tool_calls ?? [{ id: "legacy-tool", type: "function", function: { name: "ReadFile", arguments: '{"path":"/work/precise-state.json"}' } }];
  f.reload().applyContextTruncation(fresh, target);
  assert.equal((f.state(fresh).summaryJob as { cursor: number }).cursor, 0);
  assert.ok(JSON.stringify(f.request(2).payload.messages).includes("/work/precise-state.json"));
});
