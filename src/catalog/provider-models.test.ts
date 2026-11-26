import assert from "node:assert/strict";
import test from "node:test";
import { type Catalog, type SecretBytes } from "../domain/types.ts";
import { type SecretStore } from "../supervisor/secrets.ts";
import { fetchModelsForProvider, modelsUrl, normalizeProviderModels, OPENAI_CODEX_MODELS_URL, type FetchLike } from "./provider-models.ts";
import { ORIGIN, SCOPES } from "../../payload/chatgpt-plan.cjs";

type ResponseStub = { readonly status: number; readonly ok: boolean; text(): Promise<string> };

function jsonResponse(status: number, body: unknown): ResponseStub {
  return { status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(body) };
}

function textResponse(status: number, text: string): ResponseStub {
  return { status, ok: status >= 200 && status < 300, text: async () => text };
}

function makeCatalog(providers: { id: string; name: string; origin: string }[]): Catalog {
  const catalog = {
    providers: providers.map((provider) => ({
      ...provider,
      maxTokensDefault: 65536,
      mapFile: "provider-maps.cjs",
    })),
    models: [],
    bindings: [],
  };
  return catalog as unknown as Catalog;
}

function makeSecrets(entries: Record<string, string>): SecretStore {
  const providers: { [id: string]: SecretBytes } = {};
  for (const [id, value] of Object.entries(entries)) {
    providers[id] = value as SecretBytes;
  }
  return { providers };
}

test("modelsUrl reuses the hop base-URL normalization", () => {
  assert.equal(modelsUrl("https://api.openai.com/v1"), "https://api.openai.com/v1/models");
  assert.equal(modelsUrl("https://open.bigmodel.cn/api/paas/v4"), "https://open.bigmodel.cn/api/paas/v4/models");
  assert.equal(modelsUrl("https://example.com/v4"), "https://example.com/v4/models");
  assert.equal(modelsUrl("https://example.com/chat/completions"), "https://example.com/chat/completions/models");
  assert.equal(modelsUrl("https://example.com"), "https://example.com/v1/models");
  assert.equal(modelsUrl("https://example.com/"), "https://example.com/v1/models");
});

test("normalizeProviderModels rejects a body that is not a model list", () => {
  assert.equal(normalizeProviderModels({ foo: 1 }), undefined);
  assert.equal(normalizeProviderModels([]), undefined);
  assert.equal(normalizeProviderModels("nope"), undefined);
});

test("direct DeepSeek discovery shares its model list and adds documented reasoning choices", async () => {
  const direct = "https://api.deepseek.com";
  const entries = ["deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp", "other-model"]
    .map(id => ({ id, context_length: 1048576 }));
  for (const [origin, expectedUrl] of [
    [direct + "/v1/", direct + "/v1/models"],
    [direct + "/anthropic/v1/messages", direct + "/v1/models"],
    [direct + "/responses", direct + "/v1/models"],
    ["https://api.deepseek.com.example/v1", "https://api.deepseek.com.example/v1/models"],
  ] as const) {
    const result = await fetchModelsForProvider({
      providerId: "deepseek",
      catalog: makeCatalog([{ id: "deepseek", name: "DeepSeek", origin }]),
      secretStore: makeSecrets({ deepseek: "fixture-key" }),
      fetchFn: async (url) => {
        assert.equal(url, expectedUrl);
        return jsonResponse(200, { data: entries });
      },
    });
    assert.equal(result.status, 200);
    const models = (result.body as { models: { id: string; contextLength: number; apiType?: string; reasoningLevels: string[] }[] }).models;
    assert.deepEqual(models.map(model => model.id), entries.map(entry => entry.id));
    const levels = expectedUrl.startsWith(direct + "/") ? ["default", "none", "low", "high", "max"] : [];
    assert.deepEqual(models.map(model => model.reasoningLevels), [levels, levels, levels, []]);
    assert.ok(models.every(model => model.apiType === undefined && model.contextLength === 1048576));
  }
});

