import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import type http from "node:http";
import test from "node:test";
import { handleBotModelsApi } from "./bot-models.ts";

function request(method: string, body?: unknown): http.IncomingMessage {
  const req = Readable.from(body === undefined ? [] : [JSON.stringify(body)]) as Readable & { method: string; url: string };
  req.method = method;
  req.url = "/api/bot-models";
  return req as unknown as http.IncomingMessage;
}

function response(): { value: { status: number; body: unknown }; res: any } {
  const value = { status: 0, body: undefined as unknown };
  const res = {
    writeHead(status: number) { value.status = status; },
    end(body: string) { value.body = JSON.parse(body); },
  };
  return { value, res };
}

test("bot model API validates models and supports clearing and replacement", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-ui-bot-models-"));
  const file = path.join(root, "openbot-bot-models.json");
  const previous = process.env.OPENBOT_BOT_MODELS;
  process.env.OPENBOT_BOT_MODELS = file;
  const current = {
    paths: { sandData: root, plan: path.join(root, "openbot-plan.json") },
    fs: { read: () => undefined },
  } as any;
  const catalog = { providers: [], bindings: [], models: [{ id: "provider:model" }] } as any;
  const readBody = async (req: Readable) => { const chunks: Buffer[] = []; for await (const chunk of req) chunks.push(Buffer.from(chunk)); return Buffer.concat(chunks).toString(); };
  try {
    const bad = response();
    await handleBotModelsApi(request("PUT", { botId: "bot-1", modelId: "missing" }), bad.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.equal(bad.value.status, 400);
    const set = response();
    await handleBotModelsApi(request("PUT", { botId: "bot-1", modelId: "provider:model" }), set.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.equal(set.value.status, 200);
    const clear = response();
    await handleBotModelsApi(request("PUT", { botId: "bot-1", modelId: null }), clear.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.deepEqual((clear.value.body as any).assignments, {});
    const replace = response();
    await handleBotModelsApi(request("PUT", { assignments: { "bot-2": "provider:model" } }), replace.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.deepEqual((replace.value.body as any).assignments, { "bot-2": "provider:model" });
    const batch = response();
    await handleBotModelsApi(request("PUT", { botIds: ["bot-3", "bot-4"], modelId: "provider:model" }), batch.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.equal(batch.value.status, 200);
    assert.deepEqual((batch.value.body as any).assignments, { "bot-2": "provider:model", "bot-3": "provider:model", "bot-4": "provider:model" });
    const badBatch = response();
    await handleBotModelsApi(request("PUT", { botIds: ["bot-3"], modelId: "missing" }), badBatch.res, new URL("http://localhost/api/bot-models"), current, readBody, (res, status, body) => { res.writeHead(status); res.end(JSON.stringify(body)); }, () => catalog);
    assert.equal(badBatch.value.status, 400);
  } finally {
    if (previous === undefined) delete process.env.OPENBOT_BOT_MODELS;
    else process.env.OPENBOT_BOT_MODELS = previous;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

async function withReasoningApi(run: (api: {
  file: string;
  put: (body: unknown, read?: () => Promise<string>) => Promise<{ status: number; body: any }>;
  get: () => Promise<any>;
}) => Promise<void>) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-ui-bot-reasoning-"));
  const file = path.join(root, "openbot-bot-models.json");
  const previous = process.env.OPENBOT_BOT_MODELS;
  process.env.OPENBOT_BOT_MODELS = file;
  const current = { paths: { sandData: root, plan: path.join(root, "plan.json") }, fs: { read: () => undefined } } as any;
  const catalog = { models: [
    { id: "one:full", reasoningLevels: ["default", "none", "low", "high", "xhigh", "max"] },
    { id: "two:limited", reasoningLevels: ["default", "none", "low", "high"] },
    { id: "three:plain", reasoningLevels: ["default"] },
  ] } as any;
  const invoke = async (method: string, body?: unknown, read?: () => Promise<string>) => {
    const out = response();
    await handleBotModelsApi(request(method, body), out.res, new URL("http://localhost/api/bot-models"), current,
      read ?? (async () => JSON.stringify(body)),
      (res, status, value) => { res.writeHead(status); res.end(JSON.stringify(value)); }, () => catalog);
    return out.value as { status: number; body: any };
  };
  try { await run({ file, put: (body, read) => invoke("PUT", body, read), get: async () => (await invoke("GET")).body }); }
  finally {
    if (previous === undefined) delete process.env.OPENBOT_BOT_MODELS; else process.env.OPENBOT_BOT_MODELS = previous;
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("bot reasoning batch persists selected models without changing pins or unrelated choices", async () => {
  await withReasoningApi(async ({ file, put, get }) => {
    fs.writeFileSync(file, JSON.stringify({ assignments: { a: "one:full", b: "two:limited" }, reasoning: { a: { "one:full": "max" }, other: { "one:full": "low" } }, recoveryNote: "keep" }));
    assert.equal((await put({ botIds: ["a", "b"], modelIds: ["one:full", "two:limited"], reasoningEffort: "high" })).status, 200);
    assert.deepEqual((await get()).assignments, { a: "one:full", b: "two:limited" });
    assert.deepEqual((await get()).reasoning, { a: { "one:full": "high", "two:limited": "high" }, b: { "one:full": "high", "two:limited": "high" }, other: { "one:full": "low" } });
    assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).recoveryNote, "keep");
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    await put({ botId: "a", modelId: "two:limited" });
    await put({ botId: "a", modelId: null });
    assert.deepEqual((await get()).reasoning.a, { "one:full": "high", "two:limited": "high" });
    await put({ assignments: { c: "one:full" } });
    assert.equal((await get()).reasoning.other["one:full"], "low");
  });
});

test("reasoning default is explicit; clearing one model restores inheritance only for that model", async () => {
  await withReasoningApi(async ({ put, get }) => {
    await put({ botId: "a", modelId: "one:full", reasoningEffort: "max" });
    await put({ botId: "a", reasoningModelId: "two:limited", reasoningEffort: "default" });
    assert.equal((await get()).reasoning.a["two:limited"], "default");
    await put({ botIds: ["a"], modelIds: ["one:full"], reasoningEffort: null });
    assert.deepEqual((await get()).reasoning.a, { "two:limited": "default" });
    assert.equal((await get()).assignments.a, "one:full");
    await put({ botId: "a", reasoningModelId: "two:limited", reasoningEffort: null });
    assert.deepEqual((await get()).reasoning, {});
  });
});

test("unsupported batch efforts and malformed settings leave the whole file unchanged", async () => {
  await withReasoningApi(async ({ file, put }) => {
    fs.writeFileSync(file, JSON.stringify({ assignments: { a: "two:limited" }, reasoning: { a: { "one:full": "low" } } }));
    const before = fs.readFileSync(file, "utf8");
    for (const body of [
      { botIds: ["a", "b"], modelId: "one:full", modelIds: ["one:full", "two:limited"], reasoningEffort: "max" },
      { botIds: ["a"], modelIds: ["three:plain"], reasoningEffort: "high" },
      { botIds: [], modelIds: ["one:full"], reasoningEffort: "high" },
      { botIds: ["a"], modelIds: [], reasoningEffort: null },
      { botId: "a", reasoningModelId: "missing", reasoningEffort: "high" },
      { botId: "a", reasoningModelId: "one:full", reasoningEffort: "bogus" },
      { botId: "__proto__", modelId: "one:full" },
    ]) {
      assert.equal((await put(body)).status, 400);
      assert.equal(fs.readFileSync(file, "utf8"), before);
    }
  });
});

test("a delayed request body cannot overwrite another completed bot settings save", async () => {
  await withReasoningApi(async ({ put, get }) => {
    let release!: (value: string) => void;
    const pending = put({}, () => new Promise<string>((resolve) => { release = resolve; }));
    await put({ botId: "a", modelId: "one:full" });
    release(JSON.stringify({ botId: "b", reasoningModelId: "two:limited", reasoningEffort: "high" }));
    assert.equal((await pending).status, 200);
    const saved = await get();
    assert.equal(saved.assignments.a, "one:full");
    assert.equal(saved.reasoning.b["two:limited"], "high");
  });
});
