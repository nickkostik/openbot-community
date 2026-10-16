import assert from "node:assert/strict";
import test from "node:test";
import {
  PAIR_WINDOW_MS,
  findPairById,
  pairChannels,
  pairContainsId,
  pairError,
  pairIds,
  pairKey,
  pairLatency,
  pairLogRows,
  pairModel,
  pairOpenId,
  pairStartedAt,
  pairStatus,
  pairStream,
  pairTokens,
  type PairableLog,
} from "./pair-logs.ts";

function row(partial: Partial<PairableLog> & Pick<PairableLog, "id" | "startedAt">): PairableLog {
  return {
    status: 200,
    ok: true,
    ...partial,
  };
}

test("pairLogRows joins hop and custom-host for one custom turn", () => {
  const hop = row({
    id: "hop-1",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.080Z",
    latencyMs: 1200,
    totalTokens: 400,
    stream: true,
  });
  const harness = row({
    id: "host-1",
    channel: "custom-host",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.010Z",
    latencyMs: 1300,
    stream: true,
  });
  const pairs = pairLogRows([hop, harness]);
  assert.equal(pairs.length, 1);
  const first = pairs[0];
  assert.equal(first?.kind, "pair");
  if (first?.kind !== "pair") return;
  assert.equal(first.hop.id, "hop-1");
  assert.equal(first.harness.id, "host-1");
  assert.deepEqual(pairChannels(first), ["hop", "custom-host"]);
  assert.equal(pairStartedAt(first), harness.startedAt);
  assert.equal(pairModel(first), "glm-5.3-flash");
  assert.equal(pairLatency(first), 1300);
  assert.equal(pairTokens(first), 400);
  assert.equal(pairStream(first), true);
  assert.equal(pairOpenId(first), "hop-1");
  assert.deepEqual(pairIds(first), ["hop-1", "host-1"]);
  assert.equal(pairKey(first), "hop-1+host-1");
  assert.equal(pairContainsId(first, "host-1"), true);
});

test("pairLogRows keeps official rows single", () => {
  const official = row({
    id: "off-1",
    channel: "official",
    model: "grok-4.5",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const hop = row({
    id: "hop-1",
    channel: "hop",
    model: "grok-4.5",
    startedAt: "2026-09-02T17:00:00.020Z",
  });
  const pairs = pairLogRows([official, hop]);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0]?.kind, "single");
  if (pairs[0]?.kind !== "single") return;
  assert.equal(pairs[0].record.id, "off-1");
  assert.equal(pairs[1]?.kind, "single");
});

