import assert from "node:assert/strict";
import test from "node:test";
import { runNetworkRecoveryWatchTick } from "./network-recovery-watch.ts";
import { runComputerRecoveryWatchTick, type ComputerRecoveryWatchState } from "./computer-recovery-watch.ts";
import { boxPathsFrom } from "./paths.ts";
import { type FsDeps, type ProcDeps, parseOwnedPid } from "./procs.ts";

function setup(sandData = "/tmp/owned-data") {
  const paths = boxPathsFrom({ repoRoot: "/tmp/installed-openbot", sandData, hostMain: "/tmp/host.cjs" });
  const policy = `${paths.sandData}/openbot-tailscale.json`;
  const installed = `${paths.repoRoot}/recovery/hooks/tailscale-recovery.py`;
  const deployed = `${paths.sandData}/openbot-hooks/tailscale-recovery.py`;
  const files: Record<string, string> = { [policy]: '{"schemaVersion":1,"enabled":true,"exitNodeIP":"100.64.0.2","networking":"kernel"}', [installed]: "owned source" };
  const fs: FsDeps = { read: p => files[p], exists: p => Object.hasOwn(files, p), write: (p, value) => { files[p] = value; }, copy() {}, remove() {}, mkdirp() {} };
  const starts: Array<Parameters<ProcDeps["start"]>[0]> = [];
  let pid: number | undefined;
  let alive = false;
  const procs: ProcDeps = { async port() { return false; },
    readPidFile(path) { return path.endsWith("tailscale/recovery-watch.pid") ? pid : undefined; }, pidAlive() { return alive; },
    start(input) { starts.push(input); return parseOwnedPid(4321); }, stop() {}, term() {}, hostPids() { return []; },
    opengrokHopPids() { return []; }, syntaxCheck() { return { ok: true }; } };
  return { deps: { paths, fs, procs }, files, policy, installed, deployed, starts, state: { notBeforeMs: 0 },
    running(script = installed, data = paths.sandData) { pid = 4321; alive = true; files["/proc/4321/cmdline"] = `/usr/bin/python3\0${script}\0--sand-data\0${data}\0--watch\0`; },
    dead() { alive = false; } };
}

test("network recovery starts from installed source in every harness and mode without local-only", () => {
  for (const mode of [undefined, "official", "custom", "temporal", "unknown"]) {
    const f = setup();
    if (mode !== undefined) f.files[f.deps.paths.mode] = mode;
    const before = { ...f.files };
    const state: ComputerRecoveryWatchState = { notBeforeMs: 0 };
    assert.equal(runComputerRecoveryWatchTick(f.deps, state, 1), "disabled");
    assert.equal(state.network?.lastOutcome, "started");
    assert.equal(f.starts.length, 1);
    assert.deepEqual(f.starts[0]?.argv, [f.installed, "--sand-data", f.deps.paths.sandData, "--watch"]);
    assert.equal(f.starts[0]?.writePidFile, false);
    assert.deepEqual(f.files, before);
  }
});

test("network opt-out leaves login, exit policy, and existing daemon untouched", () => {
  for (const raw of [undefined, '{"schemaVersion":1,"enabled":false}']) {
    const f = setup(); f.running();
    if (raw === undefined) delete f.files[f.policy]; else f.files[f.policy] = raw;
    const before = { ...f.files };
    assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "disabled");
    assert.deepEqual(f.starts, []);
    assert.deepEqual(f.files, before);
  }
});

test("malformed or oversized policy cannot start recovery", () => {
  for (const raw of ["{", "null", "[]", '"value"', '{"enabled":true}', '{"schemaVersion":2,"enabled":true}',
    '{"schemaVersion":1,"enabled":"true"}', '{"schemaVersion":1,"enabled":true,"label":"' + "é".repeat(2100) + '"}']) {
    const f = setup(); f.files[f.policy] = raw;
    assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "invalid-policy");
    assert.equal(f.starts.length, 0);
  }
});

test("existing deployed helper is supported when installed source is unavailable", () => {
  const f = setup(); delete f.files[f.installed]; f.files[f.deployed] = "owned deployed helper";
  assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.equal(f.starts[0]?.argv[0], f.deployed);
});

test("missing helpers cannot fabricate a successful restart", () => {
  const f = setup(); delete f.files[f.installed];
  assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "uninstalled");
  assert.equal(f.starts.length, 0);
});

test("already running installed or deployed watchers survive panel restarts", () => {
  for (const helper of ["installed", "deployed"] as const) {
    const f = setup(); f.running(f[helper]);
    assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "running");
    assert.equal(f.starts.length, 0);
  }
});

test("a foreign, recycled, or wrong-data-root PID never suppresses recovery", () => {
  for (const argv of ["/usr/bin/foreign\0", "python\0relative.py\0", "/usr/bin/python3\0-c\0not-the-watcher\0", "/usr/bin/python3\0/tmp/installed-openbot/recovery/hooks/tailscale-recovery.py\0--sand-data\0/other-data\0--watch\0"]) {
    const f = setup(); f.running(); f.files["/proc/4321/cmdline"] = argv;
    assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "started");
    assert.equal(f.starts.length, 1);
  }
});

test("watcher exit during PID inspection can be recovered", () => {
  const f = setup(); f.running();
  const read = f.deps.fs.read;
  f.deps.fs.read = path => { if (path.startsWith("/proc/")) throw new Error("process exited"); return read(path); };
  assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "started");
});

test("network spawn retries have their own backoff and recover dead watchers", () => {
  const f = setup();
  assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 15_000), "starting");
  f.running(); assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 30_000), "running");
  f.dead(); assert.equal(runNetworkRecoveryWatchTick(f.deps, f.state, 60_001), "started");
  assert.equal(f.starts.length, 2);
});

test("network failure does not prevent opted-in local-engine recovery", () => {
  const f = setup("/home/box/sand-data"); f.files[f.deps.paths.mode] = "custom";
  f.files[`${f.deps.paths.sandData}/openbot-local-only.enable`] = "";
  f.files[`${f.deps.paths.sandData}/openbot-hooks/computer-recovery.py`] = "owned local helper";
  const start = f.deps.procs.start;
  f.deps.procs.start = input => { if (input.argv.includes("--sand-data")) throw new Error("network start failed"); return start(input); };
  const state: ComputerRecoveryWatchState = { notBeforeMs: 0 };
  assert.equal(runComputerRecoveryWatchTick(f.deps, state, 1), "started");
  assert.equal(state.network?.lastOutcome, "failed");
  assert.equal(f.starts.length, 1);
  assert.deepEqual(f.starts[0]?.argv, [`${f.deps.paths.sandData}/openbot-hooks/computer-recovery.py`, "--host", f.deps.paths.hostMain, "--watch"]);
});

test("network recovery still starts when local injectors do not support the selected data root", () => {
  const f = setup(); f.files[f.deps.paths.mode] = "custom";
  f.files[`${f.deps.paths.sandData}/openbot-local-only.enable`] = "";
  const state: ComputerRecoveryWatchState = { notBeforeMs: 0 };
  assert.equal(runComputerRecoveryWatchTick(f.deps, state, 1), "unsupported-path");
  assert.equal(state.network?.lastOutcome, "started");
  assert.equal(f.starts.length, 1);
  assert.equal(f.starts[0]?.argv[0], f.installed);
});
