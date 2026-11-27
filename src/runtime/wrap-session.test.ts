import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const runtimePath = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/runtime.cjs");

test("wrapSession is sync, replaces stream, and throws without a binding", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-rt-"));
  const planPath = path.join(dir, "plan.json");
  writeFileSync(
    planPath,
    JSON.stringify({
      kind: "custom",
      agents: { "*": { modelId: "glm-5.3-flash", providerId: "zhipu" } },
      catalog: { providers: [], models: [], bindings: [] },
    }),
  );
  process.env.OPENBOT_PLAN = planPath;
  delete require.cache[runtimePath];
  const runtime = require(runtimePath) as {
    wrapSession: (stockFn: (...args: unknown[]) => unknown, args: unknown) => unknown;
    unwrapJsonSchemaTools: (tools: unknown[]) => { function: { parameters: { properties: Record<string, unknown> } } }[];
    mapToolCalls: (calls: unknown[]) => { toolName: string }[];
    defaultMaxTokens: (n: number | undefined, cap?: number) => number;
    resolveAgent: (args: unknown[]) => { modelId: string; maxOutputTokens: number } | null;
  };

  const tools = runtime.unwrapJsonSchemaTools([
    { name: "SendToUser", parameters: { jsonSchema: { type: "object", properties: { message: { type: "string" } } } } },
  ]);
  assert.equal(tools[0]?.function.parameters.properties.message !== undefined, true);

  const twice = runtime.mapToolCalls([
    { id: "a", function: { name: "SendToUser", arguments: "{}" } },
    { id: "b", function: { name: "SendToUser", arguments: "{}" } },
  ]);
  assert.equal(twice.length, 2);
  assert.equal(runtime.defaultMaxTokens(undefined), 65536);
  assert.equal(runtime.defaultMaxTokens(99999, 4096), 4096);

  function stockFn() {
    return {
      getSession() {
        return {
          getExecutor() {
            return { stream() { return { fullStream: (async function* () {})() }; }, getMessages() { return []; } };
          },
          getModelId() {
            return "grok";
          },
        };
      },
      getProviderName() {
        return "proto";
      },
      getModelId() {
        return "grok";
      },
      getThinkingDetails() {
        return undefined;
      },
    };
  }

  const provider = runtime.wrapSession(stockFn, [{ conversationId: "00000000-0000-4000-8000-9af645a8fef3" }]);
  assert.equal(typeof provider, "object");
  assert.equal(provider !== null && typeof (provider as { then?: unknown }).then, "undefined");
  const session = (provider as { getSession: (mw: unknown) => { getExecutor: () => { stream: unknown } } }).getSession(
    (exec: { stream: unknown }) => exec,
  );
  assert.equal(typeof session.getExecutor().stream, "function");

  writeFileSync(
    planPath,
    JSON.stringify({
      kind: "custom",
      agents: { "*": { modelId: "gpt-4.1", providerId: "openai" } },
      catalog: {
        providers: [],
        models: [{ id: "openai:gpt-4.1", providerId: "openai", slug: "gpt-4.1", maxOutputTokens: 2048 }],
        bindings: [],
      },
    }),
  );
  const agent = runtime.resolveAgent([{ conversationId: "00000000-0000-4000-8000-9af645a8fef3" }]);
  assert.equal(agent?.modelId, "gpt-4.1");
  assert.equal(agent?.maxOutputTokens, 2048);

  writeFileSync(planPath, JSON.stringify({ kind: "custom", agents: {}, catalog: { providers: [], models: [], bindings: [] } }));
  assert.throws(() => runtime.wrapSession(stockFn, [{}]), /no model binding/);
});

