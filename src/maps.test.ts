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
    ctx: { modelId: string; baseUrl: string; apiType?: string; parameters: readonly { id: string; value: string }[] },
  ) => string;
};

test("Kimi Code Pro drops inherited Off and maps explicit allowed reasoning", () => {
  const output = { effort: "none", format: { type: "json_schema" } };
  const body = {
    model: "k3.1-pro-highspeed", reasoning_effort: "none", thinking: { type: "disabled" },
    enable_thinking: false, output_config: output,
  };
  for (const baseUrl of ["https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/"]) {
    const ctx = { modelId: body.model, baseUrl, parameters: [{ id: "thinking", value: "false" }] };
    const fallback = { ...body };
    maps.applyProviderReasoningControls(fallback, ctx);
    assert.deepEqual(fallback, { model: body.model, output_config: { format: output.format } });
    const selected = { ...body };
    maps.applyProviderReasoningControls(selected, { ...ctx, parameters: [{ id: "effort", value: "xhigh" }] });
    assert.equal(selected.reasoning_effort, "max");
    assert.equal(selected.thinking, undefined);
    assert.equal(selected.enable_thinking, undefined);
    assert.equal(output.effort, "none");
  }
  for (const [modelId, baseUrl] of [["k3", "https://api.kimi.com/coding/v1"], [body.model, "https://other.example/v1"]] as const) {
    const other = { model: modelId, thinking: { type: "enabled" } };
    maps.applyProviderReasoningControls(other, { modelId, baseUrl, parameters: [{ id: "thinking", value: "false" }] });
    assert.deepEqual(other.thinking, { type: "disabled" });
  }
});

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

const alibabaTokenPlan = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1";
const legacyAlibabaTokenPlan = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1";

test("direct Nemotron Bolt and Lightning use the native thinking switch", () => {
  const baseUrl = "https://integrate.api.nvidia.com/v1";
  for (const modelId of ["nvidia/nemotron-3.5-bolt-e4b", "nvidia/nemotron-3.5-lightning-30b-a3b"]) {
    const template = { enable_thinking: true, reasoning_budget: 256 };
    const request = { model: modelId, reasoning_effort: "high", thinking: { type: "enabled" }, chat_template_kwargs: template };
    for (const [id, value, expected] of [
      ["thinking", "false", false], ["effort", "on", true], ["effort", "medium", true],
    ] as const) {
      const body = { ...request };
      maps.applyProviderReasoningControls(body, { modelId, baseUrl, parameters: [{ id, value }] });
      assert.deepEqual(body, { model: modelId, chat_template_kwargs: { enable_thinking: expected, reasoning_budget: 256 } });
    }
    assert.deepEqual(template, { enable_thinking: true, reasoning_budget: 256 });
    const defaultBody = { model: modelId };
    maps.applyProviderReasoningControls(defaultBody, { modelId, baseUrl, parameters: [] });
    assert.deepEqual(defaultBody, { model: modelId });
  }
});

test("direct Nemotron Super VL only sends its supported reasoning effort", () => {
  const modelId = "nvidia/nemotron-3.5-super-vl-120b-a12b";
  for (const [id, value, expected] of [
    ["thinking", "false", "none"], ["effort", "low", "low"],
    ["effort", "high", "high"], ["effort", "medium", "low"],
  ] as const) {
    const body = {
      model: modelId, enable_thinking: false, reasoning: { effort: "medium" },
      chat_template_kwargs: { enable_thinking: false, preserve_thinking: true },
    };
    maps.applyProviderReasoningControls(body, {
      modelId, baseUrl: "https://integrate.api.nvidia.com/", parameters: [{ id, value }],
    });
    assert.deepEqual(body, {
      model: modelId, reasoning_effort: expected, chat_template_kwargs: { preserve_thinking: true },
    });
  }
  const external = { model: modelId };
  maps.applyProviderReasoningControls(external, {
    modelId, baseUrl: "https://example.test/v1", parameters: [{ id: "thinking", value: "false" }],
  });
  assert.deepEqual(external, { model: modelId, thinking: { type: "disabled" } });
});

test("hosted Lightning caps output at 32768 even at the default reasoning setting", () => {
  const modelId = "nvidia/nemotron-3.5-lightning-30b-a3b";
  for (const [limit, expected] of [[65536, 32768], [8192, 8192]]) {
    const body = { model: modelId, max_tokens: limit };
    maps.applyProviderReasoningControls(body, { modelId, baseUrl: "https://integrate.api.nvidia.com/v1", parameters: [] });
    assert.equal(body.max_tokens, expected);
  }
});

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

