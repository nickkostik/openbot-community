import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import test from "node:test";
import { wrapHostSource } from "../host/wrap.ts";
import { parseUiProviderSave } from "../parse/ui.ts";
import { customBoxFromProvider, officialBox, parseUpstreamOrigin } from "../parse/argv.ts";
import { nodeFs, parseOwnedPid, type ProcDeps } from "./procs.ts";
import { boxPathsFrom, joinAbs } from "./paths.ts";
import { reconcile } from "./reconcile.ts";
import { localEngineReadiness, selectLocalEngine } from "./local-engine.ts";

const sha = (s: string): string => createHash("sha256").update(s).digest("hex");
const vendor = '"use strict";\n// latest stock no longer has BOX execution\n';
// Model the actual runner contract, not a factory-only stub that could pass
// census while still having no code capable of dispatching a BOX turn.
const stock = `/* openbot-latest-box-compatibility-v1 */
function createProtoSessionProvider(client) { return client; }
class Box {
  async sendPrompt(prompt, options2) {
${"    // preserved native turn runner\n".repeat(140)}
    return this.runAgentTurn(prompt, options2);
  }
  nextTurnEpoch() { return 1; }
  runServerScheduledAutomation() {}
}
`;

function setup(t: { after(fn: () => void): void }) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-engine-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const paths = boxPathsFrom({ repoRoot: path.join(directory, "repo"), sandData: path.join(directory, "data"), hostMain: path.join(directory, "host-main.cjs") });
  const real = nodeFs();
  real.mkdirp(paths.sandData);
  real.write(paths.hostMain, vendor);
  real.write(paths.mode, "official\n");
  real.write(paths.plan, "retained old catalog");
  const engine = joinAbs(paths.sandData, "openbot-local-engine");
  real.mkdirp(engine);
  const proof = wrapHostSource({ source: stock, runtimePath: paths.runtime, payloadFingerprint: "1234567812345678" });
  assert.equal(proof.kind, "wrapped");
  if (proof.kind !== "wrapped") throw new Error("fixture wrapping failed");
  real.write(joinAbs(engine, "stock.cjs"), stock);
  real.write(joinAbs(engine, "wrapped.cjs"), proof.source);
  const policy = joinAbs(paths.sandData, "openbot-local-engine.json");
  const manifest = { schemaVersion: 1, enabled: true, stockSha256: sha(stock), wrappedSha256: sha(proof.source) };
  real.write(policy, JSON.stringify(manifest));
  let ours = true;
  const stops: number[] = [];
  let syntax: (file: string) => { ok: true } | { ok: false; stderr: string } = () => ({ ok: true });
  const procs: ProcDeps = {
    dependencyCheck() { return { ok: true }; },
    async port() { return ours; }, readPidFile(p) { return p === paths.uiPid && ours ? 21 : undefined; },
    pidAlive(p) { return p === 21 && ours; }, start() { ours = true; return parseOwnedPid(21); },
    stop(p) { stops.push(p); }, hostPids() { return [parseOwnedPid(22)]; }, opengrokHopPids() { return []; },
    term(p) { stops.push(p); }, syntaxCheck(p) { return syntax(p); },
  };
  const desired = customBoxFromProvider({ paths, origin: parseUpstreamOrigin("https://provider.example/v1"), name: "Example", modelSlug: "model" });
  return { paths, real, engine, policy, manifest, wrapped: proof.source, deps: { paths, fs: real, procs }, desired, stops,
    syntax(fn: typeof syntax) { syntax = fn; }, foreign() { procs.readPidFile = () => undefined; } };
}

function unchangedControls(ctx: ReturnType<typeof setup>) {
  assert.equal(ctx.real.read(ctx.paths.mode), "official\n");
  assert.equal(ctx.real.read(ctx.paths.plan), "retained old catalog");
  assert.equal(ctx.real.read(ctx.paths.hostMain), vendor);
  assert.equal(ctx.real.exists(joinAbs(ctx.paths.sandData, "openbot-official-host.json")), false);
  assert.deepEqual(ctx.stops, []);
}

