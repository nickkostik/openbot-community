import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const mapsPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "../payload/provider-maps.cjs");
const maps = require(mapsPath) as {
  applyProviderReasoningControls: (
    body: Record<string, unknown>,
    ctx: { modelId: string; baseUrl: string; parameters: readonly { id: string; value: string }[] },
  ) => string;
};

const zaiCodingPlan = "https://api.z.ai/api/coding/paas/v4";

test("direct Z.AI GLM-5.3 uses the selected effort and enables thinking after a switch", () => {
  const cases = [
    ["glm-5.3", zaiCodingPlan, "low"],
    ["glm-5.3-flash", zaiCodingPlan + "/", "high"],
    ["glm-5.3", "https://api.z.ai/api/paas/v4", "max"],
  ] as const;
  for (const [model, baseUrl, effort] of cases) {
    const body = { model, reasoning_effort: "medium", thinking: { type: "disabled", clear_thinking: false } };
    maps.applyProviderReasoningControls(body, {
      modelId: model, baseUrl, parameters: [{ id: "fast", value: "true" }, { id: "effort", value: effort }],
    });
    assert.deepEqual(body, { model, reasoning_effort: effort, thinking: { type: "enabled", clear_thinking: false } });
  }
});

test("direct Z.AI GLM-5.3 drops unsupported inbound efforts and ignores legacy Off", () => {
  const body: Record<string, unknown> = { model: "glm-5.3-flash", reasoning_effort: "none", thinking: { type: "disabled" } };
  maps.applyProviderReasoningControls(body, {
    modelId: "glm-5.3-flash", baseUrl: zaiCodingPlan, parameters: [{ id: "thinking", value: "false" }],
  });
  assert.deepEqual(body, { model: "glm-5.3-flash", thinking: { type: "enabled" } });
});

test("direct Z.AI GLM-5.3 preserves provider defaults and a valid inbound effort", () => {
  const base = { model: "glm-5.3" };
  maps.applyProviderReasoningControls(base, { modelId: base.model, baseUrl: zaiCodingPlan, parameters: [] });
  assert.deepEqual(base, { model: "glm-5.3" });
  const explicit = { model: "glm-5.3", reasoning_effort: "high" };
  maps.applyProviderReasoningControls(explicit, { modelId: explicit.model, baseUrl: zaiCodingPlan, parameters: [] });
  assert.deepEqual(explicit, { model: "glm-5.3", reasoning_effort: "high", thinking: { type: "enabled" } });
});

test("the direct Z.AI GLM-5.3 rule preserves Off for GLM-5.2 and other endpoints", () => {
  const cases = [["glm-5.2", zaiCodingPlan], ["glm-5.3", "https://example.test/v1"]] as const;
  for (const [model, baseUrl] of cases) {
    const body = { model };
    maps.applyProviderReasoningControls(body, {
      modelId: model, baseUrl, parameters: [{ id: "thinking", value: "false" }],
    });
    assert.deepEqual(body, { model, thinking: { type: "disabled" } });
  }
});

const alibabaTokenPlan = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1";

test("Alibaba Token Plan sends its native Off setting for Qwen 3.8 and Qwen4 Max", () => {
  for (const model of ["qwen3.8-max", "qwen3.8-flash", "qwen4-max"]) {
    const body: Record<string, unknown> = {
      model, reasoning_effort: "high", thinking: { type: "enabled" }, enable_thinking: true, thinking_budget: 4096,
    };
    maps.applyProviderReasoningControls(body, {
      modelId: model,
      baseUrl: alibabaTokenPlan,
      parameters: [{ id: "thinking", value: "false" }],
    });
    assert.deepEqual(body, { model, reasoning_effort: "none" });
  }
});

test("Alibaba Token Plan applies selected Qwen effort without a conflicting wire budget", () => {
  for (const [model, effort] of [["qwen3.8-flash", "medium"], ["qwen4-max", "high"]] as const) {
    const body: Record<string, unknown> = {
      model, reasoning_effort: "low", thinking: { type: "disabled" }, enable_thinking: false, thinking_budget: 4096,
    };
    maps.applyProviderReasoningControls(body, {
      modelId: model,
      baseUrl: alibabaTokenPlan + "/",
      parameters: [{ id: "effort", value: effort }],
    });
    assert.deepEqual(body, { model, reasoning_effort: effort });
  }
});

test("Alibaba Token Plan keeps an explicit Qwen budget at the default setting", () => {
  const body: Record<string, unknown> = { model: "qwen3.8-max", thinking_budget: 4096 };
  maps.applyProviderReasoningControls(body, {
    modelId: "qwen3.8-max",
    baseUrl: alibabaTokenPlan,
    parameters: [],
  });
  assert.deepEqual(body, { model: "qwen3.8-max", thinking_budget: 4096 });
});

