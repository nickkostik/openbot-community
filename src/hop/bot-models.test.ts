import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const hopServer = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/hop-server.cjs");
const botId = "00000000-0000-4000-8000-feaccd25ce86";
const botMessages = [
  { role: "system", content: "Profile: /home/box/agent-data/agents/" + botId + "/profile.json" },
  { role: "user", content: "hello" },
];

function listen(handler: http.RequestListener): Promise<{ server: http.Server; port: number }> {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") throw new Error("no port");
      resolve({ server, port: addr.port });
    });
  });
}

function post(port: number, body: unknown): Promise<{ status: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request(
      {
        host: "127.0.0.1",
        port,
        path: "/v1/chat/completions",
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": String(payload.length) },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          resolve({ status: res.statusCode ?? 0, json: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
        });
      },
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

async function captureUpstream(): Promise<{
  server: http.Server;
  port: number;
  getBody: () => Record<string, unknown> | undefined;
  getPath: () => string;
}> {
  let body: Record<string, unknown> | undefined;
  let pathname = "";
  const { server, port } = await listen((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
      pathname = req.url ?? "";
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(pathname.endsWith("/responses")
        ? { id: "r", status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: "ok" }] }] }
        : pathname.endsWith("/messages")
          ? { id: "m", content: [{ type: "text", text: "ok" }], stop_reason: "end_turn" }
          : { choices: [{ message: { content: "ok" }, finish_reason: "stop" }] }));
    });
  });
  return { server, port, getBody: () => body, getPath: () => pathname };
}

async function freePort(): Promise<number> {
  const probe = await listen(() => undefined);
  const port = probe.port;
  probe.server.close();
  return port;
}

async function startHop(input: {
  upstreamPort: number;
  assignments: Record<string, string>;
  reasoning?: Record<string, Record<string, string>>;
  models?: Record<string, unknown>[];
}): Promise<{ port: number; child: ChildProcess; dir: string; settingsFile: string }> {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-hop-bot-models-"));
  const origin = "http://127.0.0.1:" + String(input.upstreamPort) + "/v1";
  writeFileSync(
    path.join(dir, "plan.json"),
    JSON.stringify({
      kind: "custom",
      agents: { "*": { modelId: input.models?.[0]?.slug ?? "glm-5.3-flash", providerId: "zhipu" } },
      catalog: {
        providers: [{ id: "zhipu", name: "Zhipu", origin, maxTokensDefault: 65536, mapFile: "provider-maps.cjs" }],
        models: input.models ?? [
          { id: "zhipu:glm-5.3-flash", providerId: "zhipu", slug: "glm-5.3-flash", parameters: [] },
          { id: "zhipu:glm-5.3", providerId: "zhipu", slug: "glm-5.3", parameters: [] },
        ],
        bindings: [],
      },
    }),
  );
  writeFileSync(path.join(dir, "secrets.json"), JSON.stringify({ providers: { zhipu: "sk-test" } }));
  const settingsFile = path.join(dir, "assignments.json");
  writeFileSync(settingsFile, JSON.stringify({ assignments: input.assignments, reasoning: input.reasoning ?? {} }));
  const port = await freePort();
  const child = spawn(process.execPath, [hopServer], {
    env: {
      ...process.env,
      OPENBOT_HOP_HOST: "127.0.0.1",
      OPENBOT_HOP_PORT: String(port),
      OPENBOT_SAND_DATA: dir,
      OPENBOT_LOGS: path.join(dir, "openbot-logs.json"),
      OPENBOT_PLAN: path.join(dir, "plan.json"),
      OPENBOT_SECRETS: path.join(dir, "secrets.json"),
      OPENBOT_BOT_MODELS: path.join(dir, "assignments.json"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("hop did not start")), 3000);
    child.stdout.on("data", (buf: Buffer) => {
      if (buf.toString("utf8").includes("listening")) {
        clearTimeout(timer);
        resolve();
      }
    });
    child.on("error", reject);
  });
  return { port, child, dir, settingsFile };
}

