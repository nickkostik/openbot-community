import type { ReasoningLevel } from "../src/domain/types.ts";

type ReasoningPolicy = {
  readonly kind: "kimi-code-pro" | "nvidia-nemotron-toggle" | "nvidia-super-vl";
  readonly levels: readonly ReasoningLevel[];
  readonly aliases: Readonly<Record<string, ReasoningLevel>>;
  readonly maxOutputTokens?: number;
};

export function documentedReasoningPolicy(modelId: string, baseUrl: string): ReasoningPolicy | undefined;
export function documentedReasoningEffort(policy: ReasoningPolicy, value: unknown): ReasoningLevel | undefined;
