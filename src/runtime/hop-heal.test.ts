import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import os from "node:os";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const runtimePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/runtime.cjs");

type HealResult = { ok: boolean; action: string; pid?: number };
type HealOverrides = { host?: string; port?: number; unified?: boolean; entry?: string; pidFile?: string; logFile?: string };
type HealTarget = HealOverrides & { unified: boolean; entry: string; pidFile: string; argv: string[] };
type RuntimeHeal = {
  HOP_HEAL: Record<string, number>;
  ensureHopUp: (overrides?: HealOverrides, allowSpawn?: boolean) => Promise<HealResult>;
  hopHealTarget: (overrides?: HealOverrides) => HealTarget;
  hopPortOpen: (host: string, port: number, timeoutMs?: number) => Promise<boolean>;
  resetHopHealState: () => void;
  hopRequestWithRetry: (body: unknown, healOverrides?: HealOverrides) => Promise<http.IncomingMessage>;
};

function loadRuntime(): RuntimeHeal {
  delete require.cache[runtimePath];
  return require(runtimePath) as RuntimeHeal;
}

const runtime = loadRuntime();

test.beforeEach(() => {
  runtime.resetHopHealState();
});

function tmpDir(): string {
  return mkdtempSync(path.join(os.tmpdir(), "openbot-hop-heal-"));
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      if (!addr || typeof addr === "string") {
        server.close(() => reject(new Error("no port")));
        return;
      }
      const port = addr.port;
      server.close(() => resolve(port));
    });
    server.on("error", reject);
  });
}

function writeFixture(d: string): string {
  const entry = path.join(d, "fake-hop.cjs");
  writeFileSync(
    entry,
    [
      '"use strict";',
      'var http = require("http");',
      'var fs = require("fs");',
      "var port = Number(process.env.OPENBOT_HOP_PORT);",
      'var marker = process.env.OPENBOT_HEAL_MARKER || "";',
      'if (marker) { try { fs.appendFileSync(marker, "spawned;"); } catch (e) {} }',
      "http.createServer(function (req, res) {",
      '  res.writeHead(200, { "Content-Type": "application/json" });',
      '  res.end(\'{"ok":true}\');',
      '}).listen(port, "127.0.0.1");',
    ].join("\n"),
  );
  return entry;
}

function readPid(pidFile: string): number | undefined {
  try {
    const pid = Number(readFileSync(pidFile, "utf8").trim());
    return Number.isInteger(pid) && pid > 0 ? pid : undefined;
  } catch {
    return undefined;
  }
}

function killPid(pidFile: string): void {
  const pid = readPid(pidFile);
  if (pid !== undefined) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }
}

test("HOP_HEAL budgets are shaped as documented", () => {
  assert.equal(runtime.HOP_HEAL.probeTimeoutMs, 250);
  assert.equal(runtime.HOP_HEAL.pollMs, 100);
  assert.equal(runtime.HOP_HEAL.startupGraceMs, 3000);
  assert.equal(runtime.HOP_HEAL.spawnWaitMs, 15000);
  assert.equal(runtime.HOP_HEAL.spawnThrottleMs, 10000);
  assert.equal(runtime.HOP_HEAL.shortWaitMs, 2000);
  assert.equal(runtime.HOP_HEAL.retryBudgetMs, 30000);
});

test("unified healing uses reconcile and the UI pidfile even with a stale standalone entry", () => {
  const target = runtime.hopHealTarget({ port: 9280, entry: "/stale/payload/hop-server.cjs" });
  assert.equal(target.unified, true);
  assert.match(target.entry, /src\/cli\.ts$/u);
  assert.match(target.pidFile, /openbot-ui\.pid$/u);
  assert.equal(target.argv[0], "--experimental-strip-types");
  assert.equal(target.argv[2], "install");
  assert.ok(target.argv.includes("--defer-host-bounce"));
  assert.ok(target.argv.includes("--keep-service"));
});

test("a hop-only listener is not a healthy unified service", async () => {
  const server = http.createServer((_req, res) => { res.writeHead(404); res.end("not found"); });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  assert.ok(addr && typeof addr !== "string");
  const previousWait = runtime.HOP_HEAL.shortWaitMs;
  assert.ok(typeof previousWait === "number");
  runtime.HOP_HEAL.shortWaitMs = 50;
  try {
    const result = await runtime.ensureHopUp({ host: "127.0.0.1", port: addr.port, unified: true }, false);
    assert.equal(result.ok, false);
    assert.equal(result.action, "listener-unready");
  } finally {
    runtime.HOP_HEAL.shortWaitMs = previousWait;
    server.close(); server.closeAllConnections();
  }
});

