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
  if (isZaiGlm53Route(modelId, baseUrl)) {
    return applyZaiGlm53(body, ctx.parameters);
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
  applyProviderReasoningControls: applyProviderReasoningControls,
  isGlmRoute: isGlmRoute,
  isZaiGlm53Route: isZaiGlm53Route,
  isGrokRoute: isGrokRoute,
};
