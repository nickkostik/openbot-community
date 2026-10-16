import assert from "node:assert/strict";
import test from "node:test";
import { CredentialDrafts, MAX_CREDENTIAL_FILE_BYTES, normalizeProviderCredential } from "./credential-import.ts";

const origin = "https://api.openai.com/v1";
const jwt = (claims: unknown) => `header.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.signature`;

test("Codex auth.json preserves account identity, derives JWT expiry, and drops unrelated secrets", () => {
  const access = jwt({ exp: 1900000000, "https://api.openai.com/auth": { chatgpt_account_id: "account-two" } });
  const result = JSON.parse(normalizeProviderCredential(JSON.stringify({
    auth_mode: "chatgpt", tokens: { access_token: access, refresh_token: "refresh-two" }, unrelated: "must-not-persist",
  }), origin));
  assert.deepEqual(result, { kind: "openai-oauth", accessToken: access, refreshToken: "refresh-two", expiresAt: 1900000000, chatgptAccountId: "account-two" });
});

test("OpenCode saved OpenAI login converts millisecond expiry and retains explicit account id", () => {
  assert.deepEqual(JSON.parse(normalizeProviderCredential(JSON.stringify({ type: "oauth", access: "access", refresh: "refresh", expires: 1900000000000, accountId: "account-two" }), origin)),
    { kind: "openai-oauth", accessToken: "access", refreshToken: "refresh", expiresAt: 1900000000, chatgptAccountId: "account-two" });
  assert.equal(normalizeProviderCredential(JSON.stringify({ type: "api", key: " sk-api-fixture " }), origin), "sk-api-fixture");
  assert.equal(normalizeProviderCredential(JSON.stringify({ OPENAI_API_KEY: "sk-api-fixture" }), origin), "sk-api-fixture");
});

test("Codex import keeps the access-token account when the ID token omits or blanks it", () => {
  const access = jwt({ exp: 1900000000, "https://api.openai.com/auth": { chatgpt_account_id: "account-two" } });
  for (const auth of [{}, { chatgpt_account_id: "  " }]) {
    const tokens = { access_token: access, refresh_token: "refresh-two", id_token: jwt({ "https://api.openai.com/auth": auth }) };
    const result = JSON.parse(normalizeProviderCredential(JSON.stringify({ tokens }), origin));
    assert.equal(result.chatgptAccountId, "account-two");
    assert.equal(result.accessToken, access);
    assert.equal(result.refreshToken, "refresh-two");
    assert.equal(result.expiresAt, 1900000000);
    const explicit = JSON.parse(normalizeProviderCredential(JSON.stringify({ tokens: { ...tokens, account_id: "explicit-account" } }), origin));
    assert.equal(explicit.chatgptAccountId, "explicit-account", "explicit imported identity keeps precedence");
  }
});

test("saved login without a usable expiry requires refresh and malformed JWT claims are safe", () => {
  for (const accessToken of ["opaque-access", jwt(null), jwt([])]) {
    const result = JSON.parse(normalizeProviderCredential(JSON.stringify({ accessToken, refreshToken: "refresh" }), origin));
    assert.equal(result.expiresAt, 0);
  }
  assert.throws(() => normalizeProviderCredential(JSON.stringify({ accessToken: "access" }), origin), /access and refresh/);
});

test("OAuth imports reject proxies, malformed JSON and oversized UTF-8 without echoing secrets", () => {
  const credential = JSON.stringify({ accessToken: "fixture-sensitive-access", refreshToken: "fixture-sensitive-refresh" });
  for (const url of ["https://n5.example/v1", "http://api.openai.com/v1", "https://chatgpt.com/unrelated", "https://api.openai.com.other.invalid/v1"]) {
    assert.throws(() => normalizeProviderCredential(credential, url), /direct OpenAI endpoint/);
  }
  assert.doesNotThrow(() => normalizeProviderCredential(credential, "https://chatgpt.com/backend-api/codex"));
  assert.throws(() => normalizeProviderCredential('{"accessToken":"fixture-sensitive-access",', origin), error =>
    error instanceof Error && error.message === "Credential file is not valid JSON.");
  assert.throws(() => normalizeProviderCredential("é".repeat(MAX_CREDENTIAL_FILE_BYTES / 2 + 1), origin), /256 KiB/);
});

test("login drafts remain private until saved, expire, can be retried, and have bounded capacity", () => {
  let now = 1000;
  const drafts = new CredentialDrafts(() => now, 2);
  const credential = { kind: "openai-oauth" as const, accessToken: "fixture-access", refreshToken: "fixture-refresh", expiresAt: 1900000000 };
  const first = drafts.stage(credential);
  assert.deepEqual(Object.keys(first).sort(), ["credentialId", "expiresIn"]);
  assert.doesNotMatch(JSON.stringify(first), /fixture-/);
  assert.deepEqual(JSON.parse(drafts.read(first.credentialId)), credential);
  assert.equal(drafts.read(first.credentialId), drafts.read(first.credentialId), "a refused save does not consume the draft");
  drafts.stage(credential);
  assert.throws(() => drafts.stage(credential), /Too many pending/);
  drafts.remove(first.credentialId);
  assert.throws(() => drafts.read(first.credentialId), /expired/);
  const final = drafts.stage(credential);
  now += 1800000;
  assert.throws(() => drafts.read(final.credentialId), /expired/);
  assert.doesNotThrow(() => drafts.stage(credential));
});