test("direct Kimi Code discovery applies the documented K3.1 Pro efforts", async () => {
  const entries = ["k3.1-pro", "k3.1-pro-highspeed", "k3"].map(id => ({
    id, reasoning_levels: ["none", "low", "high", "max"],
  }));
  for (const origin of ["https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/", "https://example.test/coding/v1"]) {
    const catalog = makeCatalog([{ id: "fixture", name: "Fixture", origin }]);
    const result = await fetchModelsForProvider({
      providerId: "fixture",
      catalog,
      secretStore: makeSecrets({ fixture: "fixture-key" }),
      fetchFn: async (url) => {
        assert.equal(url, modelsUrl(origin));
        return jsonResponse(200, { data: entries });
      },
    });
    assert.equal(result.status, 200);
    const models = (result.body as { models: { id: string; reasoningLevels: string[] }[] }).models;
    const generic = ["default", "none", "low", "high", "max"];
    const pro = origin.includes("example.test") ? generic : ["default", "low", "high", "max"];
    assert.deepEqual(models.map(row => row.reasoningLevels), [pro, pro, generic]);
  }
});

test("direct NVIDIA lists expose documented Nemotron reasoning and preserve other models", async () => {
  const origin = "https://integrate.api.nvidia.com/v1";
  const entries = [
    "nvidia/nemotron-3.5-bolt-e4b", "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3-super-120b-a12b",
  ].map(id => ({ id, context_length: 128000 }));
  const catalog = makeCatalog([
    { id: "first", name: "First", origin }, { id: "second", name: "Second", origin },
    { id: "other", name: "Other", origin: "https://example.test/v1" },
  ]);
  for (const provider of catalog.providers) {
    const response = await fetchModelsForProvider({
      providerId: provider.id, catalog,
      secretStore: makeSecrets({ first: "fixture-first", second: "fixture-second", other: "fixture-other" }),
      fetchFn: async (url, options) => {
        assert.equal(url, modelsUrl(provider.origin));
        assert.equal(new Headers(options?.headers).get("authorization"), "Bearer fixture-" + provider.id);
        return jsonResponse(200, { data: entries });
      },
    });
    assert.equal(response.status, 200);
    const models = (response.body as { models: { reasoningLevels: string[]; apiType?: string; contextLength: number; maxOutputTokens: number | null }[] }).models;
    const toggle = ["default", "none", "on"];
    const expected = provider.id === "other" ? [[], [], [], []] : [toggle, toggle, ["default", "none", "low", "high"], []];
    assert.deepEqual(models.map(model => model.reasoningLevels), expected);
    assert.deepEqual(models.map(model => model.apiType), provider.id === "other"
      ? [undefined, undefined, undefined, undefined] : ["chat-completions", "chat-completions", "chat-completions", undefined]);
    assert.ok(models.every(model => model.contextLength === 128000));
    assert.equal(models[1]?.maxOutputTokens, provider.id === "other" ? null : 32768);
  }
});

test("normalizeProviderModels drops entries without an id as skipped", () => {
  const result = normalizeProviderModels({
    data: [
      { id: "glm-5.3", name: "GLM 5.3" },
      { name: "no id" },
      "not an object",
      { id: "glm-5.3" },
    ],
  });
  assert.ok(result);
  assert.equal(result.models.length, 1);
  assert.equal(result.models[0]?.id, "glm-5.3");
  assert.equal(result.skipped, 3);
  assert.deepEqual(result.skippedReasons, ["missing-id", "not-an-object", "duplicate-id"]);
});

test("normalizeProviderModels maps modalities and reasoning levels to the allowed sets", () => {
  const result = normalizeProviderModels({
    data: [
      {
        id: "m",
        input_modalities: ["text", "image", "weird"],
        reasoningLevels: ["high", "low", "bogus", "high"],
      },
    ],
  });
  assert.ok(result);
  const model = result.models[0];
  assert.ok(model);
  assert.deepEqual(model.modalities, ["text", "image"]);
  assert.deepEqual(model.reasoningLevels, ["default", "none", "low", "high"]);
});

