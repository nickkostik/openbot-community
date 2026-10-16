import assert from "node:assert/strict";
import test from "node:test";
import { documentedOpenAIModel } from "./openai-models.ts";
import { normalizeModel } from "./model.ts";

test("documented OpenAI reasoning distinguishes mandatory and optional GPT-6 models", () => {
  for (const slug of ["gpt-6.1-sol", "gpt-6-astra"]) {
    assert.deepEqual(documentedOpenAIModel(slug)?.reasoningLevels, ["default", "low", "medium", "high", "xhigh", "max"]);
    assert.equal(documentedOpenAIModel(slug)?.contextTokens, 1_050_000);
  }
  assert.ok(documentedOpenAIModel("gpt-6-luna")?.reasoningLevels.includes("none"));
  assert.ok(documentedOpenAIModel("gpt-6-sol")?.reasoningLevels.includes("none"));
  assert.equal(documentedOpenAIModel("gpt-future"), undefined);
});

test("native OpenAI saves correct a legacy effort menu without replacing selected effort or context", () => {
  const raw = { id: "openai:gpt-6.1-sol", providerId: "openai", slug: "gpt-6.1-sol", contextTokens: 258000, reasoningLevels: ["default", "none", "low", "medium", "high"], activeReasoning: "high" };
  const model = normalizeModel(raw)!;
  assert.equal(model.activeReasoning, "high");
  assert.equal(model.contextTokens, 258000);
  assert.equal(model.reasoningLevels.includes("none"), false);
  assert.ok(model.reasoningLevels.includes("xhigh") && model.reasoningLevels.includes("max"));
  assert.equal(normalizeModel({ ...raw, activeReasoning: "max" })?.activeReasoning, "max");
  assert.equal(normalizeModel({ ...raw, activeReasoning: "none" })?.activeReasoning, "default");
  assert.equal(normalizeModel({ ...raw, contextTokens: undefined })?.contextTokens, 1_050_000);
});

test("independent model families retain their vendor effort lists", () => {
  const model = normalizeModel({ id: "another:gpt-6.1-sol", providerId: "another", slug: "gpt-6.1-sol", reasoningLevels: ["default", "low"], activeReasoning: "low" })!;
  assert.deepEqual(model.reasoningLevels, ["default", "low"]);
});