async function withHop(
  assignments: Record<string, string>,
  run: (hop: { port: number }, upstream: { getBody: () => Record<string, unknown> | undefined }) => Promise<void>,
): Promise<void> {
  const upstream = await captureUpstream();
  const hop = await startHop({ upstreamPort: upstream.port, assignments });
  try {
    await run(hop, upstream);
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
    await new Promise<void>((resolve) => hop.child.once("exit", () => resolve()));
    rmSync(hop.dir, { recursive: true, force: true });
  }
}

test("handleCompletions overrides the model when the bot has an assignment", async () => {
  await withHop({ [botId]: "zhipu:glm-5.3" }, async (hop, upstream) => {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: botMessages });
    assert.equal(out.status, 200);
    assert.equal(upstream.getBody()?.model, "glm-5.3");
  });
});

test("handleCompletions keeps the caller model when the bot has no assignment", async () => {
  await withHop({}, async (hop, upstream) => {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: botMessages });
    assert.equal(out.status, 200);
    assert.equal(upstream.getBody()?.model, "glm-5.3-flash");
  });
});

test("handleCompletions falls back to the caller model when the assignment is stale", async () => {
  await withHop({ [botId]: "zhipu:deleted-model" }, async (hop, upstream) => {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: botMessages });
    assert.equal(out.status, 200);
    assert.equal(upstream.getBody()?.model, "glm-5.3-flash");
  });
});

test("handleCompletions falls back to the wildcard when the body already carries the stale assignment", async () => {
  await withHop({ [botId]: "zhipu:deleted-model" }, async (hop, upstream) => {
    const out = await post(hop.port, { model: "zhipu:deleted-model", messages: botMessages });
    assert.equal(out.status, 200);
    assert.equal(upstream.getBody()?.model, "glm-5.3-flash");
  });
});

const efforts = ["default", "none", "low", "medium", "high", "xhigh", "max"];
const secondBot = "00000000-0000-4000-8000-0d23e78bd190";
const otherMessages = [{ role: "system", content: `/home/box/agent-data/agents/${secondBot}/profile.json` }, { role: "user", content: "hello" }];

async function withReasoningHop(
  apiType: string,
  run: (hop: Awaited<ReturnType<typeof startHop>>, upstream: Awaited<ReturnType<typeof captureUpstream>>) => Promise<void>,
  modelSlug = "gpt-6.1-sol",
) {
  const upstream = await captureUpstream();
  const hop = await startHop({ upstreamPort: upstream.port, assignments: {}, models: [
    { id: "zhipu:a", providerId: "zhipu", slug: modelSlug, reasoningLevels: efforts, activeReasoning: "high", apiType, parameters: [{ id: "fast", value: "true" }] },
    { id: "zhipu:b", providerId: "zhipu", slug: "gpt-6.1-other", reasoningLevels: ["default", "none", "low", "high"], activeReasoning: "low", apiType, parameters: [] },
  ] });
  try { await run(hop, upstream); }
  finally {
    hop.child.kill("SIGTERM"); upstream.server.close();
    await new Promise<void>((resolve) => hop.child.once("exit", () => resolve()));
    rmSync(hop.dir, { recursive: true, force: true });
  }
}

test("live hop applies per-model efforts across pin changes without leaking between bots", async () => {
  await withReasoningHop("chat-completions", async (hop, upstream) => {
    const reasoning = { [botId]: { "zhipu:a": "max", "zhipu:b": "high" }, [secondBot]: { "zhipu:a": "low" } };
    const save = (assignments: Record<string, string>) => writeFileSync(hop.settingsFile, JSON.stringify({ assignments, reasoning }));
    save({ [botId]: "zhipu:a" });
    assert.equal((await post(hop.port, { model: "gpt-6.1-other", messages: botMessages, reasoning_effort: "low", thinking: { type: "disabled" } })).status, 200);
    assert.equal(upstream.getBody()?.model, "gpt-6.1-sol");
    assert.equal(upstream.getBody()?.reasoning_effort, "max");
    assert.equal(upstream.getBody()?.thinking, undefined);
    save({ [botId]: "zhipu:b" });
    await post(hop.port, { model: "gpt-6.1-sol", messages: botMessages });
    assert.equal(upstream.getBody()?.model, "gpt-6.1-other");
    assert.equal(upstream.getBody()?.reasoning_effort, "high");
    save({});
    await post(hop.port, { model: "gpt-6.1-sol", messages: botMessages });
    assert.equal(upstream.getBody()?.reasoning_effort, "max");
    await post(hop.port, { model: "gpt-6.1-sol", messages: otherMessages });
    assert.equal(upstream.getBody()?.reasoning_effort, "low");
  });
});

