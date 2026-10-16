import assert from "node:assert/strict";
import test from "node:test";
import { credentialFileEntries, MAX_CREDENTIAL_FILE_BYTES } from "./credential-file.ts";
import { PRESETS } from "./presets.ts";

test("a multi-account OpenCode file imports only the selected account", () => {
  const entries = credentialFileEntries(JSON.stringify({
    "opencode-go": { type: "api", key: "go-account-one" },
    "opencode-go-2": { type: "api", key: "go-account-two" },
    openai: { type: "oauth", access: "separate-access", refresh: "separate-refresh", expires: 1900000000000 },
    unsupported: { cookie: "unrelated-secret" },
  }));
  assert.equal(entries.length, 3);
  assert.deepEqual(entries.map(entry => entry.label), ["opencode-go", "opencode-go-2", "openai"]);
  assert.deepEqual(JSON.parse(entries[1]!.value), { type: "api", key: "go-account-two" });
  assert.doesNotMatch(entries[1]!.value, /go-account-one|separate-access|unrelated-secret/);
});

test("Codex auth.json is one import and errors never echo file contents", () => {
  const credential = { tokens: { access_token: "fixture-access", refresh_token: "fixture-refresh" } };
  assert.deepEqual(credentialFileEntries(JSON.stringify(credential)), [{ id: "login", label: "Codex login", value: JSON.stringify(credential) }]);
  assert.throws(() => credentialFileEntries('{"fixture-sensitive-secret'), error => error instanceof Error && error.message === "Credential file is not valid JSON.");
  assert.throws(() => credentialFileEntries("[]"), /object/);
  assert.throws(() => credentialFileEntries('{"cookie":"unrelated-secret"}'), /No supported/);
  assert.throws(() => credentialFileEntries("é".repeat(MAX_CREDENTIAL_FILE_BYTES / 2 + 1)), /256 KiB/);
  assert.throws(() => credentialFileEntries(JSON.stringify(Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`account-${i}`, { type: "api", key: "fixture" }])))), /64 accounts/);
});

test("new presets use direct Go and token-plan endpoints without choosing a model", () => {
  assert.equal(PRESETS.find(row => row.id === "opencode-go")?.origin, "https://opencode.ai/zen/go/v1");
  assert.equal(PRESETS.find(row => row.id === "alibaba-token-plan")?.origin, "https://coding-intl.dashscope.aliyuncs.com/v1");
  assert.ok(PRESETS.every(row => row.model === ""));
  assert.ok(PRESETS.every(row => row.id !== "opencode" && !/n5|metakey/i.test(row.origin)));
});
