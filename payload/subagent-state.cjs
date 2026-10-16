"use strict";

// Native completed workers are evicted from their runner cache. Keep their
// checkpoint and content-addressed blobs so Task.resume restores that same
// context instead of starting an empty state under an existing journal ID.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const MAX_BLOB = 64 * 1024 * 1024;
const MAX_CHECKPOINT = 32 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function ownedDirectory(directory, create) {
  if (create && !fs.existsSync(directory)) fs.mkdirSync(directory, { mode: 0o700 });
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.uid !== process.getuid()) throw new Error("openbot-subagent: unsafe state directory");
}

function readOwned(file, limit) {
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const info = fs.fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || info.size > limit) throw new Error("openbot-subagent: unsafe state file");
    return fs.readFileSync(fd);
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw error;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function atomic(file, content, expected) {
  const temporary = path.join(path.dirname(file), ".subagent-" + crypto.randomUUID());
  let fd;
  try {
    fd = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(fd, content);
    fs.fsyncSync(fd);
    fs.closeSync(fd); fd = undefined;
    const current = readOwned(file, MAX_CHECKPOINT * 2);
    if (expected === undefined ? current !== undefined : current === undefined || !current.equals(expected)) {
      throw new Error("openbot-subagent: checkpoint changed concurrently");
    }
    fs.renameSync(temporary, file);
    const directory = fs.openSync(path.dirname(file), fs.constants.O_RDONLY | fs.constants.O_DIRECTORY);
    try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); }
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}

function digest(value) { return crypto.createHash("sha256").update(value).digest("hex"); }

function openFor(parentId, subagentId, root) {
  if (!UUID.test(parentId) || !subagentId.startsWith("sand-subagent-") || !UUID.test(subagentId.slice(14))) {
    throw new Error("openbot-subagent: invalid parent or worker identity");
  }
  const sd = root || process.env.OPENBOT_SAND_DATA || "/home/box/sand-data";
  ownedDirectory(sd, false);
  const top = path.join(sd, "openbot-subagent-state"); ownedDirectory(top, true);
  const parent = path.join(top, parentId); ownedDirectory(parent, true);
  const directory = path.join(parent, subagentId); ownedDirectory(directory, true);
  const blobs = path.join(directory, "blobs"); ownedDirectory(blobs, true);
  const checkpointPath = path.join(directory, "checkpoint.json");
  const disabledPath = path.join(directory, "recording-disabled");
  let expected = readOwned(checkpointPath, MAX_CHECKPOINT * 2);
  let allowed = false;
  const ephemeral = new Map();
  function keyFor(id) {
    const bytes = Buffer.from(id);
    if (bytes.length !== 32) throw new Error("openbot-subagent: invalid blob identity");
    return bytes.toString("hex");
  }
  function persistBlob(key, value) {
    const file = path.join(blobs, key);
    const existing = readOwned(file, MAX_BLOB);
    if (existing !== undefined) {
      if (!existing.equals(value)) throw new Error("openbot-subagent: content-address collision");
      return;
    }
    atomic(file, value, undefined);
  }
  const blobStore = {
    async getBlob(_ctx, id) {
      const key = keyFor(id);
      const value = ephemeral.get(key) || readOwned(path.join(blobs, key), MAX_BLOB);
      if (value !== undefined && digest(value) !== key) throw new Error("openbot-subagent: corrupt saved blob");
      return value;
    },
    async setBlob(_ctx, id, data) {
      const key = keyFor(id); const value = Buffer.from(data);
      if (value.length > MAX_BLOB || digest(value) !== key) throw new Error("openbot-subagent: invalid content-addressed blob");
      if (allowed) persistBlob(key, value); else ephemeral.set(key, value);
    },
    setBlobLocallyOnly(ctx, id, data) { return this.setBlob(ctx, id, data); },
    async flush() {},
    setPersistenceAllowed(value) {
      allowed = value === true;
      if (allowed) {
        for (const [key, data] of ephemeral) persistBlob(key, data);
        ephemeral.clear();
      } else if (readOwned(disabledPath, 16) === undefined) {
        atomic(disabledPath, Buffer.from("0\n"), undefined);
      }
    }
  };
  return {
    blobStore,
    loadCheckpoint() {
      if (readOwned(disabledPath, 16) !== undefined) throw new Error("openbot-subagent: prior task context was not recorded; start a new task");
      if (expected === undefined) return undefined;
      const record = JSON.parse(expected.toString("utf8"));
      const value = typeof record.data === "string" ? Buffer.from(record.data, "base64") : Buffer.alloc(0);
      if (record.version !== 1 || record.parentId !== parentId || record.subagentId !== subagentId
          || value.length > MAX_CHECKPOINT || value.toString("base64") !== record.data || digest(value) !== record.sha256) {
        throw new Error("openbot-subagent: corrupt saved checkpoint");
      }
      return value;
    },
    saveCheckpoint(data) {
      if (!allowed) return;
      const value = Buffer.from(data);
      if (value.length > MAX_CHECKPOINT) throw new Error("openbot-subagent: checkpoint exceeds state limit");
      const content = Buffer.from(JSON.stringify({ version: 1, parentId, subagentId, sha256: digest(value), data: value.toString("base64") }) + "\n");
      atomic(checkpointPath, content, expected); expected = content;
      if (readOwned(disabledPath, 16) !== undefined) fs.unlinkSync(disabledPath);
    }
  };
}

module.exports = { openFor };
