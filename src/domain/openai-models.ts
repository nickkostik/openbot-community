import type { ReasoningLevel } from "./types.ts";

// OpenAI /v1/models lists identities, not capabilities. These documented
// facts prevent a bare list from turning mandatory reasoning into an Off
// toggle. Keep vendor context limits when supplied (Codex can report less).
// https://developers.openai.com/api/docs/models/gpt-6.1-sol
// https://developers.openai.com/api/docs/guides/reasoning
export function documentedOpenAIModel(slug: string): {
  readonly contextTokens: number;
  readonly reasoningLevels: readonly ReasoningLevel[];
} | undefined {
  const name = slug.replace(/-\d{4}-\d{2}-\d{2}$/, "");
  if (!["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"].includes(name)) return undefined;
  const mandatory = name === "gpt-6.1-sol" || name === "gpt-6-astra";
  return {
    contextTokens: 1_050_000,
    reasoningLevels: mandatory
      ? ["default", "low", "medium", "high", "xhigh", "max"]
      : ["default", "none", "low", "medium", "high", "xhigh", "max"],
  };
}
