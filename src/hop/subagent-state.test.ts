import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const modulePath = require.resolve("../../payload/subagent-state.cjs");
interface SavedWorker {
  blobStore: {
    getBlob(ctx: unknown, id: Buffer): Promise<Buffer | undefined>;
    setBlob(ctx: unknown, id: Buffer, data: Buffer): Promise<void>;
    setBlobLocallyOnly(ctx: unknown, id: Buffer, data: Buffer): Promise<void>;
    setPersistenceAllowed(allowed: boolean): void;
  };
  saveCheckpoint(value: Buffer): void;
  loadCheckpoint(): Buffer | undefined;
}
const { openFor } = require(modulePath) as { openFor(parent: string, child: string, root: string): SavedWorker };
const parent = "00000000-0000-4000-8000-000000000001";
const child = "sand-subagent-00000000-0000-4000-8000-000000000002";
const sha = (value: Buffer) => createHash("sha256").update(value).digest();
const childDir = (root: string) => path.join(root, "openbot-subagent-state", parent, child);

test("a completed worker's checkpoint and prior tool results survive runner eviction and process restart", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-worker-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = openFor(parent, child, root);
  const result = Buffer.from("completed read-only tool result");
  const checkpoint = Buffer.from(JSON.stringify({ turns: [sha(result).toString("hex")], pendingToolCalls: [] }));
  assert.equal(first.loadCheckpoint(), undefined);
  // Blobs constructed before native privacy resolution are initially ephemeral.
  await first.blobStore.setBlob(undefined, sha(result), result);
  first.blobStore.setPersistenceAllowed(true);
  first.saveCheckpoint(checkpoint);
  const restarted = spawnSync(process.execPath, ["-e", `
    const s = require(process.argv[1]).openFor(process.argv[2], process.argv[3], process.argv[4]);
    const checkpoint = s.loadCheckpoint();
    s.blobStore.getBlob(null, Buffer.from(JSON.parse(checkpoint).turns[0], 'hex')).then(blob => {
      process.stdout.write(JSON.stringify({checkpoint: checkpoint.toString(), result: blob.toString()}));
    });`, modulePath, parent, child, root], { encoding: "utf8" });
  assert.equal(restarted.status, 0, restarted.stderr);
  assert.deepEqual(JSON.parse(restarted.stdout), { checkpoint: checkpoint.toString(), result: result.toString() });
  const second = openFor(parent, child, root);
  second.blobStore.setPersistenceAllowed(true);
  const continued = Buffer.from("checkpoint with completed tool retained and next read appended");
  second.saveCheckpoint(continued);
  assert.deepEqual(openFor(parent, child, root).loadCheckpoint(), continued);
  assert.equal(fs.statSync(path.join(childDir(root), "checkpoint.json")).mode & 0o777, 0o600);
});

test("NO_STORAGE keeps new blobs and checkpoints off disk and refuses a later empty-state resume", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-worker-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const store = openFor(parent, child, root);
  store.blobStore.setPersistenceAllowed(false);
  const privateData = Buffer.from("private turn that must not be recorded");
  await store.blobStore.setBlobLocallyOnly(undefined, sha(privateData), privateData);
  store.saveCheckpoint(privateData);
  assert.deepEqual(await store.blobStore.getBlob(undefined, sha(privateData)), privateData);
  assert.deepEqual(fs.readdirSync(path.join(childDir(root), "blobs")), []);
  assert.equal(fs.existsSync(path.join(childDir(root), "checkpoint.json")), false);
  assert.throws(() => openFor(parent, child, root).loadCheckpoint(), /not recorded/);
  store.blobStore.setPersistenceAllowed(true);
  store.saveCheckpoint(privateData);
  assert.deepEqual(openFor(parent, child, root).loadCheckpoint(), privateData);
});

test("tampered checkpoint/blob, invalid identities and symlinked state fail without journal or data deletion", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-worker-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const store = openFor(parent, child, root);
  store.blobStore.setPersistenceAllowed(true);
  const value = Buffer.from("original result");
  await store.blobStore.setBlob(undefined, sha(value), value);
  store.saveCheckpoint(value);
  const checkpointPath = path.join(childDir(root), "checkpoint.json");
  const original = fs.readFileSync(checkpointPath);
  const record = JSON.parse(original.toString()); record.parentId = "another-parent";
  fs.writeFileSync(checkpointPath, JSON.stringify(record));
  assert.throws(() => openFor(parent, child, root).loadCheckpoint(), /corrupt/);
  fs.writeFileSync(checkpointPath, original);
  fs.writeFileSync(path.join(childDir(root), "blobs", sha(value).toString("hex")), "changed");
  await assert.rejects(store.blobStore.getBlob(undefined, sha(value)), /corrupt/);
  await assert.rejects(store.blobStore.setBlob(undefined, Buffer.alloc(32), value), /content-addressed/);
  assert.throws(() => openFor(parent, "../../somewhere", root), /identity/);
  const top = path.join(root, "openbot-subagent-state");
  fs.renameSync(top, top + "-preserved"); fs.symlinkSync(top + "-preserved", top);
  assert.throws(() => openFor(parent, child, root), /unsafe/);
  assert.deepEqual(fs.readFileSync(path.join(top + "-preserved", parent, child, "checkpoint.json")), original);
});

test("a stale checkpoint writer cannot roll back a newer completed tool state", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-worker-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const first = openFor(parent, child, root); const stale = openFor(parent, child, root);
  first.blobStore.setPersistenceAllowed(true); stale.blobStore.setPersistenceAllowed(true);
  first.saveCheckpoint(Buffer.from("completed state"));
  assert.throws(() => stale.saveCheckpoint(Buffer.from("rolled back state")), /concurrently/);
  assert.deepEqual(openFor(parent, child, root).loadCheckpoint(), Buffer.from("completed state"));
  assert.equal(fs.readdirSync(childDir(root)).some(name => name.startsWith(".subagent-")), false);
});
