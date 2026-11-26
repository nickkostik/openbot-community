import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ChatGPTPlanLogin, loadChatGPTPlanHostId } from "./chatgpt-plan-login.ts";
import { CredentialDrafts } from "./credential-import.ts";
import { ORIGIN, SCOPES, TOKEN_ENDPOINT, readCredential, type ChatGPTPlanCredential } from "../../payload/chatgpt-plan.cjs";

const ISSUER = "https://auth.openai.com";
const CALLBACK = "http://127.0.0.1:9280/auth/callback";
const HOST = "urn:uuid:00000000-1111-4222-8333-000000000001";
const CLIENT = "oaiapp_fixture";
const SUBJECT = "subject-fixture";
const keys = generateKeyPairSync("rsa", { modulusLength: 2048 });

function fixture(attempt: { authorizationUrl: string }, options: { subject?: string; tamper?: boolean; scopes?: string[] } = {}) {
  const authorization = new URL(attempt.authorizationUrl);
  const callback = new URL(CALLBACK);
  callback.searchParams.set("state", authorization.searchParams.get("state")!);
  callback.searchParams.set("code", "code-fixture");
  callback.searchParams.set("client_id", CLIENT);
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const header = encode({ alg: "RS256", kid: "key-fixture" });
  const claims = { iss: ISSUER, aud: CLIENT, sub: options.subject ?? SUBJECT, nonce: authorization.searchParams.get("nonce"),
    exp: Math.floor(Date.now() / 1000) + 3600 };
  const signature = sign("RSA-SHA256", Buffer.from(`${header}.${encode(claims)}`), keys.privateKey).toString("base64url");
  const idToken = `${header}.${encode(options.tamper ? { ...claims, sub: "tampered-subject" } : claims)}.${signature}`;
  const calls: string[] = [];
  const fetchFn: typeof fetch = async (input, init) => {
    calls.push(String(input));
    assert.equal(init?.redirect, "error");
    if (input === TOKEN_ENDPOINT) {
      const body = init?.body;
      assert.ok(body instanceof URLSearchParams);
      assert.equal(body.get("redirect_uri"), CALLBACK);
      assert.equal(body.get("client_id"), CLIENT);
      assert.equal(body.get("resource"), ORIGIN);
      assert.equal(body.get("grant_type"), "authorization_code");
      assert.equal(body.get("code"), "code-fixture");
      assert.equal(createHash("sha256").update(body.get("code_verifier")!).digest("base64url"), authorization.searchParams.get("code_challenge"));
      return Response.json({ access_token: "access-fixture", refresh_token: "refresh-fixture", id_token: idToken,
        expires_in: 3600, scope: (options.scopes ?? SCOPES).join(" ") });
    }
    assert.equal(input, `${ISSUER}/.well-known/jwks.json`);
    return Response.json({ keys: [{ ...keys.publicKey.export({ format: "jwk" }), kid: "key-fixture", use: "sig", alg: "RS256" }] });
  };
  return { authorization, callback, calls, fetchFn };
}

test("OpenBot registers with a stable host, signed identity, and a draft bound to the target provider", async () => {
  const login = new ChatGPTPlanLogin();
  const drafts = new CredentialDrafts();
  const attempt = login.start(CALLBACK, HOST, "openai-2");
  const { authorization, callback, calls, fetchFn } = fixture(attempt);
  assert.equal(authorization.origin + authorization.pathname, `${ISSUER}/api/accounts/authorize`);
  assert.equal(authorization.searchParams.get("client_id"), "dynamic_agent_client");
  assert.equal(authorization.searchParams.get("agent_name_hint"), "OpenBot");
  assert.equal(authorization.searchParams.get("ext_agent_host_id"), HOST);
  assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
  assert.deepEqual(login.status(attempt.sessionId, CALLBACK), { phase: "pending" });
  const invalidState = new URL(callback);
  invalidState.searchParams.set("state", "wrong-state");
  const stage = (credential: ChatGPTPlanCredential, providerId?: string) => drafts.stage(credential, providerId).credentialId;
  assert.equal(await login.callback(invalidState, stage, fetchFn), false);
  assert.equal(calls.length, 0);
  assert.equal(await login.callback(callback, stage, fetchFn), true);
  assert.equal(await login.callback(callback, stage, fetchFn), false);
  assert.equal(calls.length, 2);
  const status = login.status(attempt.sessionId, CALLBACK);
  assert.equal(status.phase, "ready");
  if (status.phase !== "ready") return;
  assert.doesNotMatch(JSON.stringify(status), /access-fixture|refresh-fixture/);
  assert.throws(() => drafts.read(status.credentialId, "openai"), /different provider/);
  const credential = readCredential(drafts.read(status.credentialId, "openai-2"));
  assert.equal(credential?.clientId, CLIENT);
  assert.equal(credential?.subject, SUBJECT);
  assert.equal(credential?.accessToken, "access-fixture");
  assert.deepEqual(login.status(attempt.sessionId, CALLBACK), status);
});

test("renewal rejects a changed account, missing usage permission, and an invalid ID token", async () => {
  const login = new ChatGPTPlanLogin();
  const previous: ChatGPTPlanCredential = { kind: "chatgpt-plan", clientId: CLIENT, subject: SUBJECT, accessToken: "old-fixture",
    refreshToken: "old-refresh-fixture", idToken: "old-id-fixture", expiresAt: 1, scopes: [...SCOPES] };
  for (const options of [{ subject: "different-subject" }, { scopes: ["openid"] }, { tamper: true }]) {
    const attempt = login.start(CALLBACK, HOST, "openai", previous);
    const { authorization, callback, fetchFn } = fixture(attempt, options);
    assert.equal(authorization.searchParams.get("client_id"), CLIENT);
    assert.equal(authorization.searchParams.has("agent_name_hint"), false);
    callback.searchParams.delete("client_id");
    let staged = false;
    assert.equal(await login.callback(callback, () => { staged = true; return "unexpected"; }, fetchFn), false);
    assert.equal(staged, false);
    assert.equal(login.status(attempt.sessionId, CALLBACK).phase, "failed");
  }
});

test("the loopback host identity persists with private permissions", t => {
  const directory = mkdtempSync(path.join(os.tmpdir(), "openbot-chatgpt-host-fixture-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const host = loadChatGPTPlanHostId(directory);
  assert.match(host, /^urn:uuid:/);
  assert.equal(loadChatGPTPlanHostId(directory), host);
  assert.equal(statSync(path.join(directory, "openbot-chatgpt-host-id")).mode & 0o777, 0o600);
  assert.throws(() => new ChatGPTPlanLogin().start("https://remote.invalid/auth/callback", host), /loopback/);
});