test("Alibaba Token Plan applies selected Qwen effort on both direct hostnames", () => {
  for (const [model, effort, origin] of [
    ["qwen3.8-flash", "medium", alibabaTokenPlan],
    ["qwen4-max", "high", alibabaTokenPlan],
    ["qwen4-max", "none", legacyAlibabaTokenPlan],
    ["qwen4-max", "high", legacyAlibabaTokenPlan],
  ] as const) {
    const body: Record<string, unknown> = {
      model, reasoning_effort: "low", thinking: { type: "disabled" }, enable_thinking: false, thinking_budget: 4096,
    };
    maps.applyProviderReasoningControls(body, {
      modelId: model,
      baseUrl: origin + "/",
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

test("Alibaba Token Plan GLM-5.3 uses native thinking and effort fields", () => {
  for (const baseUrl of [alibabaTokenPlan, legacyAlibabaTokenPlan + "/"]) {
    for (const [selected, incoming, expected] of [
      ["low", "max", "low"], ["high", "low", "high"], ["max", "high", "max"],
      ["default", "high", "high"], ["default", "medium", undefined], ["none", "none", undefined],
    ] as const) {
      const body: Record<string, unknown> = {
        model: "glm-5.3", enable_thinking: false, reasoning_effort: incoming, thinking_budget: 4096,
        thinking: { type: "disabled", clear_thinking: true }, clear_thinking: false,
      };
      maps.applyProviderReasoningControls(body, {
        modelId: "glm-5.3", baseUrl, apiType: "chat-completions",
        parameters: [{ id: "fast", value: "true" }, { id: "effort", value: selected }],
      });
      assert.deepEqual(body, { model: "glm-5.3", enable_thinking: true, clear_thinking: false,
        ...(expected ? { reasoning_effort: expected } : {}) });
    }
  }
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

test("direct DeepSeek replaces stale controls with the saved thinking mode and effort", () => {
  const format = { type: "json_schema" };
  const output = { format, effort: "low" };
  for (const apiType of ["chat-completions", "anthropic", "responses"]) {
    for (const [id, value, effort] of [
      ["thinking", "false", "none"], ["effort", "max", "max"],
      ["effort", "minimal", "low"], ["effort", "xhigh", "high"], ["effort", "ultra", "max"],
    ] as const) {
      const model = "deepseek-flash";
      const body = {
        model, thinking: { type: "disabled" }, enable_thinking: false, thinking_budget: 256,
        reasoning: { effort: "none" }, reasoning_effort: "low", output_config: output,
      };
      const applied = maps.applyProviderReasoningControls(body, {
        modelId: model, baseUrl: "https://api.deepseek.com/v1", apiType, parameters: [{ id, value }],
      });
      assert.equal(applied, "deepseek-v4");
      const expected = { model, output_config: { format } };
      assert.deepEqual(body, apiType === "responses" ? { ...expected, reasoning_effort: effort }
        : effort === "none" ? { ...expected, thinking: { type: "disabled" } }
        : { ...expected, thinking: { type: "enabled" }, reasoning_effort: effort });
    }
  }
  assert.deepEqual(output, { format, effort: "low" });
});

test("direct DeepSeek preserves the provider default and honors an explicitly disabled caller", () => {
  for (const [apiType, incoming, expected] of [
    ["responses", {}, {}],
    ["responses", { thinking: { type: "disabled" }, reasoning_effort: "high" }, { reasoning_effort: "none" }],
    ["responses", { reasoning: { effort: "none" } }, { reasoning_effort: "none" }],
    ["anthropic", { output_config: { effort: "medium" } }, { thinking: { type: "enabled" }, reasoning_effort: "high" }],
    ["chat-completions", { thinking: { type: "enabled" } }, { thinking: { type: "enabled" } }],
  ] as const) {
    const model = "deepseek-v4-pro";
    const body = { model, ...incoming };
    maps.applyProviderReasoningControls(body, {
      modelId: model, baseUrl: "https://api.deepseek.com/anthropic/v1", apiType, parameters: [],
    });
    assert.deepEqual(body, { model, ...expected });
  }
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