test("normalizeProviderModels reads nested reasoning.supported_efforts", () => {
  const result = normalizeProviderModels({
    data: [
      {
        id: "deepseek-v4-flash",
        reasoning: { mandatory: false, supported_efforts: ["xhigh", "high"] },
      },
    ],
  });
  assert.ok(result);
  assert.deepEqual(result.models[0]?.reasoningLevels, ["default", "none", "high", "xhigh"]);
});

test("normalizeProviderModels reads nested reasoning_options", () => {
  const result = normalizeProviderModels({
    data: [
      {
        id: "glm-5.3-flash",
        reasoning_options: [
          { type: "toggle" },
          { type: "effort", values: ["low", "high", "max"] },
        ],
      },
    ],
  });
  assert.ok(result);
  assert.deepEqual(result.models[0]?.reasoningLevels, ["default", "none", "low", "high", "max"]);
});

test("normalizeProviderModels drops a poisoned max_completion_tokens above the ceiling", () => {
  // Regression: commandcode reported 943718 (a context-window figure) in a
  // max-completion field; it must not land in the fetched row.
  const result = normalizeProviderModels({
    data: [{ id: "meta/muse-spark", top_provider: { max_completion_tokens: 943718 } }],
  });
  assert.ok(result);
  assert.equal(result.models[0]?.maxOutputTokens, null);
});

test("normalizeProviderModels falls through to a sane max_completion_tokens candidate", () => {
  const result = normalizeProviderModels({
    data: [{ id: "m", top_provider: { max_completion_tokens: 943718 }, max_completion_tokens: 65536 }],
  });
  assert.ok(result);
  assert.equal(result.models[0]?.maxOutputTokens, 65536);
});

test("normalizeProviderModels keeps a max-output value at the ceiling boundary", () => {
  const result = normalizeProviderModels({
    data: [{ id: "m", max_completion_tokens: 131072 }],
  });
  assert.ok(result);
  assert.equal(result.models[0]?.maxOutputTokens, 131072);
});

test("fetch-models succeeds and forwards the stored secret server-side", async () => {
  const catalog = makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://open.bigmodel.cn/api/paas/v4" }]);
  const store = makeSecrets({ zhipu: "sk-live" });
  let requestedUrl = "";
  let requestedAuth = "";
  const fetchFn: FetchLike = async (url, init) => {
    requestedUrl = url;
    requestedAuth = init?.headers?.Authorization ?? "";
    return jsonResponse(200, {
      data: [
        { id: "glm-5.3", name: "GLM 5.3", context_length: 128000 },
        { id: "glm-4.5" },
        { name: "missing id" },
      ],
    });
  };
  const result = await fetchModelsForProvider({ providerId: "zhipu", catalog, secretStore: store, fetchFn });
  assert.equal(result.status, 200);
  assert.equal(requestedUrl, "https://open.bigmodel.cn/api/paas/v4/models");
  assert.equal(requestedAuth, "Bearer sk-live");
  const body = result.body as {
    ok: boolean;
    providerId: string;
    source: string;
    skipped: number;
    skippedReasons: string[];
    models: { id: string; name: string | null; contextLength: number | null; modalities: string[] }[];
  };
  assert.equal(body.ok, true);
  assert.equal(body.providerId, "zhipu");
  assert.equal(body.source, "provider");
  assert.equal(body.skipped, 1);
  assert.deepEqual(body.skippedReasons, ["missing-id"]);
  assert.equal(body.models.length, 2);
  assert.equal(body.models[0]?.id, "glm-5.3");
  assert.equal(body.models[0]?.contextLength, 128000);
  assert.equal(body.models[0]?.modalities.length, 0);
});

