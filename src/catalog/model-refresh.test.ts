import assert from "node:assert/strict";
import test from "node:test";
import type { AbsPath, Provider } from "../domain/types.ts";
import { canDiscoverProvider, createModelRefresh, mergeModelRefresh } from "./model-refresh.ts";
import type { CatalogManager, CatalogModel } from "./model-catalog.ts";
import { nodeFs } from "../supervisor/procs.ts";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const providers = [{ id: "opencode", name: "Go 1", origin: "https://opencode.ai/zen/go/v1", maxTokensDefault: 65536, mapFile: "provider-maps.cjs" },
  { id: "opencode2", name: "Go 2", origin: "https://opencode.ai/zen/go/v1", maxTokensDefault: 65536, mapFile: "provider-maps.cjs" }] as Provider[];
const model = { id: "custom-model-id", providerId: "opencode", slug: "kept", contextTokens: 258000,
  maxOutputTokens: 32000, modalities: ["text"], reasoningLevels: ["default", "high", "max"], activeReasoning: "high", parameters: [{ id: "temperature", value: "0.5" }] };
const raw = JSON.stringify({ kind: "custom", preserve: { unrelated: true }, agents: { "*": { modelId: "kept", providerId: "opencode" } },
  catalog: { providers, models: [model], bindings: [{ conversation: { kind: "wildcard" }, modelId: model.id }] } });
const facts: CatalogModel = { id: "kept", name: null, contextLength: 1050000, maxOutputTokens: 128000,
  modalities: ["text", "image"], outputModalities: ["text"], reasoning: true, reasoningLevels: ["default", "low", "high", "max"], pricing: null,
  providerKey: "opencode-go", sourceUrl: "https://models.dev/api.json" };
const manager = { snapshot: (_id?: string, origin?: string) => ({ status: "ready", lastFetched: null, totalModels: 1, sources: [],
  lookup: origin === providers[0]!.origin ? { found: true, model: facts } : { found: false } }) } as Pick<CatalogManager, "snapshot">;
const discovered = [{ id: "kept", name: null, contextLength: null, maxOutputTokens: null, modalities: ["text"], reasoningLevels: ["default"] },
  { id: "new-model", name: null, contextLength: 200000, maxOutputTokens: 8000, modalities: ["text"], reasoningLevels: ["default"] }];
const lists = providers.map(provider => ({ provider, result: { ok: true as const, models: discovered, skipped: 0, skippedReasons: [] } }));

test("automatic discovery uses Go direct and refuses retired relay and Zen families", () => {
  assert.equal(canDiscoverProvider(providers[0]!), true);
  assert.equal(canDiscoverProvider({ ...providers[0]!, origin: "https://opencode.ai/zen/v1" as Provider["origin"] }), false);
  assert.equal(canDiscoverProvider({ ...providers[0]!, origin: "https://n5.example/v1" as Provider["origin"] }), false);
  assert.equal(canDiscoverProvider({ ...providers[0]!, name: "MetaKey direct" }), false);
});

test("discovery preserves user settings, custom IDs and two independent Go families", () => {
  const out = mergeModelRefresh(raw, lists, manager);
  const saved = JSON.parse(out.raw);
  assert.equal(out.added, 3); // Existing custom ID isn't duplicated within its family.
  assert.equal(out.updated, 1);
  assert.deepEqual(saved.catalog.bindings, JSON.parse(raw).catalog.bindings);
  assert.deepEqual(saved.agents, JSON.parse(raw).agents);
  assert.deepEqual(saved.preserve, { unrelated: true });
  assert.deepEqual(saved.catalog.providers, providers);
  const kept = saved.catalog.models[0];
  assert.equal(kept.contextTokens, 258000); assert.equal(kept.maxOutputTokens, 32000); assert.equal(kept.activeReasoning, "high");
  assert.deepEqual(kept.parameters, model.parameters);
  assert.equal(kept.catalogLimits.contextTokens, 1050000);
  assert.equal(saved.catalog.models.find((m: { id: string }) => m.id === "opencode:new-model").contextTokens, 200000);
  assert.ok(saved.catalog.models.some((m: { id: string }) => m.id === "opencode2:new-model"));
  assert.equal(mergeModelRefresh(out.raw, lists, manager).raw, out.raw);
});

test("a refresh retains existing and per-bot effort selections when the source removes a level", () => {
  const narrowed: Pick<CatalogManager, "snapshot"> = { snapshot: () => ({ ...manager.snapshot(), lookup: { found: true, model: { ...facts, reasoningLevels: ["default", "high"] } } }) };
  const out = mergeModelRefresh(raw, lists, narrowed, JSON.stringify({ reasoning: { Q: { [model.id]: "max" } } }));
  assert.equal(out.reasoningHeld, 1);
  assert.deepEqual(JSON.parse(out.raw).catalog.models[0].reasoningLevels, model.reasoningLevels);
  assert.equal(mergeModelRefresh(raw, lists, narrowed, "invalid json").reasoningHeld, 1);
});