test("panel Official → Custom selects the real pinned BOX runner, then returns to exact latest vendor with catalog retained", async (t) => {
  const ctx = setup(t);
  const custom = await reconcile(ctx.desired, ctx.deps);
  assert.equal(custom.kind, "ok");
  assert.equal(ctx.real.read(ctx.paths.mode), "custom\n");
  assert.match(ctx.real.read(ctx.paths.hostMain)!, /openbot-latest-box-compatibility/);
  assert.equal(ctx.real.read(ctx.paths.knownBackup), vendor);
  const catalog = ctx.real.read(ctx.paths.plan);
  // A recovery tick is idempotent, including when the selected wrapper already
  // has exactly the current payload stamp.
  const second = await reconcile(ctx.desired, ctx.deps);
  assert.equal(second.kind, "ok");
  if (second.kind === "ok") assert.equal(second.wrapBytesChanged, false);
  ctx.real.write(ctx.paths.logsSettings, JSON.stringify({ loggingEnabled: true }));
  const official = await reconcile(officialBox(ctx.paths), ctx.deps);
  assert.equal(official.kind, "ok");
  assert.equal(ctx.real.read(ctx.paths.mode), "official\n");
  assert.equal(ctx.real.read(ctx.paths.hostMain), vendor);
  assert.equal(ctx.real.read(ctx.paths.knownBackup), vendor);
  assert.equal(ctx.real.read(ctx.paths.plan), catalog);
  assert.equal(JSON.parse(ctx.real.read(ctx.paths.logsSettings)!).loggingEnabled, true);
});

for (const [label, corrupt] of [
  ["missing wrapped artifact", (c: ReturnType<typeof setup>) => c.real.remove(joinAbs(c.engine, "wrapped.cjs"))],
  ["changed stock hash", (c: ReturnType<typeof setup>) => c.real.write(joinAbs(c.engine, "stock.cjs"), stock + "changed")],
  ["noncanonical wrapped pair", (c: ReturnType<typeof setup>) => {
    const edited = c.wrapped + "// tampered wrapper\n";
    c.real.write(joinAbs(c.engine, "wrapped.cjs"), edited);
    c.real.write(c.policy, JSON.stringify({ ...c.manifest, wrappedSha256: sha(edited) }));
  }],
  ["no native BOX runner", (c: ReturnType<typeof setup>) => {
    const stub = "function createProtoSessionProvider(client) { return client; }\n";
    c.real.write(joinAbs(c.engine, "stock.cjs"), stub);
    c.real.write(c.policy, JSON.stringify({ ...c.manifest, stockSha256: sha(stub) }));
  }],
  ["symlink file", (c: ReturnType<typeof setup>) => { c.real.remove(joinAbs(c.engine, "stock.cjs")); fs.symlinkSync(c.paths.hostMain, joinAbs(c.engine, "stock.cjs")); }],
  ["symlink directory", (c: ReturnType<typeof setup>) => { fs.renameSync(c.engine, c.engine + "-actual"); fs.symlinkSync(c.engine + "-actual", c.engine); }],
  ["oversized manifest", (c: ReturnType<typeof setup>) => c.real.write(c.policy, " ".repeat(16385))],
  ["invalid generation", (c: ReturnType<typeof setup>) => c.real.write(c.policy, JSON.stringify({ ...c.manifest, generationId: "../other" }))],
] as const) {
  test(`pinned selection refuses ${label} before changing controls`, async (t) => {
    const ctx = setup(t); corrupt(ctx);
    const result = await reconcile(ctx.desired, ctx.deps);
    assert.equal(result.kind, "refused");
    if (result.kind === "refused") assert.equal(result.error.kind, "local-engine-refused");
    unchangedControls(ctx);
  });
}

test("missing or disabled pin keeps strict unsupported-stock refusal", async (t) => {
  const ctx = setup(t);
  for (const body of [undefined, JSON.stringify({ ...ctx.manifest, enabled: false })]) {
    if (body === undefined) ctx.real.remove(ctx.policy); else ctx.real.write(ctx.policy, body);
    const result = await reconcile(ctx.desired, ctx.deps);
    assert.equal(result.kind, "refused");
    if (result.kind === "refused") assert.equal(result.error.kind, "census-refused");
    unchangedControls(ctx);
  }
});

test("owned reader refuses other-owner artifacts and directories", { skip: process.getuid?.() !== 0 }, async (t) => {
  const ctx = setup(t);
  const file = joinAbs(ctx.engine, "stock.cjs");
  for (const target of [file, ctx.engine]) {
    fs.chownSync(target, 1, 1);
    const result = await reconcile(ctx.desired, ctx.deps);
    assert.equal(result.kind, "refused");
    unchangedControls(ctx);
    fs.chownSync(target, 0, 0);
  }
});

