import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const hopServer = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/hop-server.cjs");

function listen(handler: http.RequestListener): Promise<{ server: http.Server; port: number }> {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        throw new Error("no port");
      }
      resolve({ server, port: addr.port });
    });
  });
}

function post(
  port: number,
  body: unknown,
  headers: Record<string, string>,
  pathname = "/v1/chat/completions",
): Promise<{ status: number; json: unknown }> {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request(
      {
        host: "127.0.0.1",
        port,
        path: pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(payload.length),
          ...headers,
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ status: res.statusCode ?? 0, json: JSON.parse(raw) as unknown });
        });
      },
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

async function freePort(): Promise<number> {
  const hop = await listen(() => undefined);
  const port = hop.port;
  hop.server.close();
  return port;
}

async function startHop(input: {
  plan: unknown;
  secrets: unknown;
  env?: Record<string, string>;
}): Promise<{ port: number; child: ChildProcess }> {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-hop-"));
  const planPath = path.join(dir, "plan.json");
  const secretsPath = path.join(dir, "secrets.json");
  writeFileSync(planPath, JSON.stringify(input.plan));
  writeFileSync(secretsPath, JSON.stringify(input.secrets));
  const port = await freePort();
  const child = spawn(process.execPath, [hopServer], {
    env: {
      ...process.env,
      ...input.env,
      OPENBOT_HOP_HOST: "127.0.0.1",
      OPENBOT_HOP_PORT: String(port),
      OPENBOT_SAND_DATA: dir,
      OPENBOT_LOGS: path.join(dir, "openbot-logs.json"),
      OPENBOT_PLAN: planPath,
      OPENBOT_SECRETS: secretsPath,
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
  return { port, child };
}

async function captureUpstream(): Promise<{
  server: http.Server;
  port: number;
  getAuth: () => string;
  getBody: () => Record<string, unknown> | undefined;
  getVersion: () => string;
  getUserAgent: () => string;
  getPath: () => string;
}> {
  let auth = "";
  let version = "";
  let userAgent = "";
  let urlPath = "";
  let body: Record<string, unknown> | undefined;
  const { server, port } = await listen((req, res) => {
    const header = req.headers.authorization;
    auth = typeof header === "string" ? header : "";
    const versionHeader = req.headers["x-openbot-version"];
    version = typeof versionHeader === "string" ? versionHeader : "";
    const ua = req.headers["user-agent"];
    userAgent = typeof ua === "string" ? ua : "";
    urlPath = req.url || "";
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          choices: [{ message: { content: "ok" }, finish_reason: "stop" }],
        }),
      );
    });
  });
  return {
    server,
    port,
    getAuth: () => auth,
    getBody: () => body,
    getVersion: () => version,
    getUserAgent: () => userAgent,
    getPath: () => urlPath,
  };
}

