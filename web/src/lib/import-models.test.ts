import assert from "node:assert/strict";
import test from "node:test";
import { enrichCatalogModels, modelImportFields, modelRefreshFields } from "./import-models.ts";
import type { CatalogLookupModel, FetchedModel, Model } from "../api/types.ts";

const fetched: FetchedModel = { id: "m", name: null, contextLength: null, maxOutputTokens: null, modalities: ["text"], reasoningLevels: [] };
const meta: CatalogLookupModel = { id: "m", name: null, contextLength: 1000000, maxOutputTokens: 393216, modalities: ["text", "image"], outputModalities: ["text"], reasoning: true, reasoningLevels: ["default", "low", "high", "max"], pricing: null, providerKey: "alibaba-token-plan", sourceUrl: "https://models.dev/api.json" };

test("published limits do not expand generation budgets or overwrite a provider's smaller context mode", () => {
  const fields = modelImportFields({ ...fetched, contextLength: 286000 }, meta);
  assert.equal(fields.contextTokens, 286000);
  assert.equal(fields.maxOutputTokens, 65536);
  assert.equal(fields.catalogLimits?.contextTokens, 1000000);
  assert.equal(fields.catalogLimits?.maxOutputTokens, 393216);
});

test("refresh corrects legacy generic limits while preserving custom caps and supported efforts", () => {
  const model = { id: "account:m", providerId: "account", slug: "m", contextTokens: 128000, maxOutputTokens: 12000, activeReasoning: "high", modalities: ["text"], reasoningLevels: ["default", "high"], parameters: [] } as Model;
  assert.equal(modelRefreshFields(model, meta).contextTokens, 1000000);
  assert.equal(modelRefreshFields(model, meta).maxOutputTokens, 12000);
  assert.equal(modelRefreshFields({ ...model, contextTokens: 258000 }, meta).contextTokens, 258000);
  assert.equal(modelRefreshFields(model, { ...meta, maxOutputTokens: 4000 }).maxOutputTokens, 4000);
});

test("catalog enrichment processes more than 40 models with bounded concurrency and keeps the account family", async t => {
  let inflight = 0, maxInflight = 0;
  const calls: URL[] = [];
  t.mock.method(globalThis, "fetch", async (input: string) => {
    calls.push(new URL(input, "https://openbot.test"));
    inflight++; maxInflight = Math.max(maxInflight, inflight);
    await new Promise(resolve => setTimeout(resolve, 1));
    inflight--;
    return new Response(JSON.stringify({ lookup: { found: true, model: meta } }), { status: 200 });
  });
  const models = Array.from({ length: 79 }, (_, i) => ({ ...fetched, id: "model-" + i }));
  const enriched = await enrichCatalogModels(models, "opencode-2");
  assert.equal(enriched.matched.size, 79);
  assert.equal(calls.length, 79);
  assert.ok(maxInflight <= 6);
  assert.ok(calls.every(url => url.searchParams.get("providerId") === "opencode-2"));
});

test("import and metadata refresh carry the route protocol without resetting explicit overrides", () => {
  const responses = { ...meta, apiType: "responses" as const };
  assert.equal(modelImportFields(fetched, responses).apiType, "responses");
  const model = { id: "go:m", providerId: "go", slug: "m", contextTokens: 258000, maxOutputTokens: 12000,
    activeReasoning: "high", modalities: ["text"], reasoningLevels: ["default", "high"], parameters: [] } as Model;
  const refreshed = modelRefreshFields(model, responses);
  assert.equal(refreshed.apiType, "responses");
  assert.equal(refreshed.contextTokens, 258000);
  assert.equal(refreshed.maxOutputTokens, 12000);
  assert.equal(modelRefreshFields({ ...model, apiType: "chat-completions" }, responses).apiType, "chat-completions");
});
