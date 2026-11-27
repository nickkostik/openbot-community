import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);

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

function withHopEnv<T>(origin: string, apiType: string, fn: () => Promise<T>, contextTokens?: number): Promise<T> {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-hop-err-"));
  const planPath = path.join(dir, "plan.json");
  const secretsPath = path.join(dir, "secrets.json");
  writeFileSync(
    planPath,
    JSON.stringify({
      kind: "custom",
      catalog: {
        providers: [
          { id: "upstream", name: "Upstream", origin, apiType, maxTokensDefault: 65536, mapFile: "provider-maps.cjs" },
        ],
        models: [{ id: "upstream:m", providerId: "upstream", slug: "model-x", parameters: [], contextTokens }],
        bindings: [],
      },
    }),
  );
  writeFileSync(secretsPath, JSON.stringify({ providers: { upstream: "sk-real" } }));
  const prev = {
    plan: process.env.OPENBOT_PLAN,
    secrets: process.env.OPENBOT_SECRETS,
    sand: process.env.OPENBOT_SAND_DATA,
    logs: process.env.OPENBOT_LOGS,
  };
  process.env.OPENBOT_PLAN = planPath;
  process.env.OPENBOT_SECRETS = secretsPath;
  process.env.OPENBOT_SAND_DATA = dir;
  process.env.OPENBOT_LOGS = path.join(dir, "openbot-logs.json");
  const restore = () => {
    for (const [key, value] of Object.entries(prev)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  };
  return fn().finally(restore);
}

test("the hop reserves completion room across provider protocols", async (t) => {
  const contextTokens = 128000;
  const output = 65536;
  const latest = "Continue the current task.";
  const history = Array.from({ length: 20 }, (_, index) => ({
    role: index % 2 ? "assistant" : "user", content: String(index) + "x".repeat(18000),
  }));
  const mockReplies = {
    "chat-completions": { choices: [{ message: { role: "assistant", content: "fits" }, finish_reason: "stop" }] },
    responses: { object: "response", status: "completed", output: [
      { type: "message", role: "assistant", content: [{ type: "output_text", text: "fits" }] },
    ] },
    anthropic: { type: "message", role: "assistant", content: [{ type: "text", text: "fits" }], stop_reason: "end_turn" },
  };
  for (const [protocol, answer] of Object.entries(mockReplies)) {
    await t.test(protocol, async () => {
      const seen: { input: number; output: number; keptLatest: boolean; outputFields: string[] }[] = [];
      const upstream = await listen((req, res) => {
        const chunks: Buffer[] = [];
        req.on("data", (chunk: Buffer) => chunks.push(chunk));
        req.on("end", () => {
          const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          const wire = JSON.stringify(body.messages ?? body.input);
          const input = Math.ceil(Buffer.byteLength(wire) / 4);
          const budget = Number(body.max_output_tokens ?? body.max_completion_tokens ?? body.max_tokens);
          const outputFields = ["max_output_tokens", "max_completion_tokens", "max_tokens"]
            .filter(field => Object.hasOwn(body, field));
          seen.push({ input, output: budget, keptLatest: wire.includes(latest), outputFields });
          const fits = input + budget <= contextTokens;
          res.writeHead(fits ? 200 : 400, { "Content-Type": "application/json" });
          res.end(JSON.stringify(fits ? answer : { error: { message: "Request exceeds context window" } }));
        });
      });
      try {
        await withHopEnv(`http://127.0.0.1:${upstream.port}/v1`, protocol, async () => {
          const hopServer = await startHopServer();
          try {
            const out = await postJson(hopServer.port, { model: "model-x", conversationId: "context-fit",
              max_tokens: output, messages: [...history, { role: "user", content: latest }] });
            assert.equal(out.status, 200);
            assert.equal(seen.length, 1);
            assert.equal(seen[0]?.keptLatest, true);
            assert.equal(seen[0]?.output, output);
            assert.ok(seen[0]!.input < contextTokens * 0.35);
            if (protocol !== "anthropic") {
              const aliased = await postJson(hopServer.port, { model: "model-x", conversationId: "context-fit",
                max_tokens: 256, max_completion_tokens: output, messages: [...history, { role: "user", content: latest }] });
              assert.equal(aliased.status, 200);
              assert.equal(seen[1]?.input, seen[0]?.input);
              assert.equal(seen[1]?.output, output);
              assert.deepEqual(seen[1]?.outputFields, [protocol === "responses" ? "max_output_tokens" : "max_completion_tokens"]);
            }
          } finally {
            hopServer.server.close();
            hopServer.server.closeAllConnections();
          }
        }, contextTokens);
      } finally {
        upstream.server.close();
        upstream.server.closeAllConnections();
      }
    });
  }
});

async function startHopServer(): Promise<{ server: http.Server; port: number }> {
  // The compactor captures its data directory at module load.
  const entry = require.resolve("../../payload/hop-handler.cjs");
  delete require.cache[entry];
  delete require.cache[require.resolve("../../payload/context-truncate.cjs")];
  const hop = require(entry) as {
    handleHopRequest: (req: http.IncomingMessage, res: http.ServerResponse) => Promise<boolean>;
  };
  const { server, port } = await listen((req, res) => {
    void hop.handleHopRequest(req, res).then((handled) => {
      if (!handled && !res.headersSent) {
        res.writeHead(404);
        res.end();
      }
    });
  });
  return { server, port };
}

function postJson(
  port: number,
  body: unknown,
): Promise<{ status: number; headers: http.IncomingHttpHeaders; json: any }> {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request(
      {
        host: "127.0.0.1",
        port,
        path: "/v1/chat/completions",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(payload.length),
          Authorization: "Bearer openbot-runtime",
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          resolve({ status: res.statusCode ?? 0, headers: res.headers, json: JSON.parse(raw) });
        });
      },
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

test("responses upstream 429 keeps status, headers, and request id after conversion", async () => {
  let hits = 0;
  const upstream = await listen((req, res) => {
    req.resume();
    req.on("end", () => {
      hits += 1;
      res.writeHead(429, {
        "Content-Type": "application/json",
        "x-request-id": "req-429",
        "Retry-After": "1",
      });
      res.end(JSON.stringify({
        error: { message: "rate limited", code: "rate_limit_exceeded" },
        request_id: "req-429",
      }));
    });
  });
  try {
    await withHopEnv(`http://127.0.0.1:${String(upstream.port)}/v1`, "responses", async () => {
      const hopServer = await startHopServer();
      try {
        const out = await postJson(hopServer.port, { model: "model-x", messages: [{ role: "user", content: "hi" }] });
        assert.equal(hits, 4);
        assert.equal(out.status, 429);
        assert.equal(out.headers["x-request-id"], "req-429");
        assert.equal(out.headers["retry-after"], "1");
        assert.equal(out.json.error.message, "rate limited");
        assert.equal(out.json.error.code, "rate_limit_exceeded");
        assert.equal(out.json.error.upstream_status, 429);
        assert.equal(out.json.error.request_id, "req-429");
      } finally {
        hopServer.server.close();
        hopServer.server.closeAllConnections();
      }
    });
  } finally {
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});

test("anthropic upstream 500 keeps status and x-request-id after conversion", async () => {
  let hits = 0;
  const upstream = await listen((req, res) => {
    req.resume();
    req.on("end", () => {
      hits += 1;
      res.writeHead(500, {
        "Content-Type": "application/json",
        "x-request-id": "req-500",
      });
      res.end(JSON.stringify({
        type: "error",
        error: { type: "api_error", message: "boom" },
      }));
    });
  });
  try {
    await withHopEnv(`http://127.0.0.1:${String(upstream.port)}`, "anthropic", async () => {
      const hopServer = await startHopServer();
      try {
        const out = await postJson(hopServer.port, { model: "model-x", messages: [{ role: "user", content: "hi" }] });
        assert.equal(hits, 3);
        assert.equal(out.status, 500);
        assert.equal(out.headers["x-request-id"], "req-500");
        assert.equal(out.json.error.message, "boom");
        assert.equal(out.json.error.code, "api_error");
        assert.equal(out.json.error.upstream_status, 500);
        assert.equal("request_id" in out.json.error, false);
      } finally {
        hopServer.server.close();
        hopServer.server.closeAllConnections();
      }
    });
  } finally {
    upstream.server.close();
    upstream.server.closeAllConnections();
  }
});
