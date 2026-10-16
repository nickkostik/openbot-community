import assert from "node:assert/strict";
import test from "node:test";
import type { Model } from "../api/types.ts";
import { botModel, commonReasoningLevels, reasoningOptions } from "./bot-reasoning.ts";

const full = { id: "one:m", activeReasoning: "high", reasoningLevels: ["default", "none", "low", "high", "xhigh", "max"] } as Model;
const limited = { id: "two:m", activeReasoning: "low", reasoningLevels: ["default", "none", "low", "high"] } as Model;

test("bot controls use the pinned model or the global route when its pin is absent or stale", () => {
  const state = { models: [full, limited], activeModelId: full.id };
  assert.equal(botModel(state, limited.id), limited);
  assert.equal(botModel(state), full);
  assert.equal(botModel(state, "removed:model"), full);
});

test("bulk reasoning offers only the selected models' shared supported levels", () => {
  assert.deepEqual(commonReasoningLevels([full, limited]), ["default", "none", "low", "high"]);
  assert.deepEqual(commonReasoningLevels([]), []);
  assert.deepEqual(commonReasoningLevels([full, { ...limited, reasoningLevels: ["default"] }]), ["default"]);
  assert.deepEqual(commonReasoningLevels([full]).slice(-2), ["xhigh", "max"]);
  const options = reasoningOptions([full]);
  assert.equal(options[0]?.label, "Model setting (High)");
  assert.equal(options.find((option) => option.value === "default")?.label, "Provider default");
  assert.ok(options.some((option) => option.value === "none" && option.label === "Off"));
});