test("wrapSession chain re-resolves the plan on every stream and getModelId", async () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-rt-lazy-"));
  const planPath = path.join(dir, "plan.json");
  const assignmentsPath = path.join(dir, "openbot-bot-models.json");
  const botId = "00000000-0000-4000-8000-000000000001";
  const assign = (modelId?: string) => writeFileSync(
    assignmentsPath, JSON.stringify({ assignments: modelId ? { [botId]: modelId } : {} }),
  );
  assign();
  const writePlan = (modelId: string) =>
    writeFileSync(
      planPath,
      JSON.stringify({
        kind: "custom",
        agents: { "*": { modelId, providerId: "p" } },
        catalog: {
          providers: [],
          models: [
            { id: "q:model-a", providerId: "q", slug: "model-a", maxOutputTokens: 4096 },
            { id: "p:model-a", providerId: "p", slug: "model-a", maxOutputTokens: 32768 },
            { id: "q:model-b", providerId: "q", slug: "model-b", maxOutputTokens: 65536 },
            { id: "p:model-b", providerId: "p", slug: "model-b", maxOutputTokens: 8192 },
          ],
          bindings: [],
        },
      }),
    );
  writePlan("model-a");

  const prevPlan = process.env.OPENBOT_PLAN;
  const prevBotModels = process.env.OPENBOT_BOT_MODELS;
  const prevHost = process.env.OPENBOT_HOP_HOST;
  const prevPort = process.env.OPENBOT_HOP_PORT;
  process.env.OPENBOT_PLAN = planPath;
  process.env.OPENBOT_BOT_MODELS = assignmentsPath;

  const seen: { model: string; max_tokens: number }[] = [];
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const { model, max_tokens } = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      seen.push({ model, max_tokens });
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.end('data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", () => resolve()));
  const addr = server.address();
  if (!addr || typeof addr === "string") throw new Error("no port");
  process.env.OPENBOT_HOP_HOST = "127.0.0.1";
  process.env.OPENBOT_HOP_PORT = String(addr.port);

  delete require.cache[runtimePath];
  const runtime = require(runtimePath) as {
    wrapSession: (stockFn: (...args: unknown[]) => unknown, args: unknown) => unknown;
  };
  try {
    function stockFn() {
      return {
        getSession() {
          return {
            getExecutor() {
              return {
                stream() {
                  throw new Error("stock stream should not run on custom wrap");
                },
                getMessages() {
                  return [
                    { role: "system", content: `Profile /home/box/agent-data/agents/${botId}/profile.json` },
                    { role: "user", content: "hi" },
                  ];
                },
              };
            },
            getModelId() {
              return "stock";
            },
          };
        },
        getProviderName() {
          return "proto";
        },
        getModelId() {
          return "stock";
        },
      };
    }

    const provider = runtime.wrapSession(stockFn, [{}]) as {
      getModelId: () => string;
      getSession: (mw: unknown) => { getExecutor: () => { stream: Function }; getModelId: () => string };
    };
    assert.equal(provider.getModelId(), "model-a");

    const executor = provider.getSession((exec: unknown) => exec).getExecutor();
    const drain = async (maxTokens = 70_000) => {
      const result = executor.stream({}, "inv-1", [], { maxTokens }) as {
        fullStream: AsyncIterable<unknown>;
      };
      for await (const _part of result.fullStream) {
        /* drain */
      }
    };
    await drain();
    assign("q:model-b");
    await drain();
    await drain(1200);
    assign("q:removed");
    await drain();
    assign();

    // Rewrite the plan without bouncing the host: the very next stream and
    // getModelId must use the new model.
    writePlan("model-b");
    assert.equal(provider.getModelId(), "model-b");
    const session = provider.getSession((exec: unknown) => exec);
    assert.equal(session.getModelId(), "model-b");
    await drain();
    assert.deepEqual(seen, [
      { model: "p:model-a", max_tokens: 32768 },
      { model: "q:model-b", max_tokens: 65536 },
      { model: "q:model-b", max_tokens: 1200 },
      { model: "p:model-a", max_tokens: 32768 },
      { model: "p:model-b", max_tokens: 8192 },
    ]);
  } finally {
    server.close();
    server.closeAllConnections();
    if (prevPlan === undefined) delete process.env.OPENBOT_PLAN;
    else process.env.OPENBOT_PLAN = prevPlan;
    if (prevBotModels === undefined) delete process.env.OPENBOT_BOT_MODELS;
    else process.env.OPENBOT_BOT_MODELS = prevBotModels;
    if (prevHost === undefined) delete process.env.OPENBOT_HOP_HOST;
    else process.env.OPENBOT_HOP_HOST = prevHost;
    if (prevPort === undefined) delete process.env.OPENBOT_HOP_PORT;
    else process.env.OPENBOT_HOP_PORT = prevPort;
  }
});