test("OpenAI OAuth fetch-models uses the Codex models endpoint and credential headers", async () => {
  const catalog = makeCatalog([{ id: "openai", name: "OpenAI", origin: "https://api.openai.com/v1" }]);
  const secret = JSON.stringify({ kind: "openai-oauth", accessToken: "oauth-access", refreshToken: "refresh", expiresAt: 9999999999, chatgptAccountId: "acct-123" });
  let requestedUrl = "";
  let requestedHeaders: Record<string, string> = {};
  const fetchFn: FetchLike = async (url, init) => {
    requestedUrl = url;
    requestedHeaders = init?.headers ?? {};
    return jsonResponse(200, { models: [{ slug: "gpt-5.6-sol", display_name: "GPT-5.6 Sol" }] });
  };
  const result = await fetchModelsForProvider({ providerId: "openai", catalog, secretStore: makeSecrets({ openai: secret }), fetchFn });
  assert.equal(result.status, 200);
  assert.equal(requestedUrl, OPENAI_CODEX_MODELS_URL);
  assert.equal(requestedHeaders.Authorization, "Bearer oauth-access");
  assert.equal(requestedHeaders["chatgpt-account-id"], "acct-123");
  assert.equal(requestedHeaders.originator, "codex-tui");
  const body = result.body as { models: { id: string; name: string | null }[] };
  assert.deepEqual(body.models, [{ id: "gpt-5.6-sol", name: "GPT-5.6 Sol", contextLength: null, maxOutputTokens: null, modalities: [], reasoningLevels: [] }]);
});

test("native OpenAI identity-only listings receive documented Sol capabilities", async () => {
  const catalog = makeCatalog([{ id: "openai", name: "OpenAI", origin: "https://api.openai.com/v1" }]);
  const result = await fetchModelsForProvider({ providerId: "openai", catalog, secretStore: makeSecrets({ openai: "sk-fixture" }), fetchFn: async () => jsonResponse(200, { data: [{ id: "gpt-6.1-sol" }] }) });
  assert.equal(result.status, 200);
  const model = (result.body as any).models[0];
  assert.equal(model.contextLength, 1_050_000);
  assert.deepEqual(model.reasoningLevels, ["default", "low", "medium", "high", "xhigh", "max"]);
});

test("ChatGPT plan model discovery lists only visible models through the public API", async () => {
  const credential = { kind: "chatgpt-plan", clientId: "oaiapp_fixture", subject: "subject-fixture", idToken: "id-fixture",
    accessToken: "access-old-fixture", refreshToken: "refresh-fixture", scopes: SCOPES, expiresAt: 1 };
  const catalog = makeCatalog([{ id: "openai", name: "OpenAI", origin: ORIGIN }]);
  const result = await fetchModelsForProvider({ providerId: "openai", catalog, secretStore: makeSecrets({ openai: JSON.stringify(credential) }),
    refreshOAuth: async id => {
      assert.equal(id, "openai");
      return JSON.stringify({ ...credential, accessToken: "access-new-fixture", expiresAt: 9999999999 });
    },
    fetchFn: async (url, init) => {
      assert.equal(url, `${ORIGIN}/models`);
      assert.equal(init?.headers?.Authorization, "Bearer access-new-fixture");
      assert.equal(init?.headers?.originator, undefined);
      return jsonResponse(200, { models: [{ slug: "private-fixture", visibility: "hide" },
        { slug: "gpt-6.2-sol", visibility: "list", display_name: "GPT 6.2 Sol" },
        { slug: "gpt-6.2-astra", visibility: "list", display_name: "GPT 6.2 Astra" }] });
    } });
  assert.equal(result.status, 200);
  const rows = (result.body as { models: { id: string; apiType: string; contextLength: number; maxOutputTokens: number; reasoningLevels: string[] }[] }).models;
  assert.deepEqual(rows.map(row => [row.id, row.apiType]), [["gpt-6.2-sol", "responses"], ["gpt-6.2-astra", "responses"]]);
  assert.ok(rows.every(row => row.contextLength === 1_050_000 && row.maxOutputTokens === 128000));
  assert.ok(rows.every(row => row.reasoningLevels.join(",") === "default,low,medium,high,xhigh,max"));
});

