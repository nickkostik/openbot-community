import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import test, { mock } from "node:test";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { ORIGIN, SCOPES, TOKEN_ENDPOINT } from "../../payload/chatgpt-plan.cjs";

const hopPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../payload/hop-handler.cjs");
const hop = createRequire(import.meta.url)(hopPath) as { loadKey: (providerId: string, provider: unknown, expectedCredentialKind?: string) => Promise<string>; resolveUpstreamUrl: (provider: { origin: string }, oauth: boolean, apiType: string) => string };

function expiredCredential(): string {
  return JSON.stringify({ kind: "openai-oauth", accessToken: "old-access", refreshToken: "refresh", expiresAt: 1 });
}

test("OpenAI OAuth resolves to the ChatGPT Codex Responses upstream", () => {
  assert.equal(hop.resolveUpstreamUrl({ origin: "https://api.openai.com/v1" }, true, "responses"), "https://chatgpt.com/backend-api/codex/responses");
  assert.equal(hop.resolveUpstreamUrl({ origin: "https://api.openai.com/v1" }, false, "chat-completions"), "https://api.openai.com/v1/chat/completions");
});

function installRefreshStub(t: test.TestContext, body: object | ((form: URLSearchParams) => object), url?: string): { calls: () => number } {
  let calls = 0;
  t.mock.method(https, "request", ((options: { hostname: string; path: string }, callback: (response: EventEmitter & { statusCode: number; headers: Record<string, string> }) => void) => {
    if (url) assert.equal(`https://${options.hostname}${options.path}`, url);
    calls += 1;
    const request = new EventEmitter() as EventEmitter & { setTimeout: (ms: number, fn: () => void) => void; write: (value: Buffer) => void; end: () => void; destroy: () => void };
    request.setTimeout = () => undefined;
    request.write = () => undefined;
    request.destroy = () => undefined;
    request.end = (payload?: Buffer) => {
      const response = new EventEmitter() as EventEmitter & { statusCode: number; headers: Record<string, string> };
      response.statusCode = 200;
      response.headers = {};
      callback(response);
      queueMicrotask(() => {
        response.emit("data", Buffer.from(JSON.stringify(typeof body === "function" ? body(new URLSearchParams(payload?.toString())) : body)));
        response.emit("end");
      });
    };
    return request;
  }) as never);
  return { calls: () => calls };
}

function withSecretsFile(t: test.TestContext): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-oauth-refresh-"));
  const file = path.join(dir, "secrets.json");
  process.env.OPENBOT_SECRETS = file;
  fs.writeFileSync(file, JSON.stringify({ providers: { openai: expiredCredential() } }));
  t.after(() => {
    delete process.env.OPENBOT_SECRETS;
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return file;
}

test("concurrent expired OAuth requests share one refresh and both use the new token", async (t) => {
  withSecretsFile(t);
  const stub = installRefreshStub(t, { access_token: "new-access", refresh_token: "new-refresh", expires_in: 3600 });
  const [first, second] = await Promise.all([hop.loadKey("openai", {}), hop.loadKey("openai", {})]);
  assert.equal(stub.calls(), 1);
  assert.equal(first, "new-access");
  assert.equal(second, "new-access");
});

test("multiple subscription accounts refresh independently without overwriting other secrets", async (t) => {
  const file = withSecretsFile(t);
  const credential = (id: string) => JSON.stringify({ kind: "openai-oauth", accessToken: `old-${id}`, refreshToken: `refresh-${id}`, expiresAt: 1, chatgptAccountId: id });
  fs.writeFileSync(file, JSON.stringify({ providers: { openai: credential("one"), "openai-2": credential("two"), "opencode-go": "go-key-fixture" } }));
  const stub = installRefreshStub(t, form => ({ access_token: "access-" + form.get("refresh_token"), refresh_token: "next-" + form.get("refresh_token"), expires_in: 3600 }));
  const results = await Promise.all([hop.loadKey("openai", {}), hop.loadKey("openai-2", {}), hop.loadKey("openai-2", {})]);
  assert.equal(stub.calls(), 2);
  assert.deepEqual(results, ["access-refresh-one", "access-refresh-two", "access-refresh-two"]);
  const saved = JSON.parse(fs.readFileSync(file, "utf8")).providers;
  assert.equal(JSON.parse(saved.openai).chatgptAccountId, "one");
  assert.equal(JSON.parse(saved["openai-2"]).chatgptAccountId, "two");
  assert.equal(JSON.parse(saved["openai-2"]).refreshToken, "next-refresh-two");
  assert.equal(saved["opencode-go"], "go-key-fixture");
});

test("concurrent OAuth refreshes fail closed when credential persistence fails", async (t) => {
  withSecretsFile(t);
  const stub = installRefreshStub(t, { access_token: "new-access", refresh_token: "new-refresh", expires_in: 3600 });
  const originalWriteFileSync = fs.writeFileSync;
  t.mock.method(fs, "writeFileSync", ((file: fs.PathLike, ...args: unknown[]) => {
    if (String(file).endsWith(".tmp")) throw new Error("read-only secrets");
    return (originalWriteFileSync as unknown as (...values: unknown[]) => unknown)(file, ...args);
  }) as never);
  const results = await Promise.allSettled([hop.loadKey("openai", {}), hop.loadKey("openai", {})]);
  assert.equal(stub.calls(), 1);
  assert.equal(results[0]?.status, "rejected");
  assert.equal(results[1]?.status, "rejected");
  assert.match(String((results[0] as PromiseRejectedResult).reason), /not persisted/);
  assert.match(fs.readFileSync(process.env.OPENBOT_SECRETS!, "utf8"), /old-access/);
});

test("ChatGPT plan refresh uses the issued registration and protects the direct destination", async (t) => {
  const file = withSecretsFile(t);
  const credential = { kind: "chatgpt-plan", clientId: "oaiapp_fixture", subject: "subject-fixture", idToken: "id-fixture",
    accessToken: "access-old-fixture", refreshToken: "refresh-old-fixture", scopes: SCOPES, expiresAt: 1 };
  fs.writeFileSync(file, JSON.stringify({ providers: { openai: JSON.stringify(credential), other: "other-fixture" } }));
  const stub = installRefreshStub(t, form => {
    assert.equal(form.get("client_id"), credential.clientId);
    assert.equal(form.get("resource"), ORIGIN);
    assert.equal(form.get("refresh_token"), credential.refreshToken);
    assert.equal(form.has("scope"), false);
    return { access_token: "new-access-fixture", refresh_token: "new-refresh-fixture", expires_in: 600 };
  }, TOKEN_ENDPOINT);
  await assert.rejects(hop.loadKey("openai", { origin: "https://relay.invalid/v1" }), /direct OpenAI API/);
  await assert.rejects(hop.loadKey("openai", { origin: ORIGIN }, "openai-oauth"), /login changed/);
  assert.equal(stub.calls(), 0);
  assert.deepEqual(await Promise.all([hop.loadKey("openai", { origin: ORIGIN }), hop.loadKey("openai", { origin: ORIGIN })]),
    ["new-access-fixture", "new-access-fixture"]);
  const saved = JSON.parse(fs.readFileSync(file, "utf8")).providers;
  assert.equal(stub.calls(), 1);
  assert.equal(JSON.parse(saved.openai).refreshToken, "new-refresh-fixture");
  assert.equal(JSON.parse(saved.openai).subject, credential.subject);
  assert.equal(saved.other, "other-fixture");
});
