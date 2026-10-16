import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const wire = require("../../payload/image-read.cjs") as {
  MAX_REQUEST_WIRE_BYTES: number;
  enforceImageBudget: (messages: unknown[], options?: { budget?: number; extraWireBytes?: number; convert?: unknown }) => Promise<unknown[]>;
};
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value));

test("long text history is budgeted with linear serialization work and preserves the new turn", async () => {
  let serializations = 0;
  const system = { role: "system", content: "Keep these instructions." };
  const current = { role: "user", content: "Keep my complete new request." };
  const history = Array.from({ length: 1600 }, () => ({
    role: "assistant", content: "x".repeat(12000),
    toJSON() { serializations++; return { role: this.role, content: this.content }; },
  }));
  const messages = [system, ...history, current];
  const out = await wire.enforceImageBudget(messages, { convert: null, extraWireBytes: 10000 });
  assert.equal(out, messages);
  assert.ok(serializations <= history.length * 2, `repeated full-history serialization: ${serializations}`);
  assert.equal(out[0], system);
  assert.equal(out.at(-1), current);
  assert.ok(out.length < history.length);
  assert.ok(bytes(out) + 10000 <= wire.MAX_REQUEST_WIRE_BYTES);
});

test("historical tool calls and all paired results are removed as one block", async () => {
  const system = { role: "developer", content: "Keep developer instructions." };
  const tail = [
    { role: "user", content: "Current question" },
    { role: "assistant", tool_calls: [{ id: "current", type: "function", function: { name: "read", arguments: "{}" } }] },
    { role: "tool", tool_call_id: "current", content: "Current result" },
    { role: "user", content: [{ type: "text", text: "[Image attached from Read: example.png]" }] },
  ];
  const messages = [system,
    { role: "assistant", tool_calls: ["old-a", "old-b"].map(id => ({ id, type: "function", function: { name: "read", arguments: "{}" } })) },
    { role: "tool", tool_call_id: "old-a", content: "x".repeat(10000) },
    { role: "tool", tool_call_id: "old-b", content: "x".repeat(10000) },
    ...tail,
  ];
  await wire.enforceImageBudget(messages, { convert: null, budget: bytes([system, ...tail]) });
  assert.deepEqual(messages, [system, ...tail]);
});

test("oversized current text is rejected explicitly without silently rewriting the question", async () => {
  const messages = [{ role: "system", content: "Instructions" }, { role: "user", content: "🙂".repeat(2000) }];
  const before = structuredClone(messages);
  await assert.rejects(wire.enforceImageBudget(messages, { convert: null, budget: 1024 }), (error: unknown) => {
    const e = error as Error & { httpStatus: number; upstreamError: { error: { code: string } } };
    assert.equal(e.httpStatus, 413);
    assert.equal(e.upstreamError.error.code, "openbot_wire_budget_exceeded");
    assert.match(e.message, /wire budget/);
    return true;
  });
  assert.deepEqual(messages, before);
});

test("oversized current content parts are preserved on rejection", async () => {
  const messages = [{ role: "user", content: [{ type: "text", text: "a".repeat(10000) }, { type: "text", text: "latest instruction" }] }];
  const before = structuredClone(messages);
  await assert.rejects(wire.enforceImageBudget(messages, { convert: null, budget: 2048 }), /wire budget/);
  assert.deepEqual(messages, before);
});

test("an envelope that exhausts the full wire budget cannot be hidden by a minimum message allowance", async () => {
  await assert.rejects(wire.enforceImageBudget([{ role: "user", content: "hi" }], {
    budget: 100, extraWireBytes: 120, convert: null,
  }), { httpStatus: 413 });
});

test("under-budget text remains byte-for-byte unchanged for prefix caching", async () => {
  const messages = [{ role: "system", content: "Stable prefix" }, { role: "user", content: "Hello" }];
  const before = JSON.stringify(messages);
  await wire.enforceImageBudget(messages, { convert: null, budget: bytes(messages) });
  assert.equal(JSON.stringify(messages), before);
});