test("syntax failure leaves original vendor, mode, catalog and snapshots untouched", async (t) => {
  const ctx = setup(t); ctx.syntax(() => ({ ok: false, stderr: "invalid retained engine" }));
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  if (result.kind === "refused") assert.equal(result.error.kind, "syntax-check-failed");
  unchangedControls(ctx);
});

for (const changed of ["manifest", "source", "mode", "plan", "payload"] as const) {
  test(`concurrent ${changed} change during proof refuses without publishing Custom`, async (t) => {
    const ctx = setup(t);
    ctx.syntax(() => {
      if (changed === "manifest") ctx.real.write(ctx.policy, JSON.stringify({ ...ctx.manifest, enabled: false }));
      if (changed === "source") ctx.real.write(ctx.paths.hostMain, vendor + "// upgraded\n");
      if (changed === "mode") ctx.real.write(ctx.paths.mode, "new-owner\n");
      if (changed === "plan") ctx.real.write(ctx.paths.plan, "new-owner catalog");
      if (changed === "payload") { ctx.real.mkdirp(joinAbs(ctx.paths.repoRoot, "payload")); ctx.real.write(joinAbs(ctx.paths.repoRoot, "payload", "runtime.cjs"), "new payload"); }
      return { ok: true };
    });
    const result = await reconcile(ctx.desired, ctx.deps);
    assert.equal(result.kind, "refused");
    assert.equal(ctx.real.read(ctx.paths.plan), changed === "plan" ? "new-owner catalog" : "retained old catalog");
    assert.equal(ctx.real.exists(joinAbs(ctx.paths.sandData, "openbot-official-host.json")), false);
    assert.deepEqual(ctx.stops, []);
  });
}

test("foreign loopback listener prevents engine selection and all control writes", async (t) => {
  const ctx = setup(t); ctx.foreign();
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  if (result.kind === "refused") assert.equal(result.error.kind, "foreign-hop");
  unchangedControls(ctx);
});

test("generation pair is selected only from exact owned generation directory", (t) => {
  const ctx = setup(t);
  const generation = "a".repeat(32);
  const directory = joinAbs(ctx.engine, "generations", generation);
  ctx.real.mkdirp(directory);
  for (const name of ["stock.cjs", "wrapped.cjs"]) ctx.real.copy(joinAbs(ctx.engine, name), joinAbs(directory, name));
  ctx.real.write(ctx.policy, JSON.stringify({ ...ctx.manifest, generationId: generation }));
  assert.match(selectLocalEngine(ctx.deps, "custom", vendor, "ffffffffffffffff")!.source, /openbot-payload ffffffffffffffff/);
});

test("Official never replaces an unrelated fresh stock update with the retained older vendor", async (t) => {
  const ctx = setup(t);
  await reconcile(ctx.desired, ctx.deps);
  const fresh = "// new native vendor\n";
  ctx.real.write(ctx.paths.hostMain, fresh);
  const result = await reconcile(officialBox(ctx.paths), ctx.deps);
  assert.equal(result.kind, "ok");
  assert.equal(ctx.real.read(ctx.paths.hostMain), fresh);
});

test("existing Custom engine can preserve verified upstream vendor capture without a new snapshot", async (t) => {
  const ctx = setup(t);
  ctx.real.write(ctx.paths.hostMain, ctx.wrapped);
  ctx.real.write(ctx.paths.mode, "custom\n");
  const version = "abcdef1";
  const directory = joinAbs(ctx.paths.sandData, "openbot-upstream", "releases", version);
  ctx.real.mkdirp(directory);
  ctx.real.write(joinAbs(directory, "vendor.json"), JSON.stringify({ sourceSha256: sha(vendor) }));
  ctx.real.write(joinAbs(directory, "host-main.cjs"), vendor);
  ctx.real.write(joinAbs(ctx.paths.sandData, "openbot-upstream-sync-state.json"), JSON.stringify({ upstreamVersion: version, upstreamSourceSha256: sha(vendor) }));
  assert.equal((await reconcile(ctx.desired, ctx.deps)).kind, "ok");
  assert.equal(ctx.real.read(joinAbs(ctx.paths.sandData, "openbot-official-host.cjs")), vendor);
  assert.equal((await reconcile(officialBox(ctx.paths), ctx.deps)).kind, "ok");
  assert.equal(ctx.real.read(ctx.paths.hostMain), vendor);
});