test("unified reconcile exits while the UI owns the pidfile and serves control routes", async () => {
  const d = tmpDir(), port = await freePort();
  const pidFile = path.join(d, "ui.pid");
  const ui = path.join(d, "fixture-ui.cjs"), entry = path.join(d, "fixture-reconcile.cjs");
  writeFileSync(ui, [
    "const fs=require('node:fs'),http=require('node:http');",
    "const server=http.createServer((req,res)=>{",
    "res.writeHead(200,{'Content-Type':'application/json'});",
    "res.end(req.url==='/api/pause'?JSON.stringify({paused:false}):JSON.stringify({ui:true}));",
    "});",
    "server.listen(Number(process.env.OPENBOT_UI_PORT),'127.0.0.1',()=>fs.writeFileSync(process.env.OPENBOT_UI_PID,String(process.pid)+'\\n'));",
  ].join("\n"));
  writeFileSync(entry, [
    "const fs=require('node:fs'),assert=require('node:assert/strict'),cp=require('node:child_process');",
    "assert.equal(process.argv[2],'install');assert.ok(process.argv.includes('--defer-host-bounce'));assert.ok(process.argv.includes('--keep-service'));",
    `fs.writeFileSync(${JSON.stringify(path.join(d, "reconcile.pid"))},String(process.pid));`,
    `const ui=cp.spawn(process.execPath,[${JSON.stringify(ui)}],{detached:true,stdio:'ignore',env:process.env});ui.unref();`,
  ].join("\n"));
  try {
    const result = await runtime.ensureHopUp({ host: "127.0.0.1", port, unified: true, entry, pidFile,
      logFile: path.join(d, "ui.log") });
    assert.equal(result.ok, true);
    assert.equal(result.pid, readPid(pidFile));
    assert.notEqual(result.pid, Number(readFileSync(path.join(d, "reconcile.pid"), "utf8")));
    const response = await fetch(`http://127.0.0.1:${port}/api/pause`);
    assert.deepEqual(await response.json(), { paused: false });
    assert.equal(existsSync(path.join(d, "openbot-hop.pid")), false);
  } finally { killPid(pidFile); }
});

