import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { anthropicSseToChat, anthropicToChat, chatToAnthropic, chatToResponses, mapUpstreamError, responsesSseToChat, responsesToChat, resolveApiType } from "./protocol-converters.ts";
const require = createRequire(import.meta.url);
const cjsConverters: any = require("../../payload/protocol-converters.cjs");

test("native modern OpenAI routes retain Responses despite legacy catalog settings", () => {
  for (const slug of ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.3-codex", "gpt-5.6-sol"]) {
    const model = { slug, apiType: "chat-completions" };
    const provider = { origin: "https://api.openai.com/v1", apiType: "chat-completions" };
    assert.equal(resolveApiType(model, provider), "responses");
    assert.equal(cjsConverters.resolveApiType(model, provider), "responses");
  }
});

test("protocol resolution preserves independent provider and legacy model choices", () => {
  for (const origin of ["https://opencode.ai/zen/go/v1", "https://api.openai.com.example/v1", "http://api.openai.com/v1", "invalid"]) {
    const model = { slug: "gpt-6.1-sol", apiType: "chat-completions" };
    const provider = { origin, apiType: "responses" };
    assert.equal(resolveApiType(model, provider), "chat-completions");
    assert.equal(cjsConverters.resolveApiType(model, provider), "chat-completions");
    assert.equal(resolveApiType(model, provider, true), "responses");
    assert.equal(cjsConverters.resolveApiType(model, provider, true), "responses");
  }
  assert.equal(resolveApiType({ slug: "gpt-4o" }, { origin: "https://api.openai.com/v1" }), "chat-completions");
});

test("Responses converts named choices, optional schemas, and assistant replay with parity", () => {
  const body = {
    tool_choice: { type: "function", function: { name: "lookup" } },
    tools: [{ type: "function", function: { name: "lookup", parameters: { type: "object", properties: { optional: { type: "string" } } } } }],
    messages: [{ role: "assistant", content: [{ type: "text", text: "prior answer" }] }, { role: "user", content: [{ type: "text", text: "next request" }] }],
  };
  const converted = chatToResponses(body);
  assert.deepEqual(converted.tool_choice, { type: "function", name: "lookup" });
  assert.equal((converted.tools as any[])[0].strict, false);
  assert.equal((converted.input as any[])[0].content[0].type, "output_text");
  assert.equal((converted.input as any[])[1].content[0].type, "input_text");
  assert.deepEqual(cjsConverters.chatToResponses(body), converted);
  const explicit = chatToResponses({ tools: [{ type: "function", function: { name: "strict", strict: true } }] });
  assert.equal((explicit.tools as any[])[0].strict, true);
});

const request = {
  model: "model",
  stream: true,
  max_tokens: 90000,
  messages: [
    { role: "system", content: "Be concise" },
    { role: "user", content: "call the tool" },
    { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "lookup", arguments: "{\"q\":\"x\"}" } }] },
    { role: "tool", tool_call_id: "call_1", content: "done" },
  ],
  tools: [{ type: "function", function: { name: "lookup", description: "Find it", parameters: { type: "object", properties: { q: { type: "string" } } } } }],
};

test("bot reasoning request controls retain TS/CJS parity across Responses and Anthropic", () => {
  for (const effort of ["none", "low", "high", "xhigh", "max"]) {
    const input = { ...request, reasoning_effort: effort };
    const responses = chatToResponses(input);
    assert.deepEqual(responses.reasoning, { effort });
    assert.deepEqual(cjsConverters.chatToResponses(input), responses);
    const anthropic = chatToAnthropic(input);
    if (effort !== "none") {
      assert.deepEqual(anthropic.output_config, { effort });
      assert.deepEqual(anthropic.thinking, { type: "adaptive" });
    }
    assert.deepEqual(cjsConverters.chatToAnthropic(input), anthropic);
  }
  const off = { ...request, thinking: { type: "disabled" }, output_config: { format: "fixture" } };
  assert.deepEqual(chatToAnthropic(off).thinking, { type: "disabled" });
  assert.deepEqual(chatToAnthropic(off).output_config, { format: "fixture" });
  assert.deepEqual(cjsConverters.chatToAnthropic(off), chatToAnthropic(off));
});