test("Codex descriptors retain their advertised context and supported efforts", async () => {
  const catalog = makeCatalog([{ id: "openai", name: "OpenAI", origin: "https://api.openai.com/v1" }]);
  const credential = JSON.stringify({ kind: "openai-oauth", accessToken: "fixture-access", refreshToken: "fixture-refresh" });
  const result = await fetchModelsForProvider({ providerId: "openai", catalog, secretStore: makeSecrets({ openai: credential }), fetchFn: async () => jsonResponse(200, { models: [{ slug: "gpt-6-astra", context_window: 272000, supported_reasoning_levels: [{ effort: "low" }, { effort: "high" }, { effort: "xhigh" }, { effort: "max" }] }] }) });
  assert.equal(result.status, 200);
  assert.equal((result.body as any).models[0].contextLength, 272000);
  assert.equal((result.body as any).models[0].reasoningLevels.includes("none"), false);
  const normalized = normalizeProviderModels({ models: [{ slug: "model-x", supported_reasoning_levels: [{ effort: "high" }, { effort: "xhigh" }, { effort: "max" }] }] })!;
  assert.ok(normalized.models[0]?.reasoningLevels.includes("max"));
});

test("fetch-models uses the Go catalog and separate stable sessions for both accounts", async () => {
  const catalog = makeCatalog([
    { id: "opencode", name: "Go 1", origin: "https://opencode.ai/zen/go/v1" },
    { id: "opencode-2", name: "Go 2", origin: "https://opencode.ai/inference/go/anthropic/v1" },
  ]);
  const sessions = new Map<string, string>();
  const secretStore = makeSecrets({ opencode: "fixture-go-1", "opencode-2": "fixture-go-2" });
  for (const [providerId, url, token] of [
    ["opencode", "https://opencode.ai/inference/go/v1/models", "fixture-go-1"],
    ["opencode-2", "https://opencode.ai/inference/go/v1/models", "fixture-go-2"],
  ] as const) {
    const fetchFn: FetchLike = async (requested, init) => {
      assert.equal(requested, url);
      assert.equal(init?.headers?.Authorization, `Bearer ${token}`);
      const session = init?.headers?.["x-opencode-session"];
      assert.ok(session);
      if (sessions.has(providerId)) assert.equal(session, sessions.get(providerId));
      sessions.set(providerId, session);
      return jsonResponse(200, { data: [{ id: "deepseek-v4.1-flash" }] });
    };
    for (let attempt = 0; attempt < 2; attempt++) {
      assert.equal((await fetchModelsForProvider({ providerId, catalog, secretStore, fetchFn })).status, 200);
    }
  }
  assert.notEqual(sessions.get("opencode"), sessions.get("opencode-2"));
});