test("pre-existing local engine cannot be mistaken for a missing Official snapshot", async (t) => {
  const ctx = setup(t);
  ctx.real.write(ctx.paths.hostMain, ctx.wrapped);
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  if (result.kind === "refused") assert.equal(result.error.kind, "local-engine-refused");
  assert.equal(ctx.real.read(ctx.paths.mode), "official\n");
  assert.equal(ctx.real.read(ctx.paths.plan), "retained old catalog");
  assert.equal(ctx.real.read(ctx.paths.hostMain), ctx.wrapped);
});

test("Official snapshot corruption refuses before changing selected Local mode or catalog", async (t) => {
  const ctx = setup(t);
  await reconcile(ctx.desired, ctx.deps);
  const selected = ctx.real.read(ctx.paths.hostMain);
  const catalog = ctx.real.read(ctx.paths.plan);
  ctx.real.write(joinAbs(ctx.paths.sandData, "openbot-official-host.cjs"), "changed vendor");
  const result = await reconcile(officialBox(ctx.paths), ctx.deps);
  assert.equal(result.kind, "refused");
  assert.equal(ctx.real.read(ctx.paths.hostMain), selected);
  assert.equal(ctx.real.read(ctx.paths.mode), "custom\n");
  assert.equal(ctx.real.read(ctx.paths.plan), catalog);
});


test("missing eager native dependency refuses before publishing Local or changing controls", async (t) => {
  const ctx = setup(t);
  ctx.deps.procs.dependencyCheck = () => ({ ok: false, reason: "Native host dependencies are missing: tree-sitter" });
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  if (result.kind === "refused") {
    assert.equal(result.error.kind, "local-engine-refused");
    if (result.error.kind === "local-engine-refused") assert.match(result.error.reason, /tree-sitter/);
  }
  unchangedControls(ctx);
});


test("readiness is a source-free read-only hint and rechecks current dependency availability", (t) => {
  const ctx = setup(t);
  const filesBefore = fs.readdirSync(ctx.paths.sandData).sort();
  assert.deepEqual(localEngineReadiness(ctx.deps), { state: "ready", nativeDeliveryVerified: false });
  assert.deepEqual(fs.readdirSync(ctx.paths.sandData).sort(), filesBefore);
  unchangedControls(ctx);
  ctx.deps.procs.dependencyCheck = () => ({ ok: false, reason: "missing native dependency" });
  assert.deepEqual(localEngineReadiness(ctx.deps), { state: "unavailable", nativeDeliveryVerified: false });
  ctx.real.remove(ctx.policy);
  assert.deepEqual(localEngineReadiness(ctx.deps), { state: "not-pinned", nativeDeliveryVerified: false });
});

test("readiness cache avoids large source rereads and invalidates on policy metadata; saves never trust cache", async (t) => {
  const ctx = setup(t);
  const owned = ctx.real.readOwned!;
  let largeReads = 0;
  ctx.real.readOwned = function (file, limit, dirs) {
    if (limit > 1024 * 1024) largeReads++;
    return owned.call(ctx.real, file, limit, dirs);
  };
  assert.equal(localEngineReadiness(ctx.deps).state, "ready");
  const first = largeReads;
  assert.equal(localEngineReadiness(ctx.deps).state, "ready");
  assert.equal(largeReads, first);
  // A failed explicit save does its own reads rather than using the UI hint.
  ctx.real.write(joinAbs(ctx.engine, "wrapped.cjs"), "changed source");
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  assert.ok(largeReads > first);
  unchangedControls(ctx);
  ctx.real.write(ctx.policy, JSON.stringify({ ...ctx.manifest, enabled: false }));
  assert.equal(localEngineReadiness(ctx.deps).state, "not-pinned");
});