test("Responses request and JSON response map tools, reasoning, finish, and usage", () => {
  const body = chatToResponses(request);
  assert.equal(body.max_output_tokens, 90000);
  assert.equal((body.input as { type: string }[])[3]?.type, "function_call");
  assert.equal((body.input as { type: string }[])[4]?.type, "function_call_output");
  const result = responsesToChat({ id: "resp_1", model: "model", status: "completed", output: [{ type: "reasoning", summary: [{ type: "summary_text", text: "think" }] }, { type: "message", content: [{ type: "output_text", text: "answer" }] }, { type: "function_call", call_id: "call_2", name: "lookup", arguments: "{}" }], usage: { input_tokens: 3, output_tokens: 4, input_tokens_details: { cached_tokens: 1 }, output_tokens_details: { reasoning_tokens: 2 } } }) as any;
  assert.equal(result.choices?.[0]?.finish_reason, "tool_calls");
  assert.equal(result.choices?.[0]?.message?.reasoning_content, "think");
  assert.equal(result.usage?.prompt_tokens, 3);
  assert.equal(result.usage?.completion_tokens_details?.reasoning_tokens, 2);
});

test("Responses honors the clamped max_output_tokens field", () => {
  const body = chatToResponses({ max_tokens: 90000, max_output_tokens: 65536 });
  assert.equal(body.max_output_tokens, 65536);
});

test("Responses content_filter wins over incomplete status", () => {
  const result = responsesToChat({ status: "incomplete", incomplete_details: { reason: "content_filter" } }) as any;
  assert.equal(result.choices?.[0]?.finish_reason, "content_filter");
});

test("UTF-8 tool-call ids are clamped identically in TS and CJS", () => {
  const id = "工具调用".repeat(30);
  const ts = chatToResponses({ messages: [{ role: "assistant", tool_calls: [{ id, function: { name: "x", arguments: "{}" } }] }] });
  const cjs = cjsConverters.chatToResponses({ messages: [{ role: "assistant", tool_calls: [{ id, function: { name: "x", arguments: "{}" } }] }] });
  const tsId = (ts.input as any[])[1].call_id;
  const cjsId = cjs.input[1].call_id;
  assert.equal(tsId, cjsId);
  assert.ok(Buffer.byteLength(tsId, "utf8") <= 64);
});

