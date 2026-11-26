import { createHash } from "node:crypto";
import { type ApiType, type Catalog, type ModelType, type SecretBytes, MAX_OUTPUT_TOKENS_CEILING } from "../domain/types.ts";
import { keepReasoningOrder, parseModelType } from "../domain/model.ts";
import { secretFor, type SecretStore } from "../supervisor/secrets.ts";
import { fetchedReasoningLevels, mapVendorEffort } from "./reasoning-efforts.ts";
import { documentedOpenAIModel } from "../domain/openai-models.ts";
import { isOpenCodeGoConsoleOrigin, isOpenCodeGoOrigin, OPENCODE_GO_ORIGIN } from "../domain/opencode-go.ts";
import { documentedReasoningPolicy } from "../../payload/provider-maps.cjs";
import { ORIGIN as CHATGPT_PLAN_ORIGIN, isDirectOrigin, readCredential as readChatGPTPlanCredential } from "../../payload/chatgpt-plan.cjs";

/**
 * Source A: fetch a provider's own model list from its base URL + /v1/models,
 * server-side, using the stored secret. Secrets never leave the process and are
 * never echoed back or logged.
 */

/** Minimal fetch surface so tests can mock the outbound network. */
export type FetchLike = (
  url: string,
  init?: { readonly headers?: Record<string, string>; readonly signal?: AbortSignal; readonly redirect?: RequestRedirect },
) => Promise<{ readonly status: number; readonly ok: boolean; text(): Promise<string> }>;

export const PROVIDER_MODELS_TOTAL_TIMEOUT_MS = 30_000;
export const OPENAI_CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=0.153.3";
const OPENAI_CODEX_USER_AGENT = "codex-tui/0.153.3 (Mac OS 26.5.1; arm64) iTerm.app/3.6.11 (codex-tui; 0.153.3)";

export const defaultFetch: FetchLike = (url, init) => fetch(url, init);

const MODALITY_TOKENS = new Set<string>(["text", "image", "video", "audio"]);

/** Normalized Source A model row (PRD §8.1 models[]). */
export type FetchedModel = {
  readonly id: string;
  readonly name: string | null;
  readonly contextLength: number | null;
  readonly maxOutputTokens: number | null;
  readonly modalities: string[];
  readonly reasoningLevels: string[];
  readonly apiType?: ApiType;
  readonly modelType?: ModelType;
  readonly providerMetadata?: "opencode-go";
};

export type FetchErrorKind = "unauthorized" | "unreachable" | "not-supported" | "parse-error" | "internal";

export type FetchProviderResult =
  | { readonly ok: true; readonly models: FetchedModel[]; readonly skipped: number; readonly skippedReasons: string[] }
  | { readonly ok: false; readonly errorKind: FetchErrorKind; readonly message: string; readonly upstreamStatus?: number };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function firstArray(...values: unknown[]): unknown[] | undefined {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value;
    }
  }
  return undefined;
}

function firstPositiveInt(...values: unknown[]): number | null {
  for (const value of values) {
    const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
    if (Number.isFinite(n) && n > 0) {
      return Math.floor(n);
    }
  }
  return null;
}

/**
 * Max-output variant: values above MAX_OUTPUT_TOKENS_CEILING are upstream
 * data corruption (context-window figures in a max-completion field), so
 * they are skipped in favor of the next candidate instead of poisoning the
 * fetched row.
 */
function firstSaneOutputTokens(...values: unknown[]): number | null {
  for (const value of values) {
    const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
    if (Number.isFinite(n) && n > 0 && n <= MAX_OUTPUT_TOKENS_CEILING) {
      return Math.floor(n);
    }
  }
  return null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function nestedRecord(value: unknown, key: string): Record<string, unknown> | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const inner = value[key];
  return isRecord(inner) ? inner : undefined;
}

export function filterModalities(value: unknown): string[] {
  const raw = firstArray(value);
  const out: string[] = [];
  const seen = new Set<string>();
  if (raw) {
    for (const item of raw) {
      if (typeof item !== "string") {
        continue;
      }
      const token = item.trim().toLowerCase();
      if (MODALITY_TOKENS.has(token) && !seen.has(token)) {
        seen.add(token);
        out.push(token);
      }
    }
  }
  return out;
}

export function filterReasoningLevels(value: unknown): string[] {
  const raw = firstArray(value);
  const seen = new Set<string>();
  if (raw) {
    for (const item of raw) {
      const mapped = mapVendorEffort(item);
      if (mapped !== undefined) {
        seen.add(mapped);
      }
    }
  }
  return keepReasoningOrder(seen);
}

function modelListFrom(raw: unknown): unknown[] | undefined {
  if (!isRecord(raw)) {
    return undefined;
  }
  if (Array.isArray(raw.data)) {
    return raw.data;
  }
  if (Array.isArray(raw.models)) {
    return raw.models;
  }
  return undefined;
}