test("hop strips client Authorization and injects the secret store key", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "zhipu",
            name: "Zhipu",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "zhipu:glm", providerId: "zhipu", slug: "glm-5.3-flash", parameters: [] }],
        bindings: [],
      },
    },
    secrets: { providers: { zhipu: "sk-real" } },
  });
  try {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: [] }, { Authorization: "Bearer openbot-runtime" });
    assert.equal(out.status, 200);
    assert.equal(upstream.getAuth(), "Bearer sk-real");
    assert.notEqual(upstream.getAuth(), "Bearer openbot-runtime");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop prefers the wildcard-bound provider when two models share a slug", async () => {
  const upstream = await captureUpstream();
  const origin = `http://127.0.0.1:${String(upstream.port)}/v1`;
  const hop = await startHop({
    plan: {
      kind: "custom",
      agents: { "*": { modelId: "shared-slug", providerId: "openai" } },
      catalog: {
        providers: [
          { id: "zhipu", name: "Zhipu", origin, maxTokensDefault: 65536, mapFile: "provider-maps.cjs" },
          { id: "openai", name: "OpenAI", origin, maxTokensDefault: 65536, mapFile: "provider-maps.cjs" },
        ],
        models: [
          { id: "zhipu:shared-slug", providerId: "zhipu", slug: "shared-slug", parameters: [] },
          { id: "openai:shared-slug", providerId: "openai", slug: "shared-slug", parameters: [] },
        ],
        bindings: [],
      },
    },
    secrets: { providers: { zhipu: "sk-zhipu", openai: "sk-openai" } },
  });
  try {
    const out = await post(hop.port, { model: "shared-slug", messages: [] }, { Authorization: "Bearer openbot-runtime" });
    assert.equal(out.status, 200);
    assert.equal(upstream.getAuth(), "Bearer sk-openai");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop caps max_tokens and maps active reasoning to reasoning_effort", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "openai",
            name: "OpenAI",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [
          {
            id: "openai:gpt-4.1",
            providerId: "openai",
            slug: "gpt-4.1",
            maxOutputTokens: 4096,
            activeReasoning: "high",
            parameters: [],
          },
        ],
        bindings: [],
      },
    },
    secrets: { providers: { openai: "sk-openai" } },
  });
  try {
    const out = await post(
      hop.port,
      { model: "gpt-4.1", messages: [], max_tokens: 99999 },
      { Authorization: "Bearer openbot-runtime" },
    );
    assert.equal(out.status, 200);
    const body = upstream.getBody();
    assert.equal(body?.model, "gpt-4.1");
    assert.equal(body?.max_tokens, 4096);
    assert.equal(body?.reasoning_effort, "high");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop leaves GLM thinking unset when old catalog none means default", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "zhipu",
            name: "Zhipu",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [
          {
            id: "zhipu:glm",
            providerId: "zhipu",
            slug: "glm-5.3-flash",
            activeReasoning: "none",
            parameters: [],
          },
        ],
        bindings: [],
      },
    },
    secrets: { providers: { zhipu: "sk-real" } },
  });
  try {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: [] }, { Authorization: "Bearer openbot-runtime" });
    assert.equal(out.status, 200);
    const body = upstream.getBody();
    assert.equal(body?.thinking, undefined);
    assert.equal(body?.reasoning_effort, undefined);
    assert.equal(body?.max_tokens, 65536);
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop sends GLM thinking disabled when Off is chosen after default exists", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "zhipu",
            name: "Zhipu",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [
          {
            id: "zhipu:glm",
            providerId: "zhipu",
            slug: "glm-5.3-flash",
            reasoningLevels: ["default", "none", "low", "high"],
            activeReasoning: "none",
            parameters: [],
          },
        ],
        bindings: [],
      },
    },
    secrets: { providers: { zhipu: "sk-real" } },
  });
  try {
    const out = await post(hop.port, { model: "glm-5.3-flash", messages: [] }, { Authorization: "Bearer openbot-runtime" });
    assert.equal(out.status, 200);
    const body = upstream.getBody();
    assert.deepEqual(body?.thinking, { type: "disabled" });
    assert.equal(body?.reasoning_effort, undefined);
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop does not serve /v1/responses or /v1/messages", async () => {
  const hop = await startHop({
    plan: { kind: "custom", catalog: { providers: [], models: [], bindings: [] } },
    secrets: { providers: {} },
  });
  try {
    const responses = await post(hop.port, { model: "gpt-4.1" }, {}, "/v1/responses");
    const messages = await post(hop.port, { model: "claude" }, {}, "/v1/messages");
    assert.equal(responses.status, 404);
    assert.equal(messages.status, 404);
  } finally {
    hop.child.kill("SIGTERM");
  }
});

