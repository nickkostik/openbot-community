export function canonicalKimiCodeProEffort(value: unknown): "low" | "high" | "max" | undefined;

export function kimiCodeProReasoningLevels(
  modelId: string,
  baseUrl: string,
): readonly ["default", "low", "high", "max"] | undefined;