test("both direct Go lists preserve advertised chat capabilities and independent session headers", async () => {
  const catalog = makeCatalog([
    { id: "opencode", name: "Go 1", origin: "https://opencode.ai/zen/go/v1" },
    { id: "opencode-2", name: "Go 2", origin: "https://opencode.ai/inference/go" },
  ]);
  const secretStore = makeSecrets({ opencode: "fixture-go-1", "opencode-2": "fixture-go-2" });
  const sessions = new Map<string, string>();
  const fixture = {
    data: [
      { id: "muse-spark-1.3-contributor", type: "chat", recommended_api: "responses",
        limit: { context: 1048576, output: 131072 },
        modalities: { input: ["text", "image/jpeg", "image/png", "application/pdf", "video/mp4", "audio/wav"] },
        capabilities: { reasoning: { supported: true, effort: ["xhigh", "low", "minimal", "medium", "high"] } } },
      { id: "qwen4-max", type: "chat", recommended_api: "messages",
        limit: { output: 262144 }, capabilities: { reasoning: { supported: true, toggle: true, effort: ["high", "xhigh"] } } },
      { id: "plain-chat", type: "chat", recommended_api: "chat-completions", capabilities: { reasoning: { supported: false } } },
      { id: "interactions-chat", type: "chat", recommended_api: "interactions" },
      { id: "kept-embedding", type: "embedding", recommended_api: "embeddings" },
      { id: "kept-decision", type: "decision", recommended_api: "systemone" },
    ],
  };
  for (const [index, provider] of catalog.providers.entries()) {
    const fetchFn: FetchLike = async (url, init) => {
      assert.equal(url, "https://opencode.ai/inference/go/v1/models");
      assert.equal(init?.headers?.Authorization, `Bearer fixture-go-${index + 1}`);
      assert.equal(init?.headers?.["User-Agent"], "OpenBot/catalog");
      const session = init?.headers?.["x-opencode-session"];
      assert.ok(session);
      if (sessions.has(provider.id)) assert.equal(session, sessions.get(provider.id));
      sessions.set(provider.id, session);
      return jsonResponse(200, fixture);
    };
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = await fetchModelsForProvider({ providerId: provider.id, catalog, secretStore, fetchFn });
      assert.equal(result.status, 200);
      const { models } = result.body as { models: NonNullable<ReturnType<typeof normalizeProviderModels>>["models"] };
      assert.equal(models.length, 6);
      assert.deepEqual(models[0], { id: "muse-spark-1.3-contributor", name: null, contextLength: 1048576,
        maxOutputTokens: 131072, modalities: ["text", "image", "video", "audio"],
        reasoningLevels: ["default", "minimal", "low", "medium", "high", "xhigh"], apiType: "responses", modelType: "chat", providerMetadata: "opencode-go" });
      assert.equal(models[1]?.apiType, "anthropic");
      assert.equal(models[1]?.maxOutputTokens, 262144);
      assert.deepEqual(models[1]?.reasoningLevels, ["default", "none", "high", "xhigh"]);
      assert.equal(models[2]?.apiType, "chat-completions");
      assert.deepEqual(models[2]?.reasoningLevels, ["default"]);
      assert.equal(models[3]?.apiType, undefined);
      assert.deepEqual(models.slice(4).map(row => [row.modelType, row.apiType, row.reasoningLevels]), [
        ["embedding", undefined, []], ["decision", undefined, []],
      ]);
    }
  }
  assert.equal(new Set(sessions.values()).size, 2);
  const generic = normalizeProviderModels(fixture)?.models[0];
  assert.equal(generic?.contextLength, null);
  assert.equal(generic?.apiType, undefined);
  assert.deepEqual(generic?.reasoningLevels, []);
});

test("fetch-models returns 404 provider-not-found", async () => {
  const result = await fetchModelsForProvider({
    providerId: "missing",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({}),
  });
  assert.equal(result.status, 404);
  assert.deepEqual(result.body, { error: { kind: "provider-not-found", message: "provider not found" } });
});

test("fetch-models returns 409 no-secret when the provider has no key", async () => {
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({}),
  });
  assert.equal(result.status, 409);
  assert.deepEqual(result.body, { error: { kind: "no-secret", message: "no API key stored for this provider" } });
});

test("fetch-models maps upstream 401 to unauthorized", async () => {
  const fetchFn: FetchLike = async () => jsonResponse(401, { error: { message: "bad key" } });
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 401);
  assert.deepEqual(result.body, {
    error: { kind: "unauthorized", message: "provider rejected the API key", upstreamStatus: 401 },
  });
});

test("fetch-models maps upstream 404 to not-supported", async () => {
  const fetchFn: FetchLike = async () => jsonResponse(404, {});
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 502);
  assert.deepEqual(result.body, {
    error: { kind: "not-supported", message: "provider does not expose a model list", upstreamStatus: 404 },
  });
});

test("fetch-models maps a network failure to unreachable without upstreamStatus", async () => {
  const fetchFn: FetchLike = async () => {
    throw new Error("connect ECONNREFUSED");
  };
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 502);
  assert.deepEqual(result.body, { error: { kind: "unreachable", message: "provider is unreachable" } });
});

test("fetch-models maps an upstream 503 to unreachable with upstreamStatus", async () => {
  const fetchFn: FetchLike = async () => jsonResponse(503, {});
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 502);
  assert.deepEqual(result.body, {
    error: { kind: "unreachable", message: "upstream returned HTTP 503", upstreamStatus: 503 },
  });
});