test("hop fills tool_call_id on tool messages before the upstream", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "deepseek",
            name: "DeepSeek",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "deepseek:v4", providerId: "deepseek", slug: "deepseek-v4-flash", parameters: [] }],
        bindings: [],
      },
    },
    secrets: { providers: { deepseek: "sk-deepseek" } },
  });
  try {
    const out = await post(
      hop.port,
      {
        model: "deepseek-v4-flash",
        messages: [
          { role: "user", content: "hi" },
          {
            role: "assistant",
            content: "",
            tool_calls: [{ id: "call_abc", type: "function", function: { name: "Read", arguments: "{}" } }],
          },
          { role: "tool", content: '{"file":"huge"}' },
          {
            role: "assistant",
            content: [
              { type: "text", text: "next" },
              { type: "tool-call", toolCallId: "call_def", toolName: "Grep", args: { q: "x" } },
            ],
          },
          {
            role: "tool",
            content: [{ type: "tool-result", toolCallId: "call_def", result: { hits: 1 } }],
          },
        ],
      },
      { Authorization: "Bearer openbot-runtime" },
    );
    assert.equal(out.status, 200);
    const body = upstream.getBody();
    const messages = body?.messages as { role: string; tool_call_id?: string; tool_calls?: { id: string }[] }[];
    assert.equal(messages[2]?.role, "tool");
    assert.equal(messages[2]?.tool_call_id, "call_abc");
    assert.equal(messages[4]?.role, "tool");
    assert.equal(messages[4]?.tool_call_id, "call_def");
    assert.equal(messages[3]?.tool_calls?.[0]?.id, "call_def");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop posts host reminder and hidden-prompt text unchanged", async () => {
  const query =
    "<timestamp>Wednesday, Sep 2, 2026, 3:35 PM (UTC+8)</timestamp>\n<user_query>\n[t1u]\n你看看\n\n<system_reminder>\nYou opened this turn by calling tools without first acknowledging the user\n</system_reminder>\n</user_query>";
  const reminderOnly =
    "<system_reminder>\nYou opened this turn by calling tools without first acknowledging the user, so they are watching silence\n</system_reminder>";
  const redrive =
    "<timestamp>Wednesday, Sep 2, 2026, 3:37 PM (UTC+8)</timestamp>\n<user_query>\n[SAND_HIDDEN_PROMPT][ack-redrive-00000000-0000-4000-8000-2de9ae9621ef]\n[System recovery] The user sent one or more messages\n</user_query>";
  const nudge =
    "[SAND_HIDDEN_PROMPT] Your previous turn left the user without the result they are waiting on — you never called SendToUser. Invoke SendToUser now with the result.";
  const upstream = await captureUpstream();
  const hop = await startHop({
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "deepseek",
            name: "DeepSeek",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "deepseek:v4", providerId: "deepseek", slug: "deepseek-v4-flash", parameters: [] }],
        bindings: [],
      },
    },
    secrets: { providers: { deepseek: "sk-deepseek" } },
  });
  try {
    const out = await post(
      hop.port,
      {
        model: "deepseek-v4-flash",
        messages: [
          { role: "user", content: query },
          { role: "user", content: reminderOnly },
          { role: "user", content: redrive },
          { role: "user", content: nudge },
        ],
      },
      { Authorization: "Bearer openbot-runtime" },
    );
    assert.equal(out.status, 200);
    const body = upstream.getBody();
    const messages = body?.messages as { role: string; content: string }[];
    assert.equal(messages.length, 4);
    assert.equal(messages[0]?.content, query);
    assert.equal(messages[1]?.content, reminderOnly);
    assert.equal(messages[2]?.content, redrive);
    assert.equal(messages[3]?.content, nudge);
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop sends x-openbot-version from OPENBOT_COMMIT", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    env: { OPENBOT_COMMIT: "cafed00d" },
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "deepseek",
            name: "DeepSeek",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "deepseek:v4", providerId: "deepseek", slug: "deepseek-v4-flash", parameters: [] }],
        bindings: [],
      },
    },
    secrets: { providers: { deepseek: "sk-deepseek" } },
  });
  try {
    const out = await post(hop.port, { model: "deepseek-v4-flash", messages: [] }, { Authorization: "Bearer openbot-runtime" });
    assert.equal(out.status, 200);
    assert.equal(upstream.getVersion(), "cafed00d");
    assert.equal(upstream.getUserAgent(), "");
    assert.equal(upstream.getPath(), "/v1/chat/completions");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("hop copies inbound User-Agent and does not invent one", async () => {
  const upstream = await captureUpstream();
  const hop = await startHop({
    env: { OPENBOT_COMMIT: "cafed00d" },
    plan: {
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "deepseek",
            name: "DeepSeek",
            origin: `http://127.0.0.1:${String(upstream.port)}/v1`,
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "deepseek:v4", providerId: "deepseek", slug: "deepseek-v4-flash", parameters: [] }],
        bindings: [],
      },
    },
    secrets: { providers: { deepseek: "sk-deepseek" } },
  });
  try {
    const out = await post(
      hop.port,
      { model: "deepseek-v4-flash", messages: [] },
      { Authorization: "Bearer openbot-runtime", "User-Agent": "GrokBot/test-client" },
    );
    assert.equal(out.status, 200);
    assert.equal(upstream.getVersion(), "cafed00d");
    assert.equal(upstream.getUserAgent(), "GrokBot/test-client");
  } finally {
    hop.child.kill("SIGTERM");
    upstream.server.close();
  }
});