test("explicit provider default, clearing to inherit, and stale choices do not wedge the hop", async () => {
  await withReasoningHop("chat-completions", async (hop, upstream) => {
    writeFileSync(hop.settingsFile, JSON.stringify({ reasoning: { [botId]: { "zhipu:a": "default", "zhipu:b": "max" } } }));
    await post(hop.port, { model: "gpt-6.1-sol", messages: botMessages, reasoning_effort: "max", reasoning: { effort: "max" }, thinking: { type: "enabled" }, output_config: { effort: "max" } });
    assert.equal(upstream.getBody()?.reasoning_effort, undefined);
    assert.equal(upstream.getBody()?.reasoning, undefined);
    assert.equal(upstream.getBody()?.thinking, undefined);
    assert.deepEqual(upstream.getBody()?.output_config, {});
    await post(hop.port, { model: "gpt-6.1-other", messages: botMessages });
    assert.equal(upstream.getBody()?.reasoning_effort, "low");
    writeFileSync(hop.settingsFile, JSON.stringify({ assignments: {}, reasoning: {} }));
    await post(hop.port, { model: "gpt-6.1-sol", messages: botMessages });
    assert.equal(upstream.getBody()?.reasoning_effort, "high");
  });
});

test("internal compaction requests retain their model and effort despite bot overrides", async () => {
  await withReasoningHop("chat-completions", async (hop, upstream) => {
    writeFileSync(hop.settingsFile, JSON.stringify({ assignments: { [botId]: "zhipu:a" }, reasoning: { [botId]: { "zhipu:a": "max", "zhipu:b": "high" } } }));
    assert.equal((await post(hop.port, { model: "gpt-6.1-other", messages: botMessages, openbot_internal_model: true })).status, 200);
    assert.equal(upstream.getBody()?.model, "gpt-6.1-other");
    assert.equal(upstream.getBody()?.reasoning_effort, "low");
  });
});

test("Responses sends selected max and Off effort in reasoning.effort", async () => {
  await withReasoningHop("responses", async (hop, upstream) => {
    for (const effort of ["max", "none"]) {
      writeFileSync(hop.settingsFile, JSON.stringify({ reasoning: { [botId]: { "zhipu:a": effort } } }));
      assert.equal((await post(hop.port, { model: "gpt-6.1-sol", messages: botMessages })).status, 200);
      assert.match(upstream.getPath(), /\/responses$/);
      assert.deepEqual(upstream.getBody()?.reasoning, { effort });
      assert.equal(upstream.getBody()?.reasoning_effort, undefined);
    }
  });
});

test("Anthropic sends selected max as adaptive thinking and Off as disabled thinking", async () => {
  await withReasoningHop("anthropic", async (hop, upstream) => {
    for (const effort of ["max", "none"]) {
      writeFileSync(hop.settingsFile, JSON.stringify({ reasoning: { [botId]: { "zhipu:a": effort } } }));
      assert.equal((await post(hop.port, { model: "claude-opus", messages: botMessages })).status, 200);
      assert.match(upstream.getPath(), /\/messages$/);
      assert.deepEqual(upstream.getBody()?.thinking, { type: effort === "none" ? "disabled" : "adaptive" });
      assert.deepEqual(upstream.getBody()?.output_config, effort === "none" ? undefined : { effort: "max" });
    }
  }, "claude-opus");
});

test("per-bot GLM reasoning overrides a legacy fast parameter", async () => {
  await withReasoningHop("chat-completions", async (hop, upstream) => {
    writeFileSync(hop.settingsFile, JSON.stringify({ reasoning: { [botId]: { "zhipu:a": "high" } } }));
    await post(hop.port, { model: "glm-5.3-flash", messages: botMessages });
    assert.equal(upstream.getBody()?.reasoning_effort, "high");
    assert.deepEqual(upstream.getBody()?.thinking, { type: "enabled" });
  }, "glm-5.3-flash");
});