function modelIdFrom(item: Record<string, unknown>): string | null {
  const value = item.id ?? item.slug;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function goRecommendedProtocol(value: unknown): ApiType | undefined {
  if (value === "messages") return "anthropic";
  if (value === "responses" || value === "chat-completions") return value;
  return undefined;
}

function goReasoningLevels(reasoning: Record<string, unknown> | undefined): string[] | undefined {
  if (reasoning?.supported === false) return ["default"];
  if (reasoning?.supported !== true) return undefined;
  const levels = new Set(["default", ...filterReasoningLevels(reasoning.effort)]);
  if (reasoning.toggle === true) levels.add("none");
  return keepReasoningOrder(levels);
}

function normalizeEntry(item: Record<string, unknown>, id: string, directGo: boolean): FetchedModel {
  const architecture = nestedRecord(item, "architecture");
  const topProvider = nestedRecord(item, "top_provider");
  const modelType = directGo && typeof item.type === "string" && item.type.trim()
    ? parseModelType(item.type) ?? "other" : undefined;
  const goChat = modelType === "chat";
  const limit = goChat ? nestedRecord(item, "limit") : undefined;
  const capabilities = goChat ? nestedRecord(item, "capabilities") : undefined;
  const apiType = goChat ? goRecommendedProtocol(item.recommended_api) : undefined;
  const mimeTypes = goChat ? firstArray(nestedRecord(item, "modalities")?.input) : undefined;
  const goModalities = filterModalities(mimeTypes?.map(value => typeof value === "string" ? value.split("/", 1)[0] : value));
  const reasoningLevels = goReasoningLevels(nestedRecord(capabilities, "reasoning"));
  return {
    id,
    name: stringOrNull(item.name ?? item.display_name),
    contextLength: firstPositiveInt(limit?.context, item.context_length, item.contextLength, item.context_window, item.contextWindow, architecture?.context_length),
    // The native limit.output is published capacity. Model import applies the
    // existing generation budget, including when the published limit is larger.
    maxOutputTokens: firstPositiveInt(limit?.output) ?? firstSaneOutputTokens(
      topProvider?.max_completion_tokens,
      item.max_completion_tokens,
      item.maxOutputTokens,
      item.max_tokens,
    ),
    modalities: goModalities.length ? goModalities : filterModalities(firstArray(item.input_modalities, architecture?.input_modalities, item.modalities)),
    reasoningLevels: modelType && !goChat ? [] : reasoningLevels ?? [...fetchedReasoningLevels(item)],
    ...(apiType ? { apiType } : {}),
    ...(modelType ? { modelType, providerMetadata: "opencode-go" } : {}),
  };
}

/**
 * Normalize a provider `/v1/models` response body (`{ data: [...] }` or
 * `{ models: [...] }`). Returns `undefined` when the body is not a model list.
 */
export function normalizeProviderModels(
  raw: unknown,
  directGo = false,
): { models: FetchedModel[]; skipped: number; skippedReasons: string[] } | undefined {
  const list = modelListFrom(raw);
  if (list === undefined) {
    return undefined;
  }
  const models: FetchedModel[] = [];
  const skippedReasons: string[] = [];
  const seen = new Set<string>();
  for (const item of list) {
    if (!isRecord(item)) {
      skippedReasons.push("not-an-object");
      continue;
    }
    const id = modelIdFrom(item);
    if (id === null) {
      skippedReasons.push("missing-id");
      continue;
    }
    if (seen.has(id)) {
      skippedReasons.push("duplicate-id");
      continue;
    }
    seen.add(id);
    models.push(normalizeEntry(item, id, directGo));
  }
  return { models, skipped: skippedReasons.length, skippedReasons };
}

/** The provider's `/v1/models` URL, reusing the hop's base-URL normalization. */
export function modelsUrl(origin: string): string {
  const base = origin.replace(/\/+$/, "");
  if (!base) {
    throw new Error("openbot: provider origin is empty");
  }
  if (isOpenCodeGoOrigin(base)) {
    return `${OPENCODE_GO_ORIGIN}/v1/models`;
  }
  if (
    /\/v1$/i.test(base) ||
    /\/v4$/i.test(base) ||
    /\/paas\/v4$/i.test(base) ||
    /\/chat\/completions$/i.test(base)
  ) {
    return `${base}/models`;
  }
  return `${base}/v1/models`;
}

function timeoutMessage(err: unknown): string {
  const name = err instanceof Error ? err.name : "";
  if (name === "TimeoutError" || name === "AbortError") {
    return "provider timed out";
  }
  return "provider is unreachable";
}

function opencodeModelsSession(providerId: string): string {
  return createHash("sha256").update("openbot-opencode-models\0" + providerId, "utf8").digest("hex").slice(0, 32);
}

function openAIOAuthAccountId(secret: string): string {
  try {
    const credential = JSON.parse(secret) as Record<string, unknown>;
    if (credential.kind !== "openai-oauth") return "";
    if (typeof credential.chatgptAccountId === "string" && credential.chatgptAccountId.trim()) return credential.chatgptAccountId.trim();
    for (const token of [credential.idToken, credential.accessToken]) {
      if (typeof token !== "string") continue;
      const parts = token.split(".");
      if (parts.length < 2) continue;
      const claims = JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf8")) as Record<string, unknown>;
      const auth = claims["https://api.openai.com/auth"];
      if (auth && typeof auth === "object" && !Array.isArray(auth) && typeof (auth as Record<string, unknown>).chatgpt_account_id === "string") {
        return ((auth as Record<string, unknown>).chatgpt_account_id as string).trim();
      }
    }
  } catch {
    // API-key secrets are not JSON credentials.
  }
  return "";
}

function isOpenAIOAuthSecret(secret: string): boolean {
  try {
    const credential = JSON.parse(secret) as Record<string, unknown>;
    return credential.kind === "openai-oauth" && typeof credential.accessToken === "string";
  } catch {
    return false;
  }
}

async function fetchProviderModels(input: {
  url: string;
  secret: string;
  providerId: string;
  headers?: Record<string, string>;
  fetchFn: FetchLike;
  totalTimeoutMs: number;
  chatGPTPlan?: boolean;
}): Promise<FetchProviderResult> {
  const directGo = isOpenCodeGoOrigin(input.url.replace(/\/v1\/models$/, ""));
  let oauthAccessToken: string | null = null;
  if (isOpenAIOAuthSecret(input.secret)) {
    try {
      const credential = JSON.parse(input.secret) as Record<string, unknown>;
      if (credential.kind === "openai-oauth" && typeof credential.accessToken === "string") {
        oauthAccessToken = credential.accessToken;
      }
    } catch {
      // API-key secrets are intentionally opaque.
    }
  }
  let res: { readonly status: number; readonly ok: boolean; text(): Promise<string> };
  try {
    res = await input.fetchFn(input.url, {
      headers: {
        ...(input.headers ?? {}),
        ...((oauthAccessToken ?? input.secret) ? { Authorization: "Bearer " + (oauthAccessToken ?? input.secret) } : {}),
        Accept: "application/json",
        ...(directGo ? { "User-Agent": "OpenBot/catalog" } : {}),
        ...(directGo || input.providerId.startsWith("opencode") ? { "x-opencode-session": opencodeModelsSession(input.providerId) } : {}),
      },
      signal: AbortSignal.timeout(input.totalTimeoutMs),
      ...(input.chatGPTPlan ? { redirect: "error" as const } : {}),
    });
  } catch (err) {
    return { ok: false, errorKind: "unreachable", message: timeoutMessage(err) };
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, errorKind: "unauthorized", message: input.chatGPTPlan ? "OpenAI rejected the ChatGPT login. Sign in again." : "provider rejected the API key", upstreamStatus: res.status };
  }
  if (res.status === 404 || res.status === 405 || res.status === 501) {
    return {
      ok: false,
      errorKind: "not-supported",
      message: "provider does not expose a model list",
      upstreamStatus: res.status,
    };
  }
  if (res.status < 200 || res.status >= 300) {
    return { ok: false, errorKind: "unreachable", message: `upstream returned HTTP ${res.status}`, upstreamStatus: res.status };
  }
  let text: string;
  try {
    text = await res.text();
  } catch {
    return { ok: false, errorKind: "unreachable", message: "failed to read the upstream response" };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, errorKind: "parse-error", message: "provider returned a non-JSON body" };
  }
  if (input.chatGPTPlan && isRecord(parsed) && Array.isArray(parsed.models)) {
    parsed = { models: parsed.models.filter(item => isRecord(item) && item.visibility === "list") };
  }
  const normalized = normalizeProviderModels(parsed, directGo);
  if (normalized === undefined) {
    return { ok: false, errorKind: "parse-error", message: "provider response is not a model list" };
  }
  return { ok: true, ...normalized };
}

