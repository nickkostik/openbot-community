import type { ApiType, ReasoningLevel } from "../src/domain/types.ts";

type ReasoningPolicy = {
  readonly kind: "deepseek-v4" | "kimi-code-pro" | "nvidia-nemotron-toggle" | "nvidia-super-vl";
  readonly apiType?: ApiType;
  readonly levels: readonly ReasoningLevel[];
  readonly aliases: Readonly<Record<string, ReasoningLevel>>;
  readonly maxOutputTokens?: number;
};

export const DEEPSEEK_ORIGIN: string;
export function isDirectDeepSeekOrigin(baseUrl: string): boolean;
export function nvidiaEmbeddingInputs(modelId: string, baseUrl: string): ("text" | "image")[] | undefined;
export function documentedReasoningPolicy(modelId: string, baseUrl: string): ReasoningPolicy | undefined;
export function documentedReasoningEffort(policy: ReasoningPolicy, value: unknown): ReasoningLevel | undefined;