test("Alibaba Token Plan Qwen mapping does not apply to the same slug at another provider", () => {
  const body: Record<string, unknown> = { model: "qwen3.8-flash" };
  maps.applyProviderReasoningControls(body, {
    modelId: "qwen3.8-flash",
    baseUrl: "https://example.test/v1",
    parameters: [{ id: "thinking", value: "false" }],
  });
  assert.deepEqual(body, { model: "qwen3.8-flash", thinking: { type: "disabled" } });
});

test("GLM maps do not default to fast true", () => {
  const body: Record<string, unknown> = { model: "glm-5.3-flash" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "glm-5.3-flash",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    parameters: [],
  });
  assert.equal(result, "glm-passthrough");
  assert.equal(body.thinking, undefined);
});

test("GLM fast is opt-in through a model parameter, not an installer default", () => {
  const body: Record<string, unknown> = { model: "glm-5.3-flash" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "glm-5.3-flash",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    parameters: [{ id: "fast", value: "true" }],
  });
  assert.equal(result, "glm-fast-off");
  assert.deepEqual(body.thinking, { type: "disabled" });
});

test("GLM effort enables thinking and sets reasoning_effort", () => {
  const body: Record<string, unknown> = { model: "glm-4.6" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "glm-4.6",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    parameters: [{ id: "effort", value: "high" }],
  });
  assert.equal(result, "glm-effort");
  assert.deepEqual(body.thinking, { type: "enabled" });
  assert.equal(body.reasoning_effort, "high");
});

test("GLM thinking=false sets thinking disabled", () => {
  const body: Record<string, unknown> = { model: "glm-5.3-flash" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "glm-5.3-flash",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    parameters: [{ id: "thinking", value: "false" }],
  });
  assert.equal(result, "glm-thinking-off");
  assert.deepEqual(body.thinking, { type: "disabled" });
});

test("Grok effort maps max to xhigh", () => {
  const body: Record<string, unknown> = { model: "grok-3" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "grok-3",
    baseUrl: "https://api.x.ai/v1",
    parameters: [{ id: "effort", value: "max" }],
  });
  assert.equal(result, "grok-effort");
  assert.equal(body.reasoning_effort, "xhigh");
});

test("Grok thinking=false is passthrough without a disable field", () => {
  const body: Record<string, unknown> = { model: "grok-3" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "grok-3",
    baseUrl: "https://api.x.ai/v1",
    parameters: [{ id: "thinking", value: "false" }],
  });
  assert.equal(result, "grok-thinking-off");
  assert.equal(body.thinking, undefined);
  assert.equal(body.reasoning_effort, undefined);
});

test("generic OpenAI maps effort to reasoning_effort", () => {
  const body: Record<string, unknown> = { model: "gpt-4.1" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "gpt-4.1",
    baseUrl: "https://api.openai.com/v1",
    parameters: [{ id: "effort", value: "high" }],
  });
  assert.equal(result, "openai-effort");
  assert.equal(body.reasoning_effort, "high");
});

test("generic OpenAI does not set reasoning_effort without effort", () => {
  const body: Record<string, unknown> = { model: "gpt-4.1" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "gpt-4.1",
    baseUrl: "https://api.openai.com/v1",
    parameters: [],
  });
  assert.equal(result, "none");
  assert.equal(body.reasoning_effort, undefined);
});

test("OpenAI thinking=false uses its supported none effort", () => {
  const body: Record<string, unknown> = { model: "gpt-4.1" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "gpt-4.1",
    baseUrl: "https://api.openai.com/v1",
    parameters: [{ id: "thinking", value: "false" }],
  });
  assert.equal(result, "openai-thinking-off");
  assert.equal(body.reasoning_effort, "none");
  assert.equal(body.thinking, undefined);
});

test("generic OpenAI passes max through as reasoning_effort max", () => {
  const body: Record<string, unknown> = { model: "deepseek-v4-flash" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "deepseek-v4-flash",
    baseUrl: "https://openrouter.ai/api/v1",
    parameters: [{ id: "effort", value: "max" }],
  });
  assert.equal(result, "openai-effort");
  assert.equal(body.reasoning_effort, "max");
});

test("generic OpenAI passes xhigh through as reasoning_effort xhigh", () => {
  const body: Record<string, unknown> = { model: "deepseek-v4-flash" };
  const result = maps.applyProviderReasoningControls(body, {
    modelId: "deepseek-v4-flash",
    baseUrl: "https://openrouter.ai/api/v1",
    parameters: [{ id: "effort", value: "xhigh" }],
  });
  assert.equal(result, "openai-effort");
  assert.equal(body.reasoning_effort, "xhigh");
});
