import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { chatToResponses, prepareCodexResponses, responsesSseToJson, responsesSseToChat, mapUpstreamError } from "./protocol-converters.ts";

const require = createRequire(import.meta.url);
const https = require("node:https");
const hop = require("../../payload/hop-handler.cjs");
const converters = require("../../payload/protocol-converters.cjs");
const sse = (event: unknown) => `data: ${JSON.stringify(event)}\n\n`;

test("native Responses input survives empty chat messages used by compaction", () => {
  const input = [{ role: "user", content: "Compact this existing history" }];
  for (const messages of [undefined, []]) {
    const body = { model: "gpt-6.1-sol", input, messages };
    assert.deepEqual(chatToResponses(body).input, input);
    assert.deepEqual(converters.chatToResponses(body), chatToResponses(body));
  }
  for (const body of [{ previous_response_id: "resp_previous" }, { prompt: { id: "prompt_existing" } }, { conversation: "conv_existing" }]) {
    assert.equal(chatToResponses(body).input, undefined);
    assert.deepEqual(converters.chatToResponses(body), chatToResponses(body));
  }
  const chatWins = { messages: [{ role: "user", content: "Latest message" }], input };
  assert.deepEqual(chatToResponses(chatWins).input, [{ role: "user", content: "Latest message" }]);
});

test("Codex wire normalization preserves history, tools, cache scope and selected effort", () => {
  const input = chatToResponses({ model: "gpt-6.1-sol", stream: false, max_tokens: 65536, temperature: 0.7,
    messages: [{ role: "system", content: "Keep all instructions" }, { role: "user", content: "[SAND_HIDDEN_PROMPT]" }],
    reasoning_effort: "max", tools: [{ type: "function", function: { name: "reply", parameters: { type: "object" } } }],
  });
  input.prompt_cache_key = "stable-bot-scope";
  input.instructions = "Explicit instructions";
  const normalized = prepareCodexResponses(input);
  assert.deepEqual(converters.prepareCodexResponses(input), normalized);
  assert.equal(input.max_output_tokens, 65536);
  assert.equal(input.stream, false);
  assert.equal(normalized.stream, true);
  assert.equal(normalized.store, false);
  assert.equal(normalized.instructions, "Explicit instructions");
  assert.equal(normalized.max_output_tokens, undefined);
  assert.equal(normalized.temperature, undefined);
  assert.deepEqual(normalized.input, input.input);
  assert.deepEqual(normalized.tools, input.tools);
  assert.deepEqual(normalized.reasoning, { effort: "max" });
  assert.equal(normalized.prompt_cache_key, "stable-bot-scope");
});

test("buffered Codex responses retain final tools and cache usage; failures never become empty success", () => {
  const terminal = { id: "resp", created_at: 1791500000, model: "gpt-6.1-sol", status: "completed", output: [
    { type: "function_call", call_id: "call_1", name: "reply", arguments: "{}" },
  ], usage: { input_tokens: 100, output_tokens: 10, input_tokens_details: { cached_tokens: 90 } } };
  const complete = sse({ type: "response.completed", response: terminal });
  const result: any = responsesSseToJson(complete);
  assert.deepEqual(converters.responsesSseToJson(complete), result);
  assert.equal(result.choices[0].finish_reason, "tool_calls");
  assert.equal(result.choices[0].message.tool_calls[0].id, "call_1");
  assert.equal(result.usage.prompt_tokens_details.cached_tokens, 90);
  const itemOnly = sse({ type: "response.output_item.done", output_index: 0, item: terminal.output[0] })
    + sse({ type: "response.completed", response: { ...terminal, output: [] } });
  assert.deepEqual(responsesSseToJson(itemOnly), result);
  assert.deepEqual(converters.responsesSseToJson(itemOnly), result);
  for (const raw of [sse({ type: "response.created", response: {} }),
    sse({ type: "response.failed", response: { error: { message: "subscription exhausted", code: "usage_limit_reached" } } }),
    sse({ type: "error", message: "subscription exhausted", code: "usage_limit_reached" }),
  ]) {
    assert.ok(responsesSseToJson(raw).error);
    assert.deepEqual(converters.responsesSseToJson(raw), responsesSseToJson(raw));
  }
  const failed = sse({ type: "response.failed", response: { error: { message: "subscription exhausted", code: "usage_limit_reached" } } });
  assert.match(responsesSseToChat(failed), /subscription exhausted/);
  assert.equal(converters.responsesSseToChat(failed), responsesSseToChat(failed));
  assert.deepEqual(mapUpstreamError({ detail: "Store must be set to false" }, 400), converters.mapUpstreamError({ detail: "Store must be set to false" }, 400));
  assert.equal((mapUpstreamError({ detail: "Store must be set to false" }, 400).error as any).message, "Store must be set to false");
});