test("slow unified readiness never reinstalls or interrupts an active stream under concurrent healing", async () => {
  const d = tmpDir(), marker = path.join(d, "reinstall.log");
  let server: http.Server;
  server = http.createServer((req, res) => {
    if (req.url === "/api/pause") {
      setTimeout(() => res.end(JSON.stringify({ paused: false })), 150);
    } else if (req.url === "/fixture-reinstall") {
      // Reproduce the destructive action an old healer's install performed.
      server.closeAllConnections();
    } else {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write("data: started\n\n");
      setTimeout(() => res.end("data: completed\n\ndata: [DONE]\n\n"), 400);
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  assert.ok(addr && typeof addr !== "string");
  const entry = path.join(d, "fixture-reconcile.cjs");
  writeFileSync(entry, [
    "const fs=require('node:fs'),http=require('node:http');",
    `fs.writeFileSync(${JSON.stringify(marker)},'installed');`,
    `http.get('http://127.0.0.1:${addr.port}/fixture-reinstall').on('error',()=>{});`,
  ].join("\n"));
  const previousProbe = runtime.HOP_HEAL.probeTimeoutMs;
  assert.equal(typeof previousProbe, "number");
  runtime.HOP_HEAL.probeTimeoutMs = 30;
  try {
    const response = await fetch(`http://127.0.0.1:${addr.port}/stream`);
    const complete = response.text();
    const overrides = { host: "127.0.0.1", port: addr.port, unified: true, entry,
      pidFile: path.join(d, "ui.pid"), logFile: path.join(d, "ui.log") };
    const healed = await Promise.all(Array.from({ length: 5 }, () => runtime.ensureHopUp(overrides)));
    for (const result of healed) assert.deepEqual(result, { ok: false, action: "listener-unready" });
    assert.equal(existsSync(marker), false, "a readiness timeout must not launch reinstall");
    assert.equal(await complete, "data: started\n\ndata: completed\n\ndata: [DONE]\n\n");
  } finally {
    if (previousProbe !== undefined) runtime.HOP_HEAL.probeTimeoutMs = previousProbe;
    server.close(); server.closeAllConnections();
  }
});

test("ensureHopUp fast-paths when the port is already open", async () => {
  const d = tmpDir();
  const server = http.createServer((_req, res) => {
    res.end("ok");
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const addr = server.address();
  assert.ok(addr && typeof addr !== "string");
  try {
    const result = await runtime.ensureHopUp({
      host: "127.0.0.1",
      port: addr.port,
      entry: path.join(d, "must-not-spawn.cjs"),
      pidFile: path.join(d, "hop.pid"),
      logFile: path.join(d, "hop.log"),
    });
    assert.equal(result.ok, true);
    assert.equal(result.action, "already-up");
    assert.equal(existsSync(path.join(d, "hop.pid")), false);
  } finally {
    server.close();
    server.closeAllConnections();
  }
});

test("ensureHopUp fails fast and never throws when nothing is launchable", async () => {
  const d = tmpDir();
  const port = await freePort();
  const started = Date.now();
  const result = await runtime.ensureHopUp({
    host: "127.0.0.1",
    port,
    entry: path.join(d, "no-such-hop-server.cjs"),
    pidFile: path.join(d, "hop.pid"),
    logFile: path.join(d, "hop.log"),
  });
  assert.equal(result.ok, false);
  assert.equal(result.action, "spawn-failed");
  assert.ok(Date.now() - started < 10000);
});

test("ensureHopUp with allowSpawn=false only confirms", async () => {
  const d = tmpDir();
  const port = await freePort();
  const started = Date.now();
  const result = await runtime.ensureHopUp(
    {
      host: "127.0.0.1",
      port,
      entry: path.join(d, "x.cjs"),
      pidFile: path.join(d, "hop.pid"),
      logFile: path.join(d, "hop.log"),
    },
    false,
  );
  assert.equal(result.ok, false);
  assert.equal(result.action, "still-down");
  assert.ok(Date.now() - started < 10000);
});

test("concurrent healers share one spawn", async () => {
  const d = tmpDir();
  const port = await freePort();
  const marker = path.join(d, "spawns.log");
  const overrides: HealOverrides = {
    host: "127.0.0.1",
    port,
    entry: writeFixture(d),
    pidFile: path.join(d, "hop.pid"),
    logFile: path.join(d, "hop.log"),
  };
  process.env.OPENBOT_HEAL_MARKER = marker;
  try {
    const results = await Promise.all([
      runtime.ensureHopUp(overrides),
      runtime.ensureHopUp(overrides),
      runtime.ensureHopUp(overrides),
      runtime.ensureHopUp(overrides),
      runtime.ensureHopUp(overrides),
    ]);
    for (const result of results) assert.equal(result.ok, true);
    const spawns = readFileSync(marker, "utf8").split("spawned").length - 1;
    assert.equal(spawns, 1);
  } finally {
    delete process.env.OPENBOT_HEAL_MARKER;
    killPid(path.join(d, "hop.pid"));
  }
});

test("hopRequestWithRetry heals a dead hop then replays the request", async () => {
  const d = tmpDir();
  const deadPort = await freePort();
  const marker = path.join(d, "spawns.log");
  const prevHost = process.env.OPENBOT_HOP_HOST;
  const prevPort = process.env.OPENBOT_HOP_PORT;
  const prevMarker = process.env.OPENBOT_HEAL_MARKER;
  process.env.OPENBOT_HOP_HOST = "127.0.0.1";
  process.env.OPENBOT_HOP_PORT = String(deadPort);
  process.env.OPENBOT_HEAL_MARKER = marker;
  delete require.cache[runtimePath];
  const fresh = require(runtimePath) as RuntimeHeal;
  try {
    const res = await fresh.hopRequestWithRetry({ model: "m", messages: [] }, {
      entry: writeFixture(d),
      pidFile: path.join(d, "hop.pid"),
      logFile: path.join(d, "hop.log"),
    });
    assert.equal(res.statusCode, 200);
    const text = await new Promise<string>((resolve, reject) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
      res.on("error", reject);
    });
    assert.match(text, /"ok":true/);
    const spawns = readFileSync(marker, "utf8").split("spawned").length - 1;
    assert.equal(spawns, 1);
  } finally {
    killPid(path.join(d, "hop.pid"));
    if (prevHost === undefined) delete process.env.OPENBOT_HOP_HOST;
    else process.env.OPENBOT_HOP_HOST = prevHost;
    if (prevPort === undefined) delete process.env.OPENBOT_HOP_PORT;
    else process.env.OPENBOT_HOP_PORT = prevPort;
    if (prevMarker === undefined) delete process.env.OPENBOT_HEAL_MARKER;
    else process.env.OPENBOT_HEAL_MARKER = prevMarker;
  }
});

test("a fresh pidfile waits instead of double-spawning", async () => {
  const d = tmpDir();
  const port = await freePort();
  const marker = path.join(d, "spawns.log");
  const pidFile = path.join(d, "hop.pid");
  writeFileSync(pidFile, "999999998" + "\n");
  const prevWait = runtime.HOP_HEAL.spawnWaitMs;
  assert.ok(typeof prevWait === "number");
  runtime.HOP_HEAL.spawnWaitMs = 1500;
  try {
    const result = await runtime.ensureHopUp({
      host: "127.0.0.1",
      port,
      entry: writeFixture(d),
      pidFile,
      logFile: path.join(d, "hop.log"),
    });
    assert.equal(result.ok, false);
    assert.equal(result.action, "spawn-throttled");
    assert.equal(existsSync(marker), false);
  } finally {
    if (typeof prevWait === "number") runtime.HOP_HEAL.spawnWaitMs = prevWait;
    killPid(pidFile);
  }
});
