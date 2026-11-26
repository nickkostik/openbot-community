import assert from "node:assert/strict";
import test from "node:test";
import { catalogFromPlanJson } from "./plan.ts";
import { DEFAULT_CONTEXT_TOKENS, DEFAULT_REASONING_LEVELS } from "../domain/model.ts";
import { HIGH_AGENT_MAX_TOKENS } from "../domain/types.ts";

test("saved Kimi Code Pro routes display documented efforts and keep valid choices", () => {
  const entries = [
    ["kimi", "https://api.kimi.com/coding/v1", "k3.1-pro", "none"],
    ["kimi-2", "https://api.kimi.ai/coding/v1", "k3.1-pro-highspeed", "xhigh"],
    ["other", "https://example.test/v1", "k3.1-pro", "none"],
  ];
  const providers = entries.map(([id, origin]) => ({ id, name: id, origin }));
  const models = entries.map(([providerId, , slug, activeReasoning]) => ({
    id: providerId + ":" + slug, providerId, slug, reasoningLevels: ["default", "none", "high", "max"],
    activeReasoning, contextTokens: 262144, maxOutputTokens: 8192,
  }));
  const raw = JSON.stringify({ kind: "custom", catalog: { providers, models, bindings: [] } });
  const read = catalogFromPlanJson(raw);
  assert.deepEqual(read.models.map(row => row.activeReasoning), ["default", "max", "none"]);
  assert.deepEqual(read.models.map(row => row.reasoningLevels.includes("none")), [false, false, true]);
  assert.ok(read.models.every(row => row.contextTokens === 262144 && row.maxOutputTokens === 8192));
});

test("NVIDIA saved reasoning choices normalize by hosted model without changing its limits", () => {
  const origin = "https://integrate.api.nvidia.com/v1";
  const providers = [{ id: "first", origin }, { id: "second", origin }, { id: "other", origin: "https://example.test/v1" }];
  const models = [
    ["first", "nvidia/nemotron-3.5-bolt-e4b", "medium"],
    ["second", "nvidia/nemotron-3.5-lightning-30b-a3b", "none"],
    ["first", "nvidia/nemotron-3.5-super-vl-120b-a12b", "medium"],
    ["other", "nvidia/nemotron-3.5-bolt-e4b", "medium"],
  ].map(([providerId, slug, activeReasoning]) => ({
    id: providerId + ":" + slug, providerId, slug, activeReasoning,
    reasoningLevels: ["default", "none", "medium"], contextTokens: 256000, maxOutputTokens: 16000,
  }));
  const raw = JSON.stringify({ kind: "custom", catalog: { providers, models, bindings: [] } });
  const read = catalogFromPlanJson(raw);
  assert.deepEqual(read.models.map(row => row.activeReasoning), ["on", "none", "low", "medium"]);
  assert.deepEqual(read.models.map(row => row.reasoningLevels), [
    ["default", "none", "on"], ["default", "none", "on"], ["default", "none", "low", "high"], ["default", "none", "medium"],
  ]);
  assert.ok(read.models.every(row => row.contextTokens === 256000 && row.maxOutputTokens === 16000));
});

test("catalogFromPlanJson fills model limits on old plan JSON", () => {
  const catalog = catalogFromPlanJson(
    JSON.stringify({
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "zhipu",
            name: "Zhipu",
            origin: "https://open.bigmodel.cn/api/paas/v4",
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [{ id: "zhipu:glm", providerId: "zhipu", slug: "glm-5.3-flash", parameters: [] }],
        bindings: [{ conversation: { kind: "wildcard" }, modelId: "zhipu:glm" }],
      },
    }),
  );
  const model = catalog.models[0];
  assert.ok(model);
  assert.equal(model.contextTokens, DEFAULT_CONTEXT_TOKENS);
  assert.equal(model.maxOutputTokens, HIGH_AGENT_MAX_TOKENS);
  assert.deepEqual(model.reasoningLevels, DEFAULT_REASONING_LEVELS);
  assert.equal(model.activeReasoning, "default");
  assert.deepEqual(model.modalities, ["text"]);
});

test("catalogFromPlanJson keeps configured context, output, reasoning, and modalities", () => {
  const catalog = catalogFromPlanJson(
    JSON.stringify({
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "openai",
            name: "OpenAI",
            origin: "https://api.openai.com/v1",
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [
          {
            id: "openai:gpt-4.1",
            providerId: "openai",
            slug: "gpt-4.1",
            contextTokens: 200000,
            maxOutputTokens: 8192,
            reasoningLevels: ["none", "low", "high"],
            activeReasoning: "high",
            modalities: ["text", "image"],
            parameters: [],
          },
        ],
        bindings: [],
      },
    }),
  );
  const model = catalog.models[0];
  assert.ok(model);
  assert.equal(model.contextTokens, 200000);
  assert.equal(model.maxOutputTokens, 8192);
  assert.deepEqual(model.reasoningLevels, ["default", "none", "low", "high"]);
  assert.equal(model.activeReasoning, "high");
  assert.deepEqual(model.modalities, ["text", "image"]);
});

test("catalogFromPlanJson migrates old active none to default", () => {
  const catalog = catalogFromPlanJson(
    JSON.stringify({
      kind: "custom",
      catalog: {
        providers: [
          {
            id: "openai",
            name: "OpenAI",
            origin: "https://api.openai.com/v1",
            maxTokensDefault: 65536,
            mapFile: "provider-maps.cjs",
          },
        ],
        models: [
          {
            id: "openai:gpt-4.1",
            providerId: "openai",
            slug: "gpt-4.1",
            reasoningLevels: ["none", "low", "high"],
            activeReasoning: "none",
            parameters: [],
          },
        ],
        bindings: [],
      },
    }),
  );
  const model = catalog.models[0];
  assert.ok(model);
  assert.deepEqual(model.reasoningLevels, ["default", "none", "low", "high"]);
  assert.equal(model.activeReasoning, "default");
});

test("reading a saved catalog preserves each model protocol", () => {
  for (const apiType of ["responses", "anthropic", "chat-completions"]) {
    const catalog = catalogFromPlanJson(JSON.stringify({kind: "custom", catalog: {providers: [], bindings: [], models: [{id: "go:model", providerId: "go", slug: "model", apiType}]}}));
    assert.equal(catalog.models[0]?.apiType, apiType);
  }
});
