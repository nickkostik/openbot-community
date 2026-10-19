import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { localTransfer, publicTransfer } from "./local-transfer.ts";
import { boxPathsFrom } from "../supervisor/paths.ts";
import { nodeFs, nodeProcs } from "../supervisor/procs.ts";

function fixture(t: { after: (fn: () => void) => void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "transfer-contract-"));
  t.after(() => fs.rmSync(root, { force: true, recursive: true }));
  const data = path.join(root, "data");
  fs.mkdirSync(data);
  fs.mkdirSync(path.join(root, "recovery/hooks"), { recursive: true });
  fs.writeFileSync(path.join(root, "recovery/hooks/custom-dispatch-recovery.py"), "# owned helper");
  const paths = boxPathsFrom({ repoRoot: root, sandData: data, hostMain: path.join(root, "host.cjs") });
  fs.writeFileSync(paths.mode, "custom\n");
  fs.writeFileSync(path.join(data, "openbot-local-only.enable"), "");
  return { paths, fs: nodeFs(), procs: nodeProcs() };
}

test("fixed transfer command carries no caller shell, paths, environment or secrets", async t => {
  const current = fixture(t);
  const protectedBefore = fs.readFileSync(current.paths.mode);
  for (const action of ["start", "resume"] as const) {
    const status = await localTransfer(current, action, async (command, args, options) => {
      assert.equal(command, "/usr/bin/python3");
      assert.deepEqual(args, [path.join(current.paths.repoRoot, "recovery/hooks/custom-dispatch-recovery.py"), "--root", current.paths.sandData, "--request"]);
      assert.equal(options.timeout, 10000);
      assert.equal("env" in options, false);
      return { stdout: JSON.stringify({ enabled: true, phase: "review-needed", workerRunning: false, message: "private", diagnosticCode: "history-conflict" }) };
    });
    assert.equal(status.phase, "review-needed");
    assert.equal("message" in status, false);
  }
  assert.deepEqual(fs.readFileSync(current.paths.mode), protectedBefore);
});

test("Official, invalid mode and symlinked opt-in cannot request a worker", async t => {
  const current = fixture(t);
  let calls = 0;
  const run = async () => { calls++; return { stdout: "{}" }; };
  for (const mode of ["official", "", "garbage"]) {
    fs.writeFileSync(current.paths.mode, mode);
    await assert.rejects(localTransfer(current, "resume", run));
  }
  fs.writeFileSync(current.paths.mode, "custom");
  const flag = path.join(current.paths.sandData, "openbot-local-only.enable");
  fs.unlinkSync(flag); fs.symlinkSync(current.paths.mode, flag);
  await assert.rejects(localTransfer(current, "start", run));
  assert.equal(calls, 0);
});

test("public progress allowlists counts and safe refusal phase without exposing private artifacts", () => {
  const status = publicTransfer({ phase: "review-needed", enabled: true, workerRunning: true,
    jobId: "00000000-0000-4000-8000-000000000001", agentCount: 3,
    counts: { active: 1, copied: 2, secret: "credential" }, refusalPhase: "registering",
    diagnosticCode: "identity-conflict", error: "secret", agentIds: ["private"], nativeAppDeliveryVerified: true, billingVerified: true });
  assert.deepEqual(status.counts, { copied: 2, active: 1 });
  assert.equal(status.refusalPhase, "registering");
  assert.equal(status.nativeAppDeliveryVerified, false);
  assert.equal(status.billingVerified, false);
  assert.equal(JSON.stringify(status).includes("private"), false);
  assert.equal(JSON.stringify(status).includes("credential"), false);
});

test("real helper status remains read-only and sanitizes existing recovery state", async t => {
  const current = fixture(t);
  for (const name of ["custom-dispatch-recovery.py", "server-roster-check.py"]) {
    fs.copyFileSync(new URL(`../../recovery/hooks/${name}`, import.meta.url), path.join(current.paths.repoRoot, "recovery/hooks", name));
  }
  const stateFile = path.join(current.paths.sandData, "openbot-custom-dispatch-state.json");
  const state = JSON.stringify({ jobId: "00000000-0000-4000-8000-000000000001", phase: "review-needed", diagnosticCode: "history-conflict", message: "private-detail" });
  fs.writeFileSync(stateFile, state);
  const status = await localTransfer(current);
  assert.equal(status.phase, "review-needed");
  assert.equal(status.enabled, true);
  assert.equal(status.workerRunning, false);
  assert.equal(status.diagnosticAssistant, "unavailable");
  assert.equal(JSON.stringify(status).includes("private-detail"), false);
  assert.equal(fs.readFileSync(stateFile, "utf8"), state);
});


test("saved completion without current proof is pending and a maintenance hold is not a worker", () => {
  const status = publicTransfer({ enabled: true, phase: "bound-box", lockHeld: true, workerRunning: false,
    publicationVerified: false, agentCount: 34, counts: { "registered-box": 34, active: 0 } });
  assert.equal(status.phase, "waiting-for-publication");
  assert.equal(status.lockHeld, true);
  assert.equal(status.workerRunning, false);
  assert.equal(status.publicationVerified, false);
  assert.equal(publicTransfer({ phase: "bound-box", publicationVerified: true }).phase, "bound-box");
});
