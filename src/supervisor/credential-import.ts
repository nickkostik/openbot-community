import { randomUUID } from "node:crypto";
import { openAIOAuthAccountId, type OpenAIOAuthCredential } from "./openai-oauth.ts";

export const MAX_CREDENTIAL_FILE_BYTES = 256 * 1024;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function tokenExpiry(token: string): number {
  try {
    const claims = JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString("utf8")) as unknown;
    return record(claims) && typeof claims.exp === "number" && Number.isFinite(claims.exp) ? claims.exp : 0;
  } catch { return 0; }
}

/** Accept a selected credential entry, never an entire unrelated credentials store. */
export function normalizeProviderCredential(raw: string, origin: string): string {
  if (Buffer.byteLength(raw, "utf8") > MAX_CREDENTIAL_FILE_BYTES) throw new Error("Credential file exceeds 256 KiB.");
  const value = raw.trim();
  if (!value) throw new Error("Credential is empty.");
  if (!value.startsWith("{")) return value;
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error("Credential file is not valid JSON."); }
  if (!record(parsed)) throw new Error("Credential file must contain an object.");
  if (parsed.type === "api" && text(parsed.key)) return text(parsed.key);
  if (text(parsed.OPENAI_API_KEY) && !record(parsed.tokens)) return text(parsed.OPENAI_API_KEY);

  const tokens = record(parsed.tokens) ? parsed.tokens : parsed;
  const accessToken = text(tokens.access_token) || text(tokens.accessToken) || text(tokens.access);
  const refreshToken = text(tokens.refresh_token) || text(tokens.refreshToken) || text(tokens.refresh);
  if (!accessToken || !refreshToken) throw new Error("Select an API key entry or a Codex/OpenAI login with access and refresh tokens.");
  const url = new URL(origin);
  if (url.protocol !== "https:" || !(url.hostname === "api.openai.com" ||
      (url.hostname === "chatgpt.com" && url.pathname.startsWith("/backend-api/codex")))) {
    throw new Error("Codex/OpenAI OAuth requires a direct OpenAI endpoint.");
  }
  const expiryRaw = tokens.expiresAt ?? tokens.expires_at ?? tokens.expires ?? tokenExpiry(accessToken);
  const expiry = typeof expiryRaw === "number" && Number.isFinite(expiryRaw) && expiryRaw >= 0 ? expiryRaw : 0;
  const expiresAt = expiry > 1e11 ? Math.floor(expiry / 1000) : Math.floor(expiry);
  const idToken = text(tokens.id_token) || text(tokens.idToken);
  const accountId = text(tokens.account_id) || text(tokens.accountId) || text(tokens.chatgptAccountId) ||
    openAIOAuthAccountId(idToken, accessToken);
  const credential: OpenAIOAuthCredential = {
    kind: "openai-oauth", accessToken, refreshToken, expiresAt,
    ...(accountId ? { chatgptAccountId: accountId } : {}),
  };
  // No expiry means refresh before use, rather than inventing a token lifetime.
  return JSON.stringify(credential);
}

export class CredentialDrafts {
  private readonly entries = new Map<string, { secret: string; expiresAt: number }>();
  private readonly now: () => number;
  private readonly maxEntries: number;
  constructor(now: () => number = Date.now, maxEntries = 32) {
    this.now = now;
    this.maxEntries = maxEntries;
  }

  private prune(): void {
    for (const [id, entry] of this.entries) if (entry.expiresAt <= this.now()) this.entries.delete(id);
  }

  stage(credential: OpenAIOAuthCredential): { credentialId: string; expiresIn: number } {
    this.prune();
    if (this.entries.size >= this.maxEntries) throw new Error("Too many pending logins. Finish a login or wait for it to expire.");
    const credentialId = randomUUID();
    const expiresIn = 1800;
    this.entries.set(credentialId, { secret: JSON.stringify(credential), expiresAt: this.now() + expiresIn * 1000 });
    return { credentialId, expiresIn };
  }

  read(credentialId: string): string {
    this.prune();
    const entry = this.entries.get(credentialId);
    if (!entry) throw new Error("Login draft expired. Sign in again before adding the account.");
    return entry.secret;
  }

  remove(credentialId: string): void { this.entries.delete(credentialId); }
}
