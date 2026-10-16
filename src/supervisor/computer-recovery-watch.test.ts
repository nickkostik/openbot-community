import assert from "node:assert/strict";
import test from "node:test";
import { runComputerRecoveryWatchTick } from "./computer-recovery-watch.ts";
import { boxPathsFrom } from "./paths.ts";
import { type FsDeps, type ProcDeps, parseOwnedPid } from "./procs.ts";

function setup(sandData = "/home/box/sand-data", hostMain = "/tmp/host.cjs") {
  const paths = boxPathsFrom({ repoRoot: "/tmp/recovery-repo", sandData, hostMain });
  const script = `${paths.sandData}/openbot-hooks/computer-recovery.py`;
  const files: Record<string, string> = { [paths.mode]: "custom", [`${paths.sandData}/openbot-local-only.enable`]: "", [script]: "installed" };
  const fs: FsDeps = { read: p => files[p], exists: p => Object.hasOwn(files, p), write: (p, value) => { files[p] = value; }, copy() {}, remove() {}, mkdirp() {} };
  const starts: Array<Parameters<ProcDeps["start"]>[0]> = [];
  let pid: number | undefined;
  let alive = false;
  const procs: ProcDeps = { async port() { return false; }, readPidFile() { return pid; }, pidAlive() { return alive; },
    start(input) { starts.push(input); return parseOwnedPid(1234); }, stop() {}, term() {}, hostPids() { return []; },
    opengrokHopPids() { return []; }, syntaxCheck() { return { ok: true }; } };
  return { deps: { paths, fs, procs }, files, script, starts, state: { notBeforeMs: 0 },
    running(helper = script) { pid = 1234; alive = true; files["/proc/1234/cmdline"] = `/usr/bin/python3\0${helper}\0--host\0${paths.hostMain}\0--watch\0`; },
    dead() { alive = false; } };
}

test("recovery is opt-in and rejects unknown modes", () => {
  for (const option of ["unknown-mode", "no-flag", "uninstalled"]) {
    const f = setup();
    if (option === "unknown-mode") f.files[f.deps.paths.mode] = "unknown";
    if (option === "no-flag") delete f.files[`${f.deps.paths.sandData}/openbot-local-only.enable`];
    if (option === "uninstalled") delete f.files[f.script];
    assert.notEqual(runComputerRecoveryWatchTick(f.deps, f.state, 1), "started");
    assert.equal(f.starts.length, 0);
  }
});

test("explicitly local recovery starts in Official and preserves the saved mode", () => {
  const f = setup(); f.files[f.deps.paths.mode] = "official";
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.equal(f.starts.length, 1);
  assert.equal(f.files[f.deps.paths.mode], "official");
  f.running();
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 2), "running");
  assert.equal(f.starts.length, 1);
});

test("owned running daemon survives UI restarts without a duplicate", () => {
  const f = setup(); f.running();
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "running");
  assert.equal(f.starts.length, 0);
});

test("stale PID cannot adopt a foreign process", () => {
  const f = setup(); f.running(); f.files["/proc/1234/cmdline"] = "/usr/bin/foreign\0";
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.equal(f.starts[0]?.command, "/usr/bin/python3");
  assert.deepEqual(f.starts[0]?.argv, [f.script, "--host", f.deps.paths.hostMain, "--watch"]);
  assert.equal(f.starts[0]?.writePidFile, false);
});

test("pending startup is bounded and a dead watcher is recovered", () => {
  const f = setup();
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 15_000), "starting");
  f.running(); assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 60_001), "running");
  f.dead(); assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 75_001), "started");
  assert.equal(f.starts.length, 2);
});

test("packaged hooks recover a fresh install without a manual native copy", () => {
  const f = setup(); delete f.files[f.script];
  const installed = `${f.deps.paths.repoRoot}/recovery/hooks/computer-recovery.py`;
  f.files[installed] = "packaged helper";
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "started");
  assert.deepEqual(f.starts[0]?.argv, [installed, "--host", f.deps.paths.hostMain, "--watch"]);
  f.running(installed);
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 2), "running");
  assert.equal(f.starts.length, 1);
});

test("packaged hook takes precedence while an owned deployed watcher is preserved", () => {
  const f = setup(); const installed = `${f.deps.paths.repoRoot}/recovery/hooks/computer-recovery.py`;
  f.files[installed] = "packaged helper";
  f.running();
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "running");
  f.dead();
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 2), "started");
  assert.equal(f.starts[0]?.argv[0], installed);
});

test("a daemon targeting another host cannot be adopted or competed with", () => {
  for (const args of ["--host\0/other/host.cjs\0--watch\0", "--watch\0", "--host\0/tmp/host.cjs\0--watch\0--unknown\0"]) {
    const f = setup(); f.running();
    f.files["/proc/1234/cmdline"] = `/usr/bin/python3\0${f.script}\0${args}`;
    assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "conflict");
    assert.equal(f.starts.length, 0);
  }
});

test("legacy factory watcher retains its existing lifetime lock", () => {
  const f = setup(undefined, "/opt/sand/sand-host/host-main.cjs"); f.running();
  f.files["/proc/1234/cmdline"] = `/usr/bin/python3\0${f.script}\0--watch\0`;
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "running");
  assert.equal(f.starts.length, 0);
});

test("unsupported native data roots never launch a watcher against factory files", () => {
  const f = setup("/tmp/other-data");
  const before = { ...f.files };
  assert.equal(runComputerRecoveryWatchTick(f.deps, f.state, 1), "unsupported-path");
  assert.equal(f.starts.length, 0);
  assert.deepEqual(f.files, before);
});
