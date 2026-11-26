"use strict";

var fs = require("fs");
var path = require("path");
var requestLog = require("./request-log.cjs");
var { managedService } = require("./version.cjs");
var { nvidiaEmbeddingInputs } = require("./provider-maps.cjs");

function botModelsPath() {
  if (managedService) return path.join(managedService.sandData, "openbot-bot-models.json");
  if (process.env.OPENBOT_BOT_MODELS) return process.env.OPENBOT_BOT_MODELS;
  var root = process.env.OPENBOT_SAND_DATA || (process.env.OPENBOT_PLAN ? path.dirname(process.env.OPENBOT_PLAN) : "/home/box/sand-data");
  return path.join(root, "openbot-bot-models.json");
}

// Read on every request (no cache): an assignment edit takes effect on the
// next message without a host bounce, mirroring the plan reload discipline.
// Missing or corrupt file = no assignments (fail open, never wedge chat).
function readBotSettings() {
  try {
    var parsed = JSON.parse(fs.readFileSync(botModelsPath(), "utf8"));
    var assignments = parsed && parsed.assignments;
    var out = {};
    Object.keys(assignments && typeof assignments === "object" ? assignments : {}).forEach(function (botId) {
      if (typeof assignments[botId] === "string" && assignments[botId].trim()) out[botId] = assignments[botId];
    });
    return { assignments: out, reasoning: parsed && parsed.reasoning && typeof parsed.reasoning === "object" ? parsed.reasoning : {} };
  } catch (err) {
    return { assignments: {}, reasoning: {} };
  }
}

function readBotModels() { return readBotSettings().assignments; }

function writeBotModels(assignments) {
  var file = botModelsPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  var tmp = file + ".tmp";
  var saved = {};
  try { saved = JSON.parse(fs.readFileSync(file, "utf8")); } catch (err) {}
  fs.writeFileSync(tmp, JSON.stringify(Object.assign({}, saved, { assignments: assignments }), null, 2) + "\n", "utf8");
  fs.renameSync(tmp, file);
}

function extractBotId(messages) {
  var context = requestLog.extractChatContext(messages);
  return context && typeof context.botId === "string" && context.botId ? context.botId : undefined;
}

function modelSupportsChat(model, origin) {
  return (model.modelType === undefined || model.modelType === "chat") && !nvidiaEmbeddingInputs(model.slug, origin);
}

function modelInPlan(plan, modelId) {
  var catalog = plan && plan.catalog;
  var models = catalog && catalog.models;
  var providers = catalog && Array.isArray(catalog.providers) ? catalog.providers : [];
  if (!Array.isArray(models)) return false;
  for (var i = 0; i < models.length; i++) {
    var row = models[i];
    if (!row || (row.id !== modelId && row.slug !== modelId)) continue;
    var provider = providers.find(function (p) { return p && p.id === row.providerId; });
    if (modelSupportsChat(row, provider && provider.origin)) return true;
  }
  return false;
}

// Resolve the per-bot override for one request. Returns undefined when the
// messages carry no bot identity or no assignment exists. When the assignment
// points at a model unavailable for chat, the result is
// marked stale so callers fall back to the global model instead of failing
// the chat. The API also validates new assignments on save.
function resolveAssignment(messages, plan, settings) {
  var botId = extractBotId(messages);
  if (!botId) return undefined;
  var assignments = (settings || readBotSettings()).assignments;
  var modelId = assignments[botId] || assignments[botId.toLowerCase()];
  if (!modelId) return undefined;
  return { botId: botId, modelId: modelId, stale: !modelInPlan(plan, modelId) };
}

// Efforts belong to a bot AND a canonical catalog model ID. Switching models
// must not carry a different model's effort across or erase its saved choice.
function resolveReasoning(messages, model, settings) {
  var botId = extractBotId(messages);
  if (!botId || !model || typeof model.id !== "string") return undefined;
  var reasoning = (settings || readBotSettings()).reasoning;
  var choices = reasoning[botId] || reasoning[botId.toLowerCase()];
  var effort = choices && Object.prototype.hasOwnProperty.call(choices, model.id) ? choices[model.id] : undefined;
  if (["default", "none", "on", "minimal", "low", "medium", "high", "xhigh", "max"].indexOf(effort) < 0) return undefined;
  var levels = Array.isArray(model.reasoningLevels) && model.reasoningLevels.length ? model.reasoningLevels : ["default", "none", "low", "medium", "high"];
  return { botId: botId, reasoningEffort: effort, stale: levels.indexOf(effort) < 0 };
}

exports.botModelsPath = botModelsPath;
exports.readBotModels = readBotModels;
exports.readBotSettings = readBotSettings;
exports.writeBotModels = writeBotModels;
exports.extractBotId = extractBotId;
exports.modelSupportsChat = modelSupportsChat;
exports.resolveAssignment = resolveAssignment;
exports.resolveReasoning = resolveReasoning;