test("pairLogRows treats missing channel as hop", () => {
  const hop = row({
    id: "hop-legacy",
    model: "kimi-k2",
    startedAt: "2026-09-02T17:00:00.040Z",
  });
  const harness = row({
    id: "host-legacy",
    channel: "custom-host",
    model: "kimi-k2",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const pairs = pairLogRows([hop, harness]);
  assert.equal(pairs[0]?.kind, "pair");
});

test("pairLogRows does not join different models", () => {
  const hop = row({
    id: "hop-1",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.040Z",
  });
  const harness = row({
    id: "host-1",
    channel: "custom-host",
    model: "deepseek-chat",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const pairs = pairLogRows([hop, harness]);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0]?.kind, "single");
  assert.equal(pairs[1]?.kind, "single");
});

test("pairLogRows does not join rows outside the time window", () => {
  const hop = row({
    id: "hop-1",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:20.000Z",
  });
  const harness = row({
    id: "host-1",
    channel: "custom-host",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const delta = Date.parse(hop.startedAt) - Date.parse(harness.startedAt);
  assert.ok(delta > PAIR_WINDOW_MS);
  const pairs = pairLogRows([hop, harness]);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0]?.kind, "single");
  assert.equal(pairs[1]?.kind, "single");
});

test("pairLogRows matches consecutive same-model turns to the nearest mate", () => {
  const host1 = row({
    id: "host-1",
    channel: "custom-host",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const hop1 = row({
    id: "hop-1",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.050Z",
  });
  const host2 = row({
    id: "host-2",
    channel: "custom-host",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.400Z",
  });
  const hop2 = row({
    id: "hop-2",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.450Z",
  });
  // API order is newest-first.
  const pairs = pairLogRows([hop2, host2, hop1, host1]);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0]?.kind, "pair");
  assert.equal(pairs[1]?.kind, "pair");
  if (pairs[0]?.kind !== "pair" || pairs[1]?.kind !== "pair") return;
  assert.equal(pairs[0].hop.id, "hop-2");
  assert.equal(pairs[0].harness.id, "host-2");
  assert.equal(pairs[1].hop.id, "hop-1");
  assert.equal(pairs[1].harness.id, "host-1");
});

test("pairLogRows leaves an unmatched hop or harness as a single row", () => {
  const hop = row({
    id: "hop-only",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const harness = row({
    id: "host-only",
    channel: "custom-host",
    model: "kimi-k2",
    startedAt: "2026-09-02T17:00:00.000Z",
  });
  const pairs = pairLogRows([hop, harness]);
  assert.equal(pairs.length, 2);
  assert.deepEqual(
    pairs.map((p) => (p.kind === "single" ? p.record.id : "")),
    ["hop-only", "host-only"],
  );
});

test("pairLogRows preserves input order and does not pair two hops", () => {
  const a = row({
    id: "hop-a",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:01.000Z",
  });
  const b = row({
    id: "hop-b",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:01.020Z",
  });
  const pairs = pairLogRows([b, a]);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0]?.kind, "single");
  assert.equal(pairs[1]?.kind, "single");
  if (pairs[0]?.kind !== "single" || pairs[1]?.kind !== "single") return;
  assert.equal(pairs[0].record.id, "hop-b");
  assert.equal(pairs[1].record.id, "hop-a");
});

test("pair helpers surface the failed layer's status and error", () => {
  const hop = row({
    id: "hop-1",
    channel: "hop",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.040Z",
    status: 502,
    ok: false,
    error: "upstream 502",
  });
  const harness = row({
    id: "host-1",
    channel: "custom-host",
    model: "glm-5.3-flash",
    startedAt: "2026-09-02T17:00:00.000Z",
    status: 200,
    ok: true,
  });
  const pairs = pairLogRows([hop, harness]);
  const first = pairs[0];
  assert.equal(first?.kind, "pair");
  if (!first) return;
  assert.equal(pairStatus(first), 502);
  assert.equal(pairError(first), "upstream 502");
  assert.equal(findPairById(pairs, "host-1")?.kind, "pair");
});

test("shared trace joins qualified model aliases and emits one pair when harness sorts first", () => {
  const hop = row({ id: "hop-trace", channel: "hop", requestId: "trace-one", providerId: "openai", model: "gpt-6.1-sol", startedAt: "2026-10-07T16:00:00Z", totalTokens: 100 });
  const harness = row({ id: "host-trace", channel: "custom-host", requestId: "trace-one", providerId: "openai", model: "openai:gpt-6.1-sol", startedAt: "2026-10-07T15:59:40Z", totalTokens: 100 });
  for (const order of [[hop, harness], [harness, hop]]) {
    const pairs = pairLogRows(order);
    assert.equal(pairs.length, 1);
    assert.equal(pairs[0]?.kind, "pair");
    assert.equal(pairTokens(pairs[0]!), 100);
  }
});

test("different request IDs never merge even when their timestamps and models match", () => {
  const rows = [row({ id: "hop", channel: "hop", requestId: "a", model: "model", startedAt: "2026-10-07T16:00:00Z" }), row({ id: "host", channel: "custom-host", requestId: "b", model: "model", startedAt: "2026-10-07T16:00:00Z" })];
  assert.equal(pairLogRows(rows).length, 2);
});

test("legacy aliases match only within the same known bot, provider and conversation", () => {
  const hop = row({ id: "hop", channel: "hop", model: "gpt-6.1-sol", providerId: "openai", botId: "Q", conversationId: "dm", startedAt: "2026-10-07T16:00:00Z" });
  const harness = row({ ...hop, id: "host", channel: "custom-host", model: "openai:gpt-6.1-sol" });
  assert.equal(pairLogRows([hop, harness]).length, 1);
  for (const override of [{ botId: "Factory" }, { conversationId: "room" }, { providerId: "other", model: "other:gpt-6.1-sol" }]) {
    assert.equal(pairLogRows([hop, { ...harness, ...override }]).length, 2);
  }
});

test("exact trace pairing is reserved before an older legacy hop can take its mate", () => {
  const rows = [
    row({ id: "legacy", channel: "hop", model: "model", startedAt: "2026-10-07T16:00:00Z" }),
    row({ id: "exact", channel: "hop", requestId: "trace", model: "model", startedAt: "2026-10-07T16:00:00.100Z" }),
    row({ id: "host", channel: "custom-host", requestId: "trace", model: "model", startedAt: "2026-10-07T16:00:00Z" }),
  ];
  const pairs = pairLogRows(rows);
  assert.equal(pairs.length, 2);
  assert.equal(findPairById(pairs, "exact")?.kind, "pair");
  assert.equal(findPairById(pairs, "legacy")?.kind, "single");
});

test("auxiliary calls and conflicting known bots cannot consume a chat harness row", () => {
  const host = row({ id: "host", channel: "custom-host", requestId: "trace", model: "model", botId: "Q", startedAt: "2026-10-07T16:00:00Z" });
  const auxiliary = row({ ...host, id: "aux", channel: "hop", purpose: "compaction" });
  const differentBot = row({ ...host, id: "other", channel: "hop", botId: "Factory" });
  assert.equal(pairLogRows([host, auxiliary]).length, 2);
  assert.equal(pairLogRows([host, differentBot]).length, 2);
});