test("outages and provider edits never delete or add stale routes", () => {
  const failed = lists.map(list => ({ provider: list.provider, result: { ok: false as const, errorKind: "unreachable" as const, message: "offline" } }));
  const out = mergeModelRefresh(raw, failed, manager);
  assert.equal(out.added, 0); assert.equal(JSON.parse(out.raw).catalog.models.length, 1);
  const edited = JSON.parse(raw); edited.catalog.providers[0].origin = "https://new.example/v1";
  const next = mergeModelRefresh(JSON.stringify(edited), [lists[0]!], manager);
  assert.equal(next.updated, 0); assert.equal(next.added, 0);
  const unknown = { snapshot: () => ({ lookup: { found: false } }) } as Pick<CatalogManager, "snapshot">;
  assert.equal(mergeModelRefresh(raw, failed, unknown).raw, raw);
});

test("Go per-model Responses metadata outranks the family default for both accounts", () => {
  const input = JSON.parse(raw);
  input.catalog.providers = input.catalog.providers.map((provider: Provider) => ({ ...provider, apiType: "chat-completions" }));
  input.catalog.models.push({ ...model, id: "explicit-chat", slug: "explicit", apiType: "chat-completions" });
  const responses = { snapshot: () => ({ ...manager.snapshot(), lookup: { found: true, model: { ...facts, apiType: "responses" } } }) } as Pick<CatalogManager, "snapshot">;
  const out = mergeModelRefresh(JSON.stringify(input), lists, responses);
  const saved = JSON.parse(out.raw);
  assert.equal(saved.catalog.models.find((m: { id: string }) => m.id === model.id).apiType, "responses");
  assert.equal(saved.catalog.models.find((m: { id: string }) => m.id === "explicit-chat").apiType, "chat-completions");
  for (const id of ["opencode:new-model", "opencode2:kept", "opencode2:new-model"]) {
    assert.equal(saved.catalog.models.find((m: { id: string }) => m.id === id).apiType, "responses");
  }
  assert.deepEqual(saved.catalog.bindings, input.catalog.bindings);
  assert.deepEqual(saved.agents, input.agents);
});

test("automatic discovery is bounded, durable across restarts and merges a concurrent panel selection", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-refresh-"));
  try {
    const sd = dir as AbsPath, planPath = path.join(dir, "openbot-plan.json") as AbsPath;
    const secretsPath = path.join(dir, "secrets.json") as AbsPath;
    writeFileSync(planPath, raw); writeFileSync(secretsPath, JSON.stringify({ providers: { opencode: "first-secret", opencode2: "second-secret" } }));
    let clock = Date.parse("2026-10-07T00:00:00Z"), active = 0, peak = 0, calls = 0;
    let release!: () => void;
    const wait = new Promise<void>(resolve => { release = resolve; });
    const deps = { fs: nodeFs(), sandData: sd, planPath, secretsPath, manager: { ...manager,
      start: async () => {}, refresh: () => ({ startedAt: new Date(clock).toISOString(), done: Promise.resolve() }) } as CatalogManager,
      enqueue: async <T>(work: () => Promise<T>) => work(), now: () => clock,
      fetchProvider: async () => { calls++; peak = Math.max(peak, ++active); await wait; active--; return { status: 200, body: { ok: true, models: discovered } }; } };
    const controller = createModelRefresh(deps);
    const running = controller.check();
    await new Promise(resolve => setImmediate(resolve));
    const selected = JSON.parse(raw); selected.catalog.bindings[0].modelId = "chosen-during-refresh";
    selected.catalog.models[0].activeReasoning = "max";
    writeFileSync(planPath, JSON.stringify(selected)); release();
    assert.equal((await running).state, "completed");
    assert.equal(calls, 2); assert.equal(peak, 2);
    const saved = JSON.parse(readFileSync(planPath, "utf8"));
    assert.equal(saved.catalog.bindings[0].modelId, "chosen-during-refresh");
    assert.equal(saved.catalog.models[0].activeReasoning, "max");
    const restarted = createModelRefresh(deps); await restarted.check(); assert.equal(calls, 2);
    clock += 6 * 3600000 + 1; await restarted.check(); assert.equal(calls, 4);
    writeFileSync(path.join(dir, "openbot-model-refresh.json"), '{"enabled":false}');
    assert.equal((await restarted.run()).state, "disabled"); assert.equal(calls, 4);
    assert.ok(!readFileSync(path.join(dir, "openbot-model-refresh-status.json"), "utf8").includes("secret"));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