test("Go switching preserves account credentials, protocols and session identity on streams", async () => {
  const captured: Array<{path: string; headers: http.IncomingHttpHeaders; body: any}> = [];
  const upstream = await listen((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", c => chunks.push(c));
    req.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString());
      captured.push({ path: req.url!, headers: req.headers, body });
      res.writeHead(200, {"Content-Type": body.stream ? "text/event-stream" : "application/json"});
      res.end(body.stream ? 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n' : JSON.stringify({choices: [{message: {content: "ok"}, finish_reason: "stop"}]}));
    });
  });
  const providers = ["opencode", "opencode-2"].map(id => ({id, name: id, origin: `http://127.0.0.1:${upstream.port}/v1`, apiType: "responses", mapFile: "provider-maps.cjs"}));
  const models = providers.map(p => ({id: `${p.id}:flash`, providerId: p.id, slug: "flash", apiType: "chat-completions", parameters: []}));
  const server = await startHop({plan: {kind: "custom", catalog: {providers, models, bindings: []}}, secrets: {providers: {opencode: "test-account-one", "opencode-2": "test-account-two"}}});
  try {
    for (const [model, conversationId, stream, auxiliary] of [["opencode:flash", "chat-a", false, undefined], ["opencode:flash", "chat-a", true, undefined], ["opencode:flash", "chat-b", true, undefined], ["opencode-2:flash", "chat-a", true, undefined], ["opencode:flash", "chat-a", true, true], ["opencode:flash", "chat-a", false, true], ["opencode:flash", "chat-a", true, "true"]] as const) {
      const response = await fetch(`http://127.0.0.1:${server.port}/v1/chat/completions`, {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({model, conversationId, stream, openbot_internal_auxiliary: auxiliary, messages:[{role:"user",content:"routing test"}]})});
      assert.equal(response.status, 200);
      await response.text();
    }
    assert.equal(captured.length, 7);
    assert.ok(captured.every(r => r.path === "/v1/chat/completions"));
    assert.ok(captured[0]!.headers["x-opencode-session"]);
    assert.equal(captured[0]!.headers["x-opencode-session"], captured[1]!.headers["x-opencode-session"]);
    assert.notEqual(captured[1]!.headers["x-opencode-session"], captured[2]!.headers["x-opencode-session"]);
    assert.notEqual(captured[1]!.headers["x-opencode-session"], captured[3]!.headers["x-opencode-session"]);
    assert.notEqual(captured[1]!.headers["x-opencode-session"], captured[4]!.headers["x-opencode-session"], "helpers have separate Go session affinity");
    assert.equal(captured[4]!.headers["x-opencode-session"], captured[5]!.headers["x-opencode-session"], "helper affinity is stable across streaming modes");
    assert.equal(captured[1]!.headers["x-opencode-session"], captured[6]!.headers["x-opencode-session"], "only the internal boolean changes Go session affinity");
    assert.equal(captured[0]!.headers.authorization, "Bearer test-account-one");
    assert.equal(captured[3]!.headers.authorization, "Bearer test-account-two");
    assert.ok(captured.every(r => !r.body.conversationId));
    assert.ok(captured.every(r => !Object.hasOwn(r.body, "openbot_internal_auxiliary")));
  } finally {
    server.child.kill("SIGTERM");
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});

