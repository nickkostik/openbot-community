import type { CatalogModel } from "./model-catalog.ts";

// Supplement identities missing from models.dev using fetched official model
// pages. No fallback across accounts or endpoints. Unknown efforts stay empty
// rather than acquiring another provider's enum from a matching model name.
const OPENAI_LIMITS: Record<string, readonly [number, number, boolean]> = {
  "gpt-3.5-turbo": [16385, 4096, false],
  "gpt-3.5-turbo-instruct": [4096, 4096, false],
  "gpt-4": [8192, 8192, false],
  "gpt-5-codex": [400000, 128000, true],
  "gpt-5.1-codex": [400000, 128000, true],
  "gpt-5.1-codex-max": [400000, 128000, true],
  "gpt-5.2-codex": [400000, 128000, true],
  "o4-mini-deep-research": [200000, 100000, true],
  "gpt-5-chat-latest": [128000, 16384, true],
  "gpt-5.1-chat-latest": [128000, 16384, true],
  "gpt-5.2-chat-latest": [128000, 16384, true],
  "gpt-5.3-chat-latest": [128000, 16384, true],
};

export function documentedRouteModel(origin: string, modelId: string): CatalogModel | undefined {
  let url: URL;
  try { url = new URL(origin); } catch { return undefined; }
  if (url.hostname !== "api.openai.com") return undefined;
  const bare = modelId.replace(/-\d{4}-\d{2}-\d{2}$/, "")
    .replace(/^gpt-3\.5-turbo-(0125|1106)$/, "gpt-3.5-turbo")
    .replace(/^gpt-4-0613$/, "gpt-4");
  const limits = OPENAI_LIMITS[bare];
  if (!limits) return undefined;
  return {
    id: modelId, name: null, contextLength: limits[0], maxOutputTokens: limits[1],
    modalities: bare.startsWith("gpt-3.5") || bare === "gpt-4" ? ["text"] : ["text", "image"],
    reasoning: limits[2], reasoningLevels: !limits[2] ? ["default"]
      : bare === "gpt-5.2-codex" || bare === "gpt-5.1-codex-max" ? ["default", "low", "medium", "high", "xhigh"] : [],
    pricing: null, providerKey: "openai", sourceUrl: "https://developers.openai.com/api/docs/models/" + bare,
    outputModalities: ["text"],
  };
}
