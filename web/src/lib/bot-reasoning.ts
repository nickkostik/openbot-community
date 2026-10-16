import { REASONING_LEVELS, type BoxState, type Model, type ReasoningLevel } from "../api/types.ts";
import { labelReasoning } from "./format.ts";

export function botModel(state: Pick<BoxState, "models" | "activeModelId">, assigned?: string): Model | undefined {
  return state.models.find((model) => model.id === assigned) ?? state.models.find((model) => model.id === state.activeModelId);
}

export function commonReasoningLevels(models: readonly Model[]): ReasoningLevel[] {
  return models.length ? REASONING_LEVELS.filter((level) => models.every((model) => model.reasoningLevels.includes(level))) : [];
}

export function reasoningOptions(models: readonly Model[]) {
  return [{ value: "", label: models.length === 1 ? `Model setting (${labelReasoning(models[0]!.activeReasoning)})` : "Model settings" },
    ...commonReasoningLevels(models).map((level) => ({ value: level, label: level === "default" ? "Provider default" : labelReasoning(level) }))];
}