test("Go identifies native and auxiliary callers with OpenBot without replacing a supplied client", async () => {
  const upstream = await captureUpstream();
  const server = await startHop({ env: { OPENBOT_COMMIT: "cafed00d" }, plan: { kind: "custom", catalog: {
    providers: [{ id: "opencode-2", name: "Go 2", origin: `http://127.0.0.1:${upstream.port}/v1`, mapFile: "provider-maps.cjs" }],
    models: [{ id: "opencode-2:flash", providerId: "opencode-2", slug: "flash", parameters: [] }], bindings: [],
  } }, secrets: { providers: { "opencode-2": "test-account-two" } } });
  try {
    for (const stream of [true, false]) {
      const out = await post(server.port, { model: "opencode-2:flash", conversationId: "native", messages: [], stream, openbot_internal_auxiliary: !stream }, {});
      assert.equal(out.status, 200);
      assert.equal(upstream.getUserAgent(), "OpenBot/cafed00d");
      assert.equal(upstream.getVersion(), "cafed00d");
    }
    const supplied = await post(server.port, { model: "opencode-2:flash", messages: [] }, { "User-Agent": "GrokBot/test-client" });
    assert.equal(supplied.status, 200);
    assert.equal(upstream.getUserAgent(), "GrokBot/test-client");
  } finally {
    server.child.kill("SIGTERM");
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});

test("long text turns are bounded before upstream delivery and the shared loop keeps serving health", async () => {
  const upstream = await captureUpstream();
  const server = await startHop({ plan: { kind: "custom", catalog: {
    providers: [{ id: "direct", name: "Direct", origin: `http://127.0.0.1:${upstream.port}/v1` }],
    models: [{ id: "direct:model", providerId: "direct", slug: "model", parameters: [] }], bindings: [],
  } }, secrets: { providers: { direct: "test-key" } } });
  try {
    const current = { role: "user", content: "Preserve the entire latest request." };
    const body = { model: "direct:model", messages: [
      { role: "system", content: "Stable instructions" },
      ...Array.from({ length: 1600 }, () => ({ role: "assistant", content: "x".repeat(12000) })), current,
    ] };
    const turn = post(server.port, body, {});
    await new Promise(resolve => setTimeout(resolve, 20));
    const start = performance.now();
    const health = await fetch(`http://127.0.0.1:${server.port}/healthz`);
    assert.equal(health.status, 200);
    assert.ok(performance.now() - start < 2000, "a text-budget pass starved the shared loop");
    assert.equal((await turn).status, 200);
    const forwarded = upstream.getBody()!;
    const messages = forwarded.messages as { role: string; content: string }[];
    assert.deepEqual(messages.at(-1), current);
    assert.equal(messages[0]?.content, "Stable instructions");
    assert.ok(messages.length < 1602);
    assert.ok(Buffer.byteLength(JSON.stringify(forwarded)) <= Math.floor(3.4 * 1024 * 1024));

    const tooBig = await post(server.port, { model: "direct:model", messages: [{ role: "user", content: "y".repeat(4 * 1024 * 1024) }] }, {});
    assert.equal(tooBig.status, 413);
    assert.equal((tooBig.json as { error: { code: string } }).error.code, "openbot_wire_budget_exceeded");
    assert.deepEqual(upstream.getBody(), forwarded, "an oversized current request must never reach the provider");
  } finally {
    server.child.kill("SIGTERM");
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});

test("compaction sees the complete text history before the final wire clamp", async () => {
  const captured: { messages: { role: string; content: string }[] }[] = [];
  const upstream = await listen((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      captured.push(JSON.parse(Buffer.concat(chunks).toString()));
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "Test response" }, finish_reason: "stop" }] }));
    });
  });
  const server = await startHop({ plan: { kind: "custom", catalog: {
    providers: [{ id: "direct", name: "Direct", origin: `http://127.0.0.1:${upstream.port}/v1` }],
    models: [{ id: "direct:model", providerId: "direct", slug: "model", contextTokens: 128000, parameters: [] }], bindings: [],
  } }, secrets: { providers: { direct: "test-key" } } });
  try {
    const current = { role: "user", content: "Keep this latest question in full." };
    const out = await post(server.port, { model: "direct:model", conversationId: "compaction-before-wire", messages: [
      { role: "system", content: "Stable instructions" },
      ...Array.from({ length: 1600 }, () => ({ role: "assistant", content: "x".repeat(12000) })), current,
    ] }, {});
    assert.equal(out.status, 200);
    const forwarded = captured.find(body => body.messages.at(-1)?.content === current.content)!;
    assert.ok(forwarded, "the chat request was delivered");
    assert.ok(forwarded.messages.some(message => message.role === "system" && message.content.includes("earlier context summarized")),
      "the wire clamp discarded history before the compactor could preserve it");
    assert.deepEqual(forwarded.messages.at(-1), current);
  } finally {
    server.child.kill("SIGTERM");
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});
