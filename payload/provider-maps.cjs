"use strict";

function param(parameters, id) {
  if (!Array.isArray(parameters)) return undefined;
  for (var i = 0; i < parameters.length; i++) {
    var p = parameters[i];
    if (p && p.id === id) return p.value;
  }
  return undefined;
}

var GLM_MODEL_RE = /^glm[-.\d]/i;
var GLM_BASE_RE = /bigmodel\.cn/;
var GROK_MODEL_RE = /^grok[-.]/i;
var ZAI_DIRECT_RE = /^https:\/\/api\.z\.ai\/api\/(?:coding\/)?paas\/v4\/?$/i;
var ZAI_GLM53_RE = /^glm-5\.3(?:-flash)?$/i;
var ZAI_GLM53_EFFORT_RE = /^(?:low|high|max)$/;
var ALIBABA_TOKEN_PLAN_CHAT_ORIGINS = new Set([
  "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1",
  "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
]);
var ALIBABA_QWEN_REASONING_RE = /^(?:qwen4-max|qwen3\.8-(?:max(?:-0902)?|flash|2\.4t-a95b|27b))$/i;
var ALIBABA_QWEN_EFFORT_RE = /^(?:none|minimal|low|medium|high|xhigh|max)$/;
var DEEPSEEK_ORIGIN = "https://api.deepseek.com";
var DEEPSEEK_V4_RE = /^(?:deepseek-flash|deepseek-v4-(?:pro|flash(?:-vision-exp)?))$/;
var DEEPSEEK_V4 = Object.freeze({
  kind: "deepseek-v4",
  levels: Object.freeze(["default", "none", "low", "high", "max"]),
  aliases: Object.freeze({ minimal: "low", medium: "high", xhigh: "high", ultra: "max" }),
});
var KIMI_CODE_PRO = Object.freeze({
  kind: "kimi-code-pro",
  levels: Object.freeze(["default", "low", "high", "max"]),
  aliases: Object.freeze({ minimum: "low", light: "low", medium: "high", xhigh: "max", ultra: "max" }),
});
var NVIDIA_NEMOTRON_TOGGLE = Object.freeze({
  kind: "nvidia-nemotron-toggle",
  apiType: "chat-completions",
  levels: Object.freeze(["default", "none", "on"]),
  aliases: Object.freeze({ minimal: "on", low: "on", medium: "on", high: "on", xhigh: "on", max: "on" }),
});
var NVIDIA_LIGHTNING = Object.freeze(Object.assign({}, NVIDIA_NEMOTRON_TOGGLE, { maxOutputTokens: 32768 }));
var NVIDIA_DIRECT = /^https:\/\/integrate\.api\.nvidia\.com(?:\/v1)?\/*$/i;
var NVIDIA_SUPER_VL = Object.freeze({
  kind: "nvidia-super-vl",
  apiType: "chat-completions",
  levels: Object.freeze(["default", "none", "low", "high"]),
  aliases: Object.freeze({ minimal: "low", medium: "low", on: "high", xhigh: "high", max: "high" }),
});

function documentedReasoningEffort(policy, value) {
  if (typeof value !== "string") return undefined;
  if (policy.levels.includes(value)) return value;
  return Object.hasOwn(policy.aliases, value) ? policy.aliases[value] : undefined;
}

function isDirectDeepSeekOrigin(baseUrl) {
  return /^https:\/\/api\.deepseek\.com(?::443)?(?:\/(?:v1(?:\/(?:chat\/completions|responses))?|chat\/completions|responses|anthropic(?:\/v1(?:\/messages)?)?))?\/*$/i.test(baseUrl);
}

// Source: NVIDIA's retrieval API reference and model cards.
function nvidiaEmbeddingInputs(modelId, baseUrl) {
  if (!NVIDIA_DIRECT.test(baseUrl)) return undefined;
  switch (modelId) {
    case "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1":
    case "nvidia/llama-nemotron-embed-vl-1b-v2":
    case "nvidia/nemotron-3.5-embed-1b":
    case "nvidia/nemotron-3.5-embed-8b":
    case "nvidia/nvclip":
      return ["text", "image"];
    case "nvidia/embed-qa-4":
    case "nvidia/llama-3.2-nv-embedqa-1b-v1":
    case "nvidia/nemotron-3-embed-1b":
    case "nvidia/nv-embedqa-mistral-7b-v2":
    case "snowflake/arctic-embed-l":
      return ["text"];
  }
}

function documentedReasoningPolicy(modelId, baseUrl) {
  if (isDirectDeepSeekOrigin(baseUrl) && DEEPSEEK_V4_RE.test(modelId)) return DEEPSEEK_V4;
  if (/^https:\/\/api\.kimi\.(?:com|ai)\/coding(?:\/v1)?\/*$/i.test(baseUrl) && /^k3\.1-pro(?:-highspeed)?$/.test(modelId)) {
    return KIMI_CODE_PRO;
  }
  if (!NVIDIA_DIRECT.test(baseUrl)) return undefined;
  if (modelId === "nvidia/nemotron-3.5-super-vl-120b-a12b") return NVIDIA_SUPER_VL;
  if (modelId === "nvidia/nemotron-3.5-lightning-30b-a3b") return NVIDIA_LIGHTNING;
  if (modelId === "nvidia/nemotron-3.5-bolt-e4b") return NVIDIA_NEMOTRON_TOGGLE;
}

function isGlmRoute(modelId, baseUrl) {
  return GLM_MODEL_RE.test(String(modelId || "")) || GLM_BASE_RE.test(String(baseUrl || ""));
}

function isZaiGlm53Route(modelId, baseUrl) {
  return ZAI_DIRECT_RE.test(String(baseUrl || "")) && ZAI_GLM53_RE.test(String(modelId || ""));
}

function isGrokRoute(modelId, baseUrl) {
  return GROK_MODEL_RE.test(String(modelId || "")) || /api\.x\.ai/.test(String(baseUrl || ""));
}

function thinkingOff(body) {
  body.thinking = { type: "disabled" };
}

function applyDeepSeekV4(body, parameters, apiType) {
  var thinking = param(parameters, "thinking");
  var effort = thinking === false || thinking === "false" ? "none"
    : documentedReasoningEffort(DEEPSEEK_V4, param(parameters, "effort"));
  var incomingThinking = body.thinking?.type;
  var output = body.output_config;
  if (!effort || effort === "default") {
    var nativeEffort = apiType === "responses" ? body.reasoning?.effort : apiType === "anthropic" ? output?.effort : undefined;
    effort = incomingThinking === "disabled" ? "none"
      : documentedReasoningEffort(DEEPSEEK_V4, nativeEffort)
        || documentedReasoningEffort(DEEPSEEK_V4, body.reasoning_effort);
    if (effort === "default" || (incomingThinking === "enabled" && effort === "none")) effort = undefined;
  }

  // The saved choice wins over thinking and effort fields from a previous model.
  delete body.thinking;
  delete body.thinking_budget;
  delete body.enable_thinking;
  delete body.reasoning;
  delete body.reasoning_effort;
  if (output && Object.hasOwn(output, "effort")) {
    output = Object.assign({}, output);
    delete output.effort;
    if (Object.keys(output).length) body.output_config = output;
    else delete body.output_config;
  }

  if (apiType === "responses") {
    if (effort) body.reasoning_effort = effort;
  } else if (effort === "none") {
    thinkingOff(body);
  } else if (effort || incomingThinking === "enabled") {
    body.thinking = { type: "enabled" };
    if (effort) body.reasoning_effort = effort;
  }
  return DEEPSEEK_V4.kind;
}

function applyKimiCodePro(body, parameters) {
  var effort = documentedReasoningEffort(KIMI_CODE_PRO, param(parameters, "effort"))
    || documentedReasoningEffort(KIMI_CODE_PRO, body.reasoning_effort);
  if (effort && effort !== "default") body.reasoning_effort = effort;
  else delete body.reasoning_effort;

  // Both variants require thinking. Removing an unsupported control lets
  // Kimi apply its default without changing the saved preference.
  if (body.thinking && body.thinking.type === "disabled") delete body.thinking;
  if (body.enable_thinking === false) delete body.enable_thinking;
  if (body.output_config && typeof body.output_config === "object" && !Array.isArray(body.output_config)) {
    var outputEffort = documentedReasoningEffort(KIMI_CODE_PRO, body.output_config.effort);
    if (outputEffort === "default") outputEffort = undefined;
    var output = body.output_config;
    if (outputEffort !== body.output_config.effort) {
      output = Object.assign({}, body.output_config);
      if (outputEffort) output.effort = outputEffort;
      else delete output.effort;
    }
    if (Object.keys(output).length) body.output_config = output;
    else delete body.output_config;
  }
  return body.reasoning_effort === undefined ? "kimi-code-pro-default" : "kimi-code-pro-effort";
}

function applyNvidiaNemotron(body, parameters, policy) {
  if (policy.maxOutputTokens && body.max_tokens > policy.maxOutputTokens) body.max_tokens = policy.maxOutputTokens;
  var thinking = param(parameters, "thinking");
  var effort = thinking === false || thinking === "false" ? "none"
    : documentedReasoningEffort(policy, param(parameters, "effort"))
      || documentedReasoningEffort(policy, body.reasoning_effort);

  // Direct Nemotron uses a template switch or a documented Super VL enum.
  // Remove controls carried over from another protocol before applying it.
  delete body.thinking;
  delete body.enable_thinking;
  delete body.reasoning;
  delete body.reasoning_effort;
  if (!effort || effort === "default") return policy.kind;

  var previous = body.chat_template_kwargs;
  var options = previous && typeof previous === "object" && !Array.isArray(previous)
    ? Object.assign({}, previous) : {};
  delete options.reasoning_effort;
  if (policy.kind === "nvidia-nemotron-toggle") {
    options.enable_thinking = effort === "on";
  } else {
    delete options.enable_thinking;
    body.reasoning_effort = effort;
  }
  if (Object.keys(options).length) body.chat_template_kwargs = options;
  else delete body.chat_template_kwargs;
  return policy.kind;
}

function applyZaiGlm53(body, parameters) {
  var effort = String(param(parameters, "effort"));
  if (ZAI_GLM53_EFFORT_RE.test(effort)) body.reasoning_effort = effort;
  if (!ZAI_GLM53_EFFORT_RE.test(String(body.reasoning_effort))) delete body.reasoning_effort;

  // GLM-5.3 always thinks on Z.AI. Keep supported thinking options while
  // replacing a disabled flag carried over from another model or a legacy setting.
  if (body.thinking !== undefined || body.reasoning_effort !== undefined) {
    var thinking = body.thinking;
    var options = thinking && typeof thinking === "object" && !Array.isArray(thinking) ? thinking : {};
    body.thinking = Object.assign({}, options, { type: "enabled" });
  }
  return body.reasoning_effort === undefined ? "zai-glm53-default" : "zai-glm53-effort";
}

function applyAlibabaQwen(body, parameters) {
  var thinking = param(parameters, "thinking");
  var effort = thinking === false || String(thinking).toLowerCase() === "false"
    ? "none"
    : param(parameters, "effort");
  if (!ALIBABA_QWEN_EFFORT_RE.test(String(effort))) return "alibaba-qwen-passthrough";

  // An explicit selection replaces any thinking controls on the incoming
  // request. These models reject a budget alongside reasoning_effort.
  body.reasoning_effort = String(effort);
  delete body.thinking_budget;
  delete body.enable_thinking;
  delete body.thinking;
  return "alibaba-qwen-effort";
}

function applyGlm(body, parameters) {
  var fast = param(parameters, "fast");
  if (fast === true || String(fast).toLowerCase() === "true") {
    thinkingOff(body);
    return "glm-fast-off";
  }
  var thinking = param(parameters, "thinking");
  if (thinking === false || String(thinking).toLowerCase() === "false") {
    thinkingOff(body);
    return "glm-thinking-off";
  }
  var effort = param(parameters, "effort");
  var glmEffort = { low: "low", medium: "medium", high: "high", xhigh: "max", max: "max" };
  var token = effort != null ? glmEffort[String(effort)] : undefined;
  if (token) {
    if (!body.thinking) body.thinking = { type: "enabled" };
    if (body.reasoning_effort == null) body.reasoning_effort = token;
    return "glm-effort";
  }
  return null;
}

function applyGrok(body, maxMode, parameters) {
  var effortToXai = { low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "xhigh" };
  if (maxMode === true) {
    body.reasoning_effort = "xhigh";
    return "grok-max";
  }
  var fast = param(parameters, "fast");
  if (fast === true || String(fast).toLowerCase() === "true") {
    body.reasoning_effort = "low";
    return "grok-fast";
  }
  var thinking = param(parameters, "thinking");
  if (thinking === false || String(thinking).toLowerCase() === "false") {
    return "grok-thinking-off";
  }
  var effort = param(parameters, "effort");
  if (effort != null && Object.prototype.hasOwnProperty.call(effortToXai, String(effort))) {
    body.reasoning_effort = effortToXai[String(effort)];
    return "grok-effort";
  }
  return null;
}

function applyGeneric(body, parameters, openaiRoute) {
  var thinking = param(parameters, "thinking");
  if (thinking === false || String(thinking).toLowerCase() === "false") {
    if (openaiRoute) {
      body.reasoning_effort = "none";
      return "openai-thinking-off";
    }
    thinkingOff(body);
    return "openai-thinking-off";
  }
  var effort = param(parameters, "effort");
  var openaiEffort = { minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max" };
  var token = effort != null ? openaiEffort[String(effort)] : undefined;
  if (token && body.reasoning_effort == null) {
    body.reasoning_effort = token;
    return "openai-effort";
  }
  return "none";
}

function applyProviderReasoningControls(body, ctx) {
  ctx = ctx || {};
  var modelId = String(ctx.modelId || "");
  var baseUrl = String(ctx.baseUrl || "");
  var documented = documentedReasoningPolicy(modelId, baseUrl);
  if (documented === DEEPSEEK_V4) return applyDeepSeekV4(body, ctx.parameters, ctx.apiType);
  if (documented === KIMI_CODE_PRO) {
    return applyKimiCodePro(body, ctx.parameters);
  }
  if (documented) return applyNvidiaNemotron(body, ctx.parameters, documented);
  if (isZaiGlm53Route(modelId, baseUrl)) {
    return applyZaiGlm53(body, ctx.parameters);
  }
  if (ALIBABA_TOKEN_PLAN_CHAT_ORIGINS.has(baseUrl.replace(/\/$/, "")) && ALIBABA_QWEN_REASONING_RE.test(modelId)) {
    return applyAlibabaQwen(body, ctx.parameters);
  }
  if (isGrokRoute(modelId, baseUrl)) {
    return applyGrok(body, ctx.maxMode === true, ctx.parameters) || "grok-passthrough";
  }
  if (isGlmRoute(modelId, baseUrl)) {
    return applyGlm(body, ctx.parameters) || "glm-passthrough";
  }
  return applyGeneric(body, ctx.parameters, /(^|[/:])(gpt[-.]|o[1-9]([- .]|$))/i.test(modelId) || /api\.openai\.com|chatgpt\.com/i.test(baseUrl));
}

module.exports = {
  DEEPSEEK_ORIGIN: DEEPSEEK_ORIGIN,
  isDirectDeepSeekOrigin: isDirectDeepSeekOrigin,
  nvidiaEmbeddingInputs: nvidiaEmbeddingInputs,
  documentedReasoningPolicy: documentedReasoningPolicy,
  documentedReasoningEffort: documentedReasoningEffort,
  applyProviderReasoningControls: applyProviderReasoningControls,
  isGlmRoute: isGlmRoute,
  isZaiGlm53Route: isZaiGlm53Route,
  isGrokRoute: isGrokRoute,
};