test("fetch-models maps a 200 non-list body to parse-error", async () => {
  const fetchFn: FetchLike = async () => jsonResponse(200, { something: "else" });
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 502);
  assert.deepEqual(result.body, { error: { kind: "parse-error", message: "provider response is not a model list" } });
});

test("fetch-models maps a 200 non-JSON body to parse-error", async () => {
  const fetchFn: FetchLike = async () => textResponse(200, "<html>not json</html>");
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "https://example.com/v1" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
    fetchFn,
  });
  assert.equal(result.status, 502);
  assert.deepEqual(result.body, { error: { kind: "parse-error", message: "provider returned a non-JSON body" } });
});

test("fetch-models maps an unexpected failure to 500 internal", async () => {
  const result = await fetchModelsForProvider({
    providerId: "zhipu",
    catalog: makeCatalog([{ id: "zhipu", name: "Zhipu", origin: "" }]),
    secretStore: makeSecrets({ zhipu: "sk-live" }),
  });
  assert.equal(result.status, 500);
  const body = result.body as { error: { kind: string } };
  assert.equal(body.error.kind, "internal");
});

test("second OAuth family discovers its own catalog with token and account headers", async () => {
  const catalog = makeCatalog([{ id: "openai-2", name: "OpenAI", origin: "https://api.openai.com/v1" }]);
  const secret = JSON.stringify({ kind: "openai-oauth", accessToken: "account-two-access", refreshToken: "account-two-refresh", expiresAt: 1900000000, chatgptAccountId: "account-two" });
  const result = await fetchModelsForProvider({ providerId: "openai-2", catalog, secretStore: makeSecrets({ "openai-2": secret }), fetchFn: async (url, init) => {
    assert.equal(url, OPENAI_CODEX_MODELS_URL);
    assert.equal(init?.headers?.Authorization, "Bearer account-two-access");
    assert.equal(init?.headers?.["chatgpt-account-id"], "account-two");
    return jsonResponse(200, { models: [{ slug: "gpt-6.1-sol", context_window: 272000 }] });
  } });
  assert.equal(result.status, 200);
  const body = result.body as { models: { id: string; contextLength: number; reasoningLevels: string[] }[] };
  assert.equal(body.models[0]?.contextLength, 272000);
  assert.ok(body.models[0]?.reasoningLevels.includes("max"));
});

test("an expired imported login refreshes before listing models and preserves the rotated account header", async () => {
  const catalog = makeCatalog([{ id: "openai-2", name: "OpenAI", origin: "https://api.openai.com/v1" }]);
  const secret = JSON.stringify({ kind: "openai-oauth", accessToken: "expired-access", refreshToken: "fixture-refresh", expiresAt: 0 });
  let refreshed = false;
  const result = await fetchModelsForProvider({ providerId: "openai-2", catalog, secretStore: makeSecrets({ "openai-2": secret }),
    refreshOAuth: async id => {
      assert.equal(id, "openai-2"); refreshed = true;
      return JSON.stringify({ kind: "openai-oauth", accessToken: "fresh-access", refreshToken: "next-refresh", expiresAt: 1900000000, chatgptAccountId: "rotated-account" });
    }, fetchFn: async (_url, init) => {
      assert.equal(refreshed, true);
      assert.equal(init?.headers?.Authorization, "Bearer fresh-access");
      assert.equal(init?.headers?.["chatgpt-account-id"], "rotated-account");
      return jsonResponse(200, { models: [{ slug: "gpt-6.1-sol" }] });
    } });
  assert.equal(result.status, 200);
  const failed = await fetchModelsForProvider({ providerId: "openai-2", catalog, secretStore: makeSecrets({ "openai-2": secret }),
    refreshOAuth: async () => { throw new Error("sensitive-refresh-fixture"); }, fetchFn: async () => { throw new Error("must not fetch after failed refresh"); } });
  assert.equal(failed.status, 401);
  assert.doesNotMatch(JSON.stringify(failed), /sensitive-refresh-fixture/);
});