test("Responses SSE maps text, reasoning, tool deltas, and completion usage", () => {
  const raw = [
    `data: ${JSON.stringify({ type: "response.created", response: { id: "r", model: "m" } })}`,
    `data: ${JSON.stringify({ type: "response.output_text.delta", delta: "hi" })}`,
    `data: ${JSON.stringify({ type: "response.output_item.added", output_index: 0, item: { type: "function_call", id: "fc", name: "lookup" } })}`,
    `data: ${JSON.stringify({ type: "response.function_call_arguments.delta", output_index: 0, delta: "{}" })}`,
    `data: ${JSON.stringify({ type: "response.completed", response: { usage: { input_tokens: 1, output_tokens: 2 }, output: [] } })}`,
    "data: [DONE]", "",
  ].join("\n\n");
  const text = responsesSseToChat(raw);
  assert.match(text, /\"content\":\"hi\"/);
  assert.match(text, /\"tool_calls\"/);
  assert.match(text, /\"finish_reason\":\"stop\"/);
  assert.match(text, /data: \[DONE\]/);
});

test("Anthropic request and JSON response map system, tool blocks, thinking, and cache usage", () => {
  const body = chatToAnthropic(request);
  assert.equal(body.system, "Be concise");
  assert.equal(body.max_tokens, 90000);
  assert.equal((body.messages as { role: string }[])[1]?.role, "assistant");
  const result = anthropicToChat({ id: "msg_1", model: "m", content: [{ type: "thinking", thinking: "private" }, { type: "text", text: "answer" }, { type: "tool_use", id: "toolu_1", name: "lookup", input: { q: "x" } }], stop_reason: "tool_use", usage: { input_tokens: 2, cache_read_input_tokens: 3, cache_creation_input_tokens: 4, output_tokens: 4 } }) as any;
  assert.equal(result.choices?.[0]?.finish_reason, "tool_calls");
  assert.equal(result.choices?.[0]?.message?.content, "answer");
  assert.equal(result.usage?.prompt_tokens, 9);
  assert.equal(result.usage?.prompt_tokens_details?.cached_tokens, 7);
  const thinking = (body.messages as any[])[1].content.find((part: any) => part.type === "thinking");
  assert.equal(thinking, undefined);
  const thinkingRequest = chatToAnthropic({ messages: [{ role: "assistant", reasoning_content: "private", content: "answer" }] });
  assert.deepEqual((thinkingRequest.messages as any[])[0].content[0], { type: "thinking", thinking: "private" });
});

test("Anthropic SSE maps text, thinking, tool JSON, stop reason, and usage", () => {
  const raw = [
    `event: message_start\ndata: ${JSON.stringify({ type: "message_start", message: { id: "msg_1", model: "m" } })}`,
    `event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "hi" } })}`,
    `event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: 1, delta: { type: "thinking_delta", thinking: "why" } })}`,
    `event: content_block_delta\ndata: ${JSON.stringify({ type: "content_block_delta", index: 2, delta: { type: "input_json_delta", partial_json: "{\"q\":\"x\"}" } })}`,
    `event: message_delta\ndata: ${JSON.stringify({ type: "message_delta", delta: { stop_reason: "end_turn" }, usage: { input_tokens: 1, output_tokens: 2 } })}`,
    "event: message_stop\ndata: {\"type\":\"message_stop\"}", "",
  ].join("\n\n");
  const text = anthropicSseToChat(raw);
  assert.match(text, /\"content\":\"hi\"/);
  assert.match(text, /\"reasoning_content\":\"why\"/);
  assert.match(text, /\"tool_calls\"/);
  assert.match(text, /\"finish_reason\":\"stop\"/);
  assert.match(text, /\"prompt_tokens\":1/);
});

test("SSE parser accepts consecutive data lines without blank separators", () => {
  const raw = `data: ${JSON.stringify({ type: "response.created", response: { id: "r", model: "m" } })}\ndata: ${JSON.stringify({ type: "response.output_text.delta", delta: "ok" })}\ndata: [DONE]\n`;
  assert.match(responsesSseToChat(raw), /\"content\":\"ok\"/);
});

test("mapUpstreamError preserves status and request id identically in TS and CJS", () => {
  const responses = { error: { message: "rate limited", code: "rate_limit_exceeded" }, request_id: "req_429" };
  const ts = mapUpstreamError(responses, 429) as any;
  const cjs = cjsConverters.mapUpstreamError(responses, 429);
  assert.deepEqual(ts, cjs);
  assert.equal(ts.error.message, "rate limited");
  assert.equal(ts.error.code, "rate_limit_exceeded");
  assert.equal(ts.error.upstream_status, 429);
  assert.equal(ts.error.request_id, "req_429");
  const anthropic = { type: "error", error: { type: "api_error", message: "boom" } };
  const tsAnthropic = mapUpstreamError(anthropic, 500) as any;
  const cjsAnthropic = cjsConverters.mapUpstreamError(anthropic, 500);
  assert.deepEqual(tsAnthropic, cjsAnthropic);
  assert.equal(tsAnthropic.error.code, "api_error");
  assert.equal(tsAnthropic.error.upstream_status, 500);
  assert.equal("request_id" in tsAnthropic.error, false);
});