for (const providerId of ["openai", "openai-2"]) for (const mode of ["tools", "stream", "failed", "truncated"] as const) {
  test(`${providerId} subscription ${mode} traverses the production hop with the Codex endpoint contract`, async (t) => {
    const sessionKeys: (string | undefined)[] = [];
    const upstream = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", chunk => chunks.push(chunk));
      req.on("end", () => {
        const body = JSON.parse(Buffer.concat(chunks).toString());
        assert.equal(body.stream, true);
        assert.equal(body.store, false);
        assert.equal(body.instructions, "");
        assert.equal(body.max_output_tokens, undefined);
        assert.deepEqual(body.reasoning, { effort: "high" });
        assert.equal(req.headers.authorization, "Bearer fixture-token");
        assert.equal(req.headers["chatgpt-account-id"], "fixture-account");
        if (body.prompt_cache_key) {
          assert.match(body.prompt_cache_key, /^[a-f0-9]{40}$/);
          assert.equal(req.headers["session-id"], body.prompt_cache_key);
        } else assert.equal(req.headers["session-id"], undefined);
        assert.notEqual(req.headers["session-id"], "caller-session");
        assert.equal(req.headers["thread-id"], undefined);
        assert.equal(req.headers["x-codex-turn-state"], undefined);
        assert.equal(body.conversationId, undefined);
        assert.equal(body.openbot_internal_auxiliary, undefined, "internal auxiliary metadata never reaches the provider");
        sessionKeys.push(req.headers["session-id"] as string | undefined);
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.write(sse({ type: "response.created", response: { id: "resp", model: body.model } }));
        if (mode === "failed") res.end(sse({ type: "response.failed", response: { error: { message: "subscription exhausted", code: "usage_limit_reached" } } }));
        else if (mode === "truncated") res.end();
        else {
          const followup = body.input.some((item: any) => item.type === "function_call_output" && item.call_id === "call_fixture");
          const output = mode === "tools" && !followup
            ? [{ type: "function_call", call_id: "call_fixture", name: "lookup", arguments: "{}" }]
            : [{ type: "message", content: [{ type: "output_text", text: "PASS" }] }];
          if (mode === "stream") res.write(sse({ type: "response.output_text.delta", delta: "PASS" }));
          for (const [output_index, item] of output.entries()) res.write(sse({ type: "response.output_item.done", output_index, item }));
          res.end(sse({ type: "response.completed", response: { id: "resp", model: body.model, status: "completed", output: [],
            usage: { input_tokens: 100, output_tokens: 1, input_tokens_details: { cached_tokens: 90 } } } }));
        }
      });
    });
    await new Promise<void>(resolve => upstream.listen(0, "127.0.0.1", resolve));
    t.after(() => upstream.close());
    const address = upstream.address(); assert.ok(address && typeof address !== "string");
    t.mock.method(https, "request", (options: any, callback: any) => {
      assert.equal(options.hostname, "chatgpt.com");
      assert.equal(options.path, "/backend-api/codex/responses");
      return http.request({ ...options, protocol: "http:", hostname: "127.0.0.1", port: address.port }, callback);
    });
    const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-codex-compat-"));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const env = { OPENBOT_SAND_DATA: dir, OPENBOT_PLAN: path.join(dir, "plan.json"), OPENBOT_SECRETS: path.join(dir, "secrets.json") };
    const previous = Object.fromEntries(Object.keys(env).map(key => [key, process.env[key]]));
    Object.assign(process.env, env);
    t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
    writeFileSync(env.OPENBOT_PLAN, JSON.stringify({ kind: "custom", agents: {}, catalog: {
      providers: [{ id: providerId, name: "OpenAI", origin: mode === "stream" ? "https://legacy-origin.invalid/v1" : "https://api.openai.com/v1" }],
      models: [{ id: `${providerId}:gpt-6.1-sol`, providerId, slug: "gpt-6.1-sol", reasoningLevels: ["default", "high"], activeReasoning: "high", parameters: [] }], bindings: [],
    } }));
    writeFileSync(env.OPENBOT_SECRETS, JSON.stringify({ providers: { [providerId]: JSON.stringify({ kind: "openai-oauth", accessToken: "fixture-token", refreshToken: "fixture-refresh", expiresAt: Date.now()/1000 + 3600, chatgptAccountId: "fixture-account" }) } }));
    const service = http.createServer((req, res) => { void hop.handleHopRequest(req, res); });
    await new Promise<void>(resolve => service.listen(0, "127.0.0.1", resolve));
    t.after(() => service.close());
    const local = service.address(); assert.ok(local && typeof local !== "string");
    const request: any = { model: `${providerId}:gpt-6.1-sol`, stream: mode === "stream", max_tokens: 65536, openbot_internal_model: true,
      conversationId: mode + ":fixture-chat", prompt_cache_key: "caller-key",
      messages: [{ role: "user", content: "Verify route" }], tools: [{ type: "function", function: { name: "lookup", parameters: { type: "object" } } }],
    };
    const send = () => fetch(`http://127.0.0.1:${local.port}/v1/chat/completions`, { method: "POST", headers: { "content-type": "application/json", "session-id": "caller-session", "thread-id": "caller-thread", "x-codex-turn-state": "caller-turn" }, body: JSON.stringify(request) });
    const response = await send();
    assert.equal(response.status, mode === "failed" || mode === "truncated" ? 502 : 200);
    if (mode === "stream") assert.match(await response.text(), /"content":"PASS"/);
    else {
      assert.match(response.headers.get("content-type") ?? "", /application\/json/);
      const result: any = await response.json();
      if (mode === "failed" || mode === "truncated") assert.ok(result.error);
      else {
        assert.equal(result.choices[0].finish_reason, "tool_calls");
        request.messages.push(result.choices[0].message, { role: "tool", tool_call_id: "call_fixture", content: "verified" });
        const followup = await send();
        assert.equal(followup.status, 200);
        const reply: any = await followup.json();
        assert.equal(reply.choices[0].message.content, "PASS");
        assert.equal(reply.usage.prompt_tokens_details.cached_tokens, 90);
        assert.equal(sessionKeys[1], sessionKeys[0], "a tool continuation retains cache affinity");
        request.conversationId = "different-chat";
        const different = await send();
        assert.equal(different.status, 200);
        await different.text();
        assert.notEqual(sessionKeys[2], sessionKeys[0], "conversations do not share a routing identity");
        delete request.conversationId;
        delete request.prompt_cache_key;
        const stateless = await send();
        assert.equal(stateless.status, 200);
        await stateless.text();
        assert.equal(sessionKeys[3], undefined, "stateless calls never adopt client session headers");
        request.conversationId = mode + ":fixture-chat";
        request.openbot_internal_auxiliary = true;
        for (let i = 0; i < 2; i++) {
          const auxiliary = await send();
          assert.equal(auxiliary.status, 200);
          await auxiliary.text();
        }
        assert.notEqual(sessionKeys[4], sessionKeys[0], "helpers do not replace the parent chat's routing affinity");
        assert.equal(sessionKeys[5], sessionKeys[4], "helper affinity remains stable for repeated calls");
        request.openbot_internal_auxiliary = "true";
        const parent = await send();
        assert.equal(parent.status, 200);
        await parent.text();
        assert.equal(sessionKeys[6], sessionKeys[0], "only the internal boolean separates helper affinity");
      }
    }
  });
}
