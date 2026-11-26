/** Curated provider presets for setup and add-provider flows. */

import { OPENCODE_GO_ORIGIN } from "../../../src/domain/opencode-go.ts";
import { ALIBABA_TOKEN_PLAN_ORIGIN } from "../../../src/domain/types.ts";

export type FreeModel = { id: string; name: string };

export function hasOpenCodeZenFreeModels(provider: { id: string; origin: string }): boolean {
  return provider.id === "opencode" && /^https:\/\/opencode\.ai\/zen(?:\/v1)?\/*$/.test(provider.origin);
}

/** $0 models listed by models.dev/providers/opencode. Ordered as display order. */
export const OPENCODE_ZEN_FREE_MODELS: readonly FreeModel[] = [
  { id: "big-pickle", name: "big-pickle" },
  { id: "glm-5-free", name: "GLM 5 Free" },
  { id: "glm-4.7-free", name: "GLM 4.7 Free" },
  { id: "deepseek-v4-flash-free", name: "DeepSeek V4 Flash Free" },
  { id: "kimi-k2.5-free", name: "Kimi K2.5 Free" },
  { id: "hy3-free", name: "HY3 Free" },
  { id: "hy3-preview-free", name: "HY3 Preview Free" },
  { id: "grok-code", name: "Grok Code" },
];

export type Preset = {
  id: string;
  name: string;
  origin: string;
  model: string;
  hint: string;
  oauth?: boolean;
  apiType?: "chat-completions" | "responses" | "anthropic";
  docsUrl?: string;
};

export const PRESETS: readonly Preset[] = [
  {
    id: "nvidia", name: "NVIDIA", origin: "https://integrate.api.nvidia.com/v1", model: "",
    hint: "Connect an NVIDIA API key, then fetch its available models.",
    docsUrl: "https://docs.api.nvidia.com/nim/reference/llm-apis",
  },
  {
    id: "opencode-go", name: "OpenCode Go", origin: OPENCODE_GO_ORIGIN, model: "",
    hint: "Use your Go or Go Plus subscription key. Each account gets its own model family and direct connection.",
    docsUrl: "https://opencode.ai/docs/console/go/",
  },
  {
    id: "openai",
    name: "OpenAI",
    origin: "https://api.openai.com/v1",
    model: "",
    hint: "Use an API key, sign in with OpenAI, or import a saved Codex login. Each account is stored separately.",
    oauth: true,
    apiType: "responses",
    docsUrl: "https://learn.chatgpt.com/docs/auth",
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    origin: "https://openrouter.ai/api/v1",
    model: "",
    hint: "One API key, many providers. OpenRouter receives the standard referer and title headers.",
  },
  {
    id: "alibaba-token-plan", name: "Alibaba Token Plan", origin: ALIBABA_TOKEN_PLAN_ORIGIN, model: "",
    hint: "Use the sk-sp- key for your Singapore Token Plan. Add each subscription as a separate account.",
    docsUrl: "https://www.alibabacloud.com/help/en/model-studio/base-url",
  },
  {
    id: "alibaba-coding-plan", name: "Alibaba Coding Plan", origin: "https://coding-intl.dashscope.aliyuncs.com/v1", model: "",
    hint: "Use your Coding Plan sk-sp- key. The international endpoint deducts Coding Plan quota.",
    docsUrl: "https://docs.modelstudio.console.alibabacloud.com/en/model-studio/coding-plan",
  },
  {
    id: "zai-coding-plan", name: "Z.AI Coding Plan", origin: "https://api.z.ai/api/coding/paas/v4", model: "",
    hint: "Connect a GLM Coding Plan account. Add each plan as a separate account.",
    docsUrl: "https://docs.z.ai/devpack/tool/opencode",
  },
  {
    id: "custom",
    name: "Custom",
    origin: "",
    model: "",
    hint: "Enter your provider’s base URL and choose Chat Completions, Responses, or Anthropic Messages.",
  },
];