test("secret and model saves preserve newer post-wrap repairs instead of causing a recovery reload loop", async (t) => {
  const ctx = setup(t);
  assert.equal((await reconcile(ctx.desired, ctx.deps)).kind, "ok");
  const original = ctx.real.read(ctx.paths.hostMain)!;
  // Represent a newer owned injector's idempotent native repair. Its source
  // postdates the retained artifact and must survive ordinary panel saves.
  const patch = (source: string): string => source.includes("/* newer-native-repair */") ? source
    : source.replace("class Box {", "/* newer-native-repair */\nfunction nativeBackupRepair() { return true; }\nclass Box {");
  const repaired = patch(original);
  assert.notEqual(repaired, original);
  ctx.real.write(ctx.paths.hostMain, repaired);
  // Recovery also owns a canonical stock backup. Do not reset it to the
  // independently preserved Official vendor on every ordinary Custom save.
  const repairedBackup = patch(stock);
  ctx.real.write(ctx.paths.knownBackup, repairedBackup);
  const policyBefore = ctx.real.read(ctx.policy);
  const pinBefore = ctx.real.read(joinAbs(ctx.engine, "wrapped.cjs"));
  ctx.stops.length = 0;
  const first = ctx.desired.catalog.models[0]!;
  const alternate = { ...first, id: `${first.id}-alternate` as typeof first.id, slug: "alternate" as typeof first.slug,
    reasoningLevels: ["default", "high", "max"] as const, activeReasoning: "high" as const };
  let catalog: typeof ctx.desired.catalog = { ...ctx.desired.catalog, models: [...ctx.desired.catalog.models, alternate] };
  const providerId = catalog.providers[0]!.id;
  const modelId = alternate.id;
  for (const command of [
    { kind: "set-secret" as const, providerId, secret: "fixture-rotated-key" },
    { kind: "use-model" as const, modelId, reasoning: "max" },
    { kind: "set-secret" as const, providerId, secret: "fixture-second-key" },
  ]) {
    const parsed = parseUiProviderSave(command, ctx.paths, catalog, { mode: "custom" });
    assert.equal(parsed.desired.kind, "custom");
    const result = await reconcile(parsed.desired, ctx.deps, { source: `ui:save:${command.kind}` });
    assert.equal(result.kind, "ok");
    if (result.kind === "ok") {
      assert.equal(result.wrapBytesChanged, false);
      assert.equal(result.hostBounce, "none");
    }
    assert.equal(ctx.real.read(ctx.paths.hostMain), repaired);
    assert.equal(ctx.real.read(ctx.paths.knownBackup), repairedBackup);
    // The next idempotent recovery pass must have nothing to repair/reload.
    assert.equal(patch(ctx.real.read(ctx.paths.hostMain)!), repaired);
    assert.equal(ctx.real.read(ctx.paths.mode), "custom\n");
    if (parsed.desired.kind === "custom") catalog = parsed.desired.catalog;
  }
  assert.deepEqual(ctx.stops, []);
  assert.equal(ctx.real.read(ctx.policy), policyBefore);
  assert.equal(ctx.real.read(joinAbs(ctx.engine, "wrapped.cjs")), pinBefore);
  assert.equal(ctx.real.read(ctx.paths.knownBackup), repairedBackup);
  const saved = JSON.parse(ctx.real.read(ctx.paths.plan)!);
  assert.equal(saved.catalog.bindings.find((b: { conversation: { kind: string } }) => b.conversation.kind === "wildcard").modelId, alternate.id);
  assert.equal(saved.catalog.models.find((m: { id: string }) => m.id === alternate.id).activeReasoning, "max");
  assert.equal((await reconcile(officialBox(ctx.paths), ctx.deps)).kind, "ok");
  assert.equal(ctx.real.read(ctx.paths.hostMain), vendor);
});

for (const [name, edit] of [
  ["foreign runtime", (source: string) => source.replace(/require\([^\n]+\)/, 'require("/foreign/runtime.cjs")')],
  ["partial runner", (source: string) => source.replace("  async sendPrompt(prompt, options2) {", "  async sendPrompt(prompt) {")],
] as const) {
  test(`marked ${name} refuses rather than bypassing pin validation`, async (t) => {
    const ctx = setup(t);
    await reconcile(ctx.desired, ctx.deps);
    const changed = edit(ctx.real.read(ctx.paths.hostMain)!);
    const plan = ctx.real.read(ctx.paths.plan);
    ctx.real.write(ctx.paths.hostMain, changed);
    ctx.stops.length = 0;
    const result = await reconcile(ctx.desired, ctx.deps);
    assert.equal(result.kind, "refused");
    if (result.kind === "refused") assert.equal(result.error.kind, "local-engine-refused");
    assert.equal(ctx.real.read(ctx.paths.hostMain), changed);
    assert.equal(ctx.real.read(ctx.paths.plan), plan);
    assert.equal(ctx.real.read(ctx.paths.mode), "custom\n");
    assert.deepEqual(ctx.stops, []);
  });
}

test("a current complete runner cannot bypass a corrupt retained pin", async (t) => {
  const ctx = setup(t);
  await reconcile(ctx.desired, ctx.deps);
  const current = ctx.real.read(ctx.paths.hostMain);
  ctx.real.write(joinAbs(ctx.engine, "wrapped.cjs"), "corrupt artifact");
  const result = await reconcile(ctx.desired, ctx.deps);
  assert.equal(result.kind, "refused");
  assert.equal(ctx.real.read(ctx.paths.hostMain), current);
});
