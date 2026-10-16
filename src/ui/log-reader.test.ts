import assert from "node:assert/strict";
import test from "node:test";
import { appendFileSync, mkdtempSync, renameSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createLogReader } from "./log-reader.ts";

test("CPU-heavy log queries don't stop the service loop; duplicate polls share bounded work", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-log-worker-"));
  const payload = path.join(dir, "slow.cjs");
  writeFileSync(payload, `let calls=0;exports.invalidateReadAggregates=()=>{};exports.usageNow=()=>{const until=Date.now()+250;while(Date.now()<until){};return {calls:++calls};};`);
  const reader = createLogReader({ sandData: dir, payloadPath: payload, maxPending: 1 });
  let ticks = 0;
  const interval = setInterval(() => ticks++, 20);
  try {
    const first = reader.read<{ calls: number }>("usageNow", { from: "now" });
    const same = reader.read<{ calls: number }>("usageNow", { from: "now" });
    await assert.rejects(reader.read("statsNow"), /busy/);
    const [a, b] = await Promise.all([first, same]);
    assert.equal(a.calls, 1); assert.deepEqual(a, b);
    assert.ok(ticks >= 3, `service loop was stalled: ${ticks} ticks`);
  } finally { clearInterval(interval); await reader.close(); rmSync(dir, { recursive: true, force: true }); }
});

test("read-only log worker sees external appends, partial lines, clear and rotation without duplicate usage", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-log-revisions-"));
  const file = path.join(dir, "openbot-requests.jsonl");
  const row = (id: string) => JSON.stringify({ id, channel: "hop", startedAt: "2026-10-07T00:00:00Z", ok: true, model: "model", promptTokens: 100, cachedTokens: 50 });
  writeFileSync(file, row("first-request") + "\n");
  const reader = createLogReader({ sandData: dir });
  try {
    assert.equal((await reader.read<{ total: number }>("listRequests")).total, 1);
    appendFileSync(file, row("second-request") + "\n" + row("first-request") + "\n");
    assert.equal((await reader.read<{ total: number }>("listRequests")).total, 2);
    appendFileSync(file, '{"id":"incomplete');
    assert.equal((await reader.read<{ total: number }>("listRequests")).total, 2);
    appendFileSync(file, '-request"}\n');
    assert.equal((await reader.read<{ total: number }>("listRequests")).total, 3);
    writeFileSync(file + ".new", row("rotated-request") + "\n"); renameSync(file + ".new", file);
    const rotated = await reader.read<{ items: { id: string }[] }>("listRequests");
    assert.equal(rotated.items[0]?.id, "rotated-request");
    writeFileSync(file, ""); assert.equal((await reader.read<{ total: number }>("listRequests")).total, 0);
  } finally { await reader.close(); rmSync(dir, { recursive: true, force: true }); }
});

test("worker failure rejects its job and a fresh worker can retry", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-log-restart-"));
  const payload = path.join(dir, "fail.cjs");
  writeFileSync(payload, "throw new Error('private-token-must-not-leak');");
  const reader = createLogReader({ sandData: dir, payloadPath: payload });
  try {
    await assert.rejects(reader.read("statsNow"), /^Error: Log worker stopped$/);
    writeFileSync(payload, "exports.invalidateReadAggregates=()=>{};exports.statsNow=()=>({ok:true});");
    assert.deepEqual(await reader.read("statsNow"), { ok: true });
  } finally { await reader.close(); rmSync(dir, { recursive: true, force: true }); }
});