function httpStatusFor(kind: FetchErrorKind): number {
  switch (kind) {
    case "unauthorized":
      return 401;
    case "not-supported":
    case "unreachable":
    case "parse-error":
      return 502;
    case "internal":
      return 500;
  }
}

/**
 * Endpoint logic for `POST /api/providers/{providerId}/fetch-models`. Resolves
 * the provider, requires a stored secret, fetches and normalizes the model list,
 * and returns the HTTP status + JSON body for the caller to send.
 */
export async function fetchModelsForProvider(input: {
  providerId: string;
  catalog: Catalog;
  secretStore: SecretStore;
  fetchFn?: FetchLike;
  refreshOAuth?: (providerId: string) => Promise<string>;
}): Promise<{ readonly status: number; readonly body: unknown }> {
  const provider = input.catalog.providers.find((row) => row.id === input.providerId);
  if (provider === undefined) {
    return { status: 404, body: { error: { kind: "provider-not-found", message: "provider not found" } } };
  }
  let secret = secretFor(input.secretStore, provider.id);
  if (secret === undefined) {
    return { status: 409, body: { error: { kind: "no-secret", message: "no API key stored for this provider" } } };
  }
  let result: FetchProviderResult;
  let isChatGPTPlan = false;
  try {
    const isOpenAIOAuth = isOpenAIOAuthSecret(secret);
    let plan = readChatGPTPlanCredential(secret);
    isChatGPTPlan = plan !== undefined;
    if (plan && !isDirectOrigin(provider.origin)) {
      return { status: 400, body: { error: { kind: "not-supported", message: "ChatGPT plan login requires the direct OpenAI API." } } };
    }
    if ((isOpenAIOAuth && input.refreshOAuth) || plan) {
      const credential = JSON.parse(secret) as Record<string, unknown>;
      if (!(Number(credential.expiresAt) > Date.now() / 1000 + 60)) {
        try {
          if (!input.refreshOAuth) throw new Error("Refresh unavailable");
          secret = await input.refreshOAuth(provider.id) as SecretBytes;
          if (plan) {
            const refreshed = readChatGPTPlanCredential(secret);
            if (!refreshed || refreshed.clientId !== plan.clientId || refreshed.subject !== plan.subject) throw new Error("Account changed");
            plan = refreshed;
          } else if (!isOpenAIOAuthSecret(secret)) {
            throw new Error("Refresh did not persist an OAuth credential");
          }
        } catch {
          return { status: 401, body: { error: { kind: "unauthorized", message: "OpenAI login could not be refreshed. Sign in again or import a current login file." } } };
        }
      }
    }
    result = await fetchProviderModels({
      url: plan ? `${CHATGPT_PLAN_ORIGIN}/models` : isOpenAIOAuth ? OPENAI_CODEX_MODELS_URL : modelsUrl(provider.origin),
      secret: plan?.accessToken ?? secret,
      chatGPTPlan: isChatGPTPlan,
      providerId: provider.id,
      ...(isOpenAIOAuth
        ? {
            headers: {
              "user-agent": OPENAI_CODEX_USER_AGENT,
              originator: "codex-tui",
              ...(openAIOAuthAccountId(secret) ? { "chatgpt-account-id": openAIOAuthAccountId(secret) } : {}),
            },
          }
        : {}),
      fetchFn: input.fetchFn ?? defaultFetch,
      totalTimeoutMs: PROVIDER_MODELS_TOTAL_TIMEOUT_MS,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: { kind: "internal", message } } };
  }
  if (!result.ok) {
    const body: { error: { kind: string; message: string; upstreamStatus?: number } } = {
      error: { kind: result.errorKind, message: result.message },
    };
    if (result.upstreamStatus !== undefined) {
      body.error.upstreamStatus = result.upstreamStatus;
    }
    return { status: httpStatusFor(result.errorKind), body };
  }
  // The native API's list normally has no context/effort capabilities. Use
  // documented facts for known models; do not rewrite another provider's
  // independently advertised route limits. Codex's supplied context wins.
  let nativeOpenAI = false;
  try { nativeOpenAI = new URL(provider.origin).hostname === "api.openai.com"; } catch { /* Reported during fetch above. */ }
  const models = result.models.map((model) => {
    const policy = documentedReasoningPolicy(model.id, provider.origin);
    if (policy) return {
      ...model,
      reasoningLevels: [...policy.levels],
      ...(policy.maxOutputTokens ? { maxOutputTokens: Math.min(model.maxOutputTokens ?? Infinity, policy.maxOutputTokens) } : {}),
      ...(policy.kind !== "kimi-code-pro" ? { apiType: "chat-completions" as const } : {}),
    };
    if (!nativeOpenAI) return model;
    const facts = documentedOpenAIModel(model.id);
    return {
      ...model,
      ...(isChatGPTPlan ? { apiType: "responses" as const } : {}),
      ...(facts ? { contextLength: model.contextLength ?? facts.contextTokens,
        maxOutputTokens: model.maxOutputTokens ?? facts.maxOutputTokens ?? null,
        reasoningLevels: isChatGPTPlan && model.reasoningLevels.length > 1 ? model.reasoningLevels : [...facts.reasoningLevels] } : {}),
    };
  });
  return {
    status: 200,
    body: {
      ok: true,
      providerId: provider.id,
      source: "provider",
      fetchedAt: new Date().toISOString(),
      skipped: result.skipped,
      skippedReasons: result.skippedReasons,
      models,
    },
  };
}
