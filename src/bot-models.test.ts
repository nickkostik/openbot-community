import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const botModels = require("../payload/bot-models.cjs") as {
  resolveAssignment(
    messages: unknown[],
    plan: unknown,
  ): { botId: string; modelId: string; stale: boolean } | undefined;
  writeBotModels(assignments: Record<string, string>): void;
  readBotSettings(): { assignments: Record<string, string>; reasoning: Record<string, Record<string, string>> };
  resolveReasoning(messages: unknown[], model: unknown, settings?: unknown): { botId: string; reasoningEffort: string; stale: boolean } | undefined;
};

const botId = "00000000-0000-4000-8000-feaccd25ce86";
const messages = [{ role: "system", content: "/home/box/agent-data/agents/" + botId + "/profile.json" }];
const plan = {
  kind: "custom",
  catalog: {
    providers: [],
    models: [{ id: "provider:special", providerId: "provider", slug: "special" }],
    bindings: [],
  },
};

function withAssignmentsFile<T>(run: (file: string) => T): T {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-bot-models-"));
  const file = path.join(root, "assignments.json");
  const previous = process.env.OPENBOT_BOT_MODELS;
  process.env.OPENBOT_BOT_MODELS = file;
  try {
    return run(file);
  } finally {
    if (previous === undefined) delete process.env.OPENBOT_BOT_MODELS;
    else process.env.OPENBOT_BOT_MODELS = previous;
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("bot model assignment resolves on every read and falls back when absent", () => {
  withAssignmentsFile(() => {
    assert.equal(botModels.resolveAssignment(messages, plan), undefined);
    botModels.writeBotModels({ [botId]: "provider:special" });
    assert.deepEqual(botModels.resolveAssignment(messages, plan), {
      botId,
      modelId: "provider:special",
      stale: false,
    });
    botModels.writeBotModels({});
    assert.equal(botModels.resolveAssignment(messages, plan), undefined);
  });
});

test("an assignment for a model removed from the catalog resolves as stale", () => {
  withAssignmentsFile(() => {
    botModels.writeBotModels({ [botId]: "provider:deleted" });
    assert.deepEqual(botModels.resolveAssignment(messages, plan), {
      botId,
      modelId: "provider:deleted",
      stale: true,
    });
  });
});

test("corrupt bot model assignments fail open", () => {
  withAssignmentsFile((file) => {
    fs.writeFileSync(file, "{not-json", "utf8");
    assert.equal(botModels.resolveAssignment(messages, plan), undefined);
  });
});

test("reasoning is scoped by bot and canonical model, reloads live and survives legacy model writes", () => {
  withAssignmentsFile((file) => {
    const model = { id: "one:shared", reasoningLevels: ["default", "high", "max"] };
    fs.writeFileSync(file, JSON.stringify({ assignments: {}, reasoning: { [botId]: { "one:shared": "max", "two:shared": "high" } }, note: "preserve" }));
    const snapshot = botModels.readBotSettings();
    assert.equal(botModels.resolveReasoning(messages, model)?.reasoningEffort, "max");
    assert.equal(botModels.resolveReasoning(messages, { ...model, id: "two:shared" })?.reasoningEffort, "high");
    assert.equal(botModels.resolveReasoning(messages, { ...model, id: "shared" }), undefined);
    assert.equal(botModels.resolveReasoning([{ role: "user", content: "no identity" }], model), undefined);
    botModels.writeBotModels({ [botId]: "two:shared" });
    const saved = JSON.parse(fs.readFileSync(file, "utf8"));
    assert.equal(saved.note, "preserve");
    assert.deepEqual(saved.reasoning, snapshot.reasoning);
    saved.reasoning[botId]["one:shared"] = "default";
    fs.writeFileSync(file, JSON.stringify(saved));
    assert.equal(botModels.resolveReasoning(messages, model)?.reasoningEffort, "default");
    assert.equal(botModels.resolveReasoning(messages, model, snapshot)?.reasoningEffort, "max");
    assert.equal(botModels.resolveReasoning(messages, { ...model, reasoningLevels: ["high"] })?.stale, true);
    saved.reasoning[botId]["one:shared"] = "nonsense";
    fs.writeFileSync(file, JSON.stringify(saved));
    assert.equal(botModels.resolveReasoning(messages, model), undefined);
  });
});
