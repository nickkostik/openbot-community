import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { normalizeProviderCredential } from "../supervisor/credential-import.ts";

const require = createRequire(import.meta.url);
const https = require("node:https");
const hop = require("../../payload/hop-handler.cjs");

async function listen(handler: http.RequestListener) {
  const server = http.createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return { server, port: address.port };
}

for (const mode of ["json", "stream", "oauth"] as const) {
  test(`native OpenAI ${mode} hop keeps tools, selected effort, and credential headers`, async (t) => {
    const captured: { url: string; headers: http.IncomingHttpHeaders; body: any }[] = [];
    const upstream = await listen((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        const body = JSON.parse(Buffer.concat(chunks).toString());
        captured.push({ url: req.url!, headers: req.headers, body });
        if (mode === "stream") {
          res.writeHead(200, { "content-type": "text/event-stream" });
          for (const event of [
            { type: "response.created", response: { id: "resp_fixture", model: "gpt-6.1-sol" } },
            { type: "response.output_text.delta", delta: "PASS" },
            { type: "response.completed", response: { status: "completed", output: [], usage: { input_tokens: 12, output_tokens: 1, input_tokens_details: { cached_tokens: 8 } } } },
          ]) res.write(`data: ${JSON.stringify(event)}\n\n`);
          res.end();
        } else {
          res.writeHead(200, { "content-type": "application/json" });
          res.end(JSON.stringify({ id: "resp_fixture", model: "gpt-6.1-sol", status: "completed", output: captured.length === 1
            ? [{ type: "function_call", call_id: "call_fixture", name: "lookup", arguments: "{}" }]
            : [{ type: "message", content: [{ type: "output_text", text: "PASS" }] }] }));
        }
      });
    });
    t.after(() => upstream.server.close());
    // Intercept only the selected official endpoint. No real network/key is
    // used; the request still traverses the production protocol/header path.
    t.mock.method(https, "request", (options: any, callback: any) => {
      assert.equal(options.hostname, mode === "oauth" ? "chatgpt.com" : "api.openai.com");
      return http.request({ ...options, protocol: "http:", hostname: "127.0.0.1", port: upstream.port }, callback);
    });
    const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-openai-route-"));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const env = { OPENBOT_SAND_DATA: dir, OPENBOT_PLAN: path.join(dir, "plan.json"), OPENBOT_SECRETS: path.join(dir, "secrets.json"), OPENBOT_LOGS: path.join(dir, "logs.json") };
    const prior = Object.fromEntries(Object.keys(env).map((key) => [key, process.env[key]]));
    Object.assign(process.env, env);
    t.after(() => { for (const [key, value] of Object.entries(prior)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
    writeFileSync(env.OPENBOT_PLAN, JSON.stringify({ kind: "custom", agents: {}, catalog: {
      providers: [{ id: "openai", name: "OpenAI", origin: "https://api.openai.com/v1", apiType: "chat-completions" }],
      models: [{ id: "openai:gpt-6.1-sol", providerId: "openai", slug: "gpt-6.1-sol", apiType: "chat-completions", reasoningLevels: ["default", "high"], activeReasoning: "high", parameters: [] }], bindings: [],
    } }));
    const jwt = (claims: unknown) => `header.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.signature`;
    const oauthAccess = jwt({ exp: Math.floor(Date.now()/1000)+3600, "https://api.openai.com/auth": { chatgpt_account_id: "fixture-account" } });
    const secret = mode === "oauth" ? normalizeProviderCredential(JSON.stringify({ tokens: {
      access_token: oauthAccess, refresh_token: "fixture-refresh", id_token: jwt({ "https://api.openai.com/auth": {} }),
    } }), "https://api.openai.com/v1") : "sk-fixture";
    if (mode === "oauth") assert.equal(JSON.parse(secret).chatgptAccountId, "fixture-account");
    writeFileSync(env.OPENBOT_SECRETS, JSON.stringify({ providers: { openai: secret } }));
    const service = await listen((req, res) => { void hop.handleHopRequest(req, res); });
    t.after(() => service.server.close());
    const request: any = { model: "openai:gpt-6.1-sol", stream: mode === "stream", max_tokens: 2048,
      conversationId: "official-route-fixture",
      messages: [{ role: "user", content: "Call lookup" }],
      tools: [{ type: "function", function: { name: "lookup", parameters: { type: "object", properties: { optional: { type: "string" } } } } }],
      tool_choice: { type: "function", function: { name: "lookup" } },
    };
    async function send() {
      return fetch(`http://127.0.0.1:${service.port}/v1/chat/completions`, { method: "POST", headers: { "content-type": "application/json", "user-agent": "fixture-client" }, body: JSON.stringify(request) });
    }
    const response = await send();
    assert.equal(response.status, 200);
    if (mode === "stream") {
      const stream = await response.text();
      assert.match(stream, /"content":"PASS"/);
      assert.match(stream, /"cached_tokens":8/);
      assert.match(stream, /data: \[DONE\]/);
    } else {
      const first: any = await response.json();
      assert.equal(first.choices[0].finish_reason, "tool_calls");
      assert.equal(first.choices[0].message.tool_calls[0].id, "call_fixture");
      request.messages.push(first.choices[0].message, { role: "tool", tool_call_id: "call_fixture", content: "result" });
      request.tool_choice = "none";
      const second = await send();
      assert.equal(second.status, 200);
      assert.equal((await second.json() as any).choices[0].message.content, "PASS");
      const continuation = captured[1];
      assert.ok(continuation);
      assert.ok(continuation.body.input.some((item: any) => item.type === "function_call_output" && item.call_id === "call_fixture"));
    }
    const outbound = captured[0];
    assert.ok(outbound);
    assert.equal(outbound.url, mode === "oauth" ? "/backend-api/codex/responses" : "/v1/responses");
    assert.deepEqual(outbound.body.reasoning, { effort: "high" });
    assert.equal(outbound.body.max_output_tokens, mode === "oauth" ? undefined : 2048);
    assert.equal(outbound.body.max_tokens, undefined);
    assert.equal(outbound.body.tools[0].strict, false);
    assert.deepEqual(outbound.body.tool_choice, { type: "function", name: "lookup" });
    assert.equal(outbound.headers.authorization, mode === "oauth" ? "Bearer " + oauthAccess : "Bearer sk-fixture");
    if (mode === "oauth") {
      assert.equal(outbound.body.store, false);
      assert.equal(outbound.body.stream, true);
      assert.equal(outbound.body.instructions, "");
      assert.equal(outbound.headers["chatgpt-account-id"], "fixture-account");
      assert.equal(outbound.headers.originator, "codex-tui");
      assert.equal(outbound.headers["session-id"], outbound.body.prompt_cache_key);
    } else {
      assert.equal(outbound.headers["user-agent"], "fixture-client");
      assert.equal(outbound.headers["session-id"], undefined, "API keys do not receive subscription identity headers");
    }
  });
}
