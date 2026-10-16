import { createHash, randomBytes, randomUUID } from "node:crypto";

export const OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
export const OPENAI_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback";
export const OPENAI_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize";
export const OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token";
type Session = { state: string; verifier: string; expiresAt: number };
const sessions = new Map<string, Session>();
export type OpenAIOAuthCredential = { kind: "openai-oauth"; accessToken: string; refreshToken: string; expiresAt: number; chatgptAccountId?: string };

function decodeJwtClaims(token: string): Record<string, unknown> {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return {};
    const value: unknown = JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf8"));
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function openAIOAuthAccountId(idToken: string, accessToken: string): string {
  for (const token of [idToken, accessToken]) {
    const auth = decodeJwtClaims(token)["https://api.openai.com/auth"];
    if (auth === null || typeof auth !== "object" || Array.isArray(auth)) continue;
    const value = (auth as Record<string, unknown>).chatgpt_account_id;
    const accountId = typeof value === "string" ? value.trim() : "";
    if (accountId) return accountId;
  }
  return "";
}

export function startOpenAIOAuth(): { sessionId: string; authorizationUrl: string; expiresIn: number } {
  for (const [id, entry] of sessions) if (entry.expiresAt <= Date.now()) sessions.delete(id);
  if (sessions.size >= 32) throw new Error("Too many pending OpenAI sign-ins. Finish one or wait for it to expire.");
  const sessionId = randomUUID();
  const state = randomBytes(32).toString("hex");
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const expiresIn = 1800;
  sessions.set(sessionId, { state, verifier, expiresAt: Date.now() + expiresIn * 1000 });
  const params = new URLSearchParams({ response_type: "code", client_id: OPENAI_OAUTH_CLIENT_ID, redirect_uri: OPENAI_OAUTH_REDIRECT_URI, scope: "openid profile email offline_access", state, code_challenge: challenge, code_challenge_method: "S256", prompt: "login", id_token_add_organizations: "true", codex_cli_simplified_flow: "true" });
  return { sessionId, authorizationUrl: OPENAI_OAUTH_AUTHORIZE_URL + "?" + params.toString(), expiresIn };
}

export function parseCallbackUrl(raw: string): { code: string; state: string } {
  const url = new URL(raw.trim());
  if (url.protocol !== "http:" || url.hostname !== "localhost" || url.port !== "1455" || url.pathname !== "/auth/callback") throw new Error("Callback URL must be http://localhost:1455/auth/callback");
  const error = url.searchParams.get("error_description") || url.searchParams.get("error");
  if (error) throw new Error("OpenAI OAuth failed: " + error);
  const code = url.searchParams.get("code")?.trim() || "";
  const state = url.searchParams.get("state")?.trim() || "";
  if (!code || !state) throw new Error("Callback URL is missing code or state");
  return { code, state };
}

export async function completeOpenAIOAuth(sessionId: string, callbackUrl: string, fetchFn: typeof fetch = fetch): Promise<OpenAIOAuthCredential> {
  const session = sessions.get(sessionId);
  if (!session || session.expiresAt <= Date.now()) throw new Error("OAuth session expired; start again");
  const callback = parseCallbackUrl(callbackUrl);
  if (callback.state !== session.state) throw new Error("OAuth state validation failed");
  const response = await fetchFn(OPENAI_OAUTH_TOKEN_URL, { method: "POST", signal: AbortSignal.timeout(15000), headers: { "content-type": "application/x-www-form-urlencoded", accept: "application/json" }, body: new URLSearchParams({ grant_type: "authorization_code", client_id: OPENAI_OAUTH_CLIENT_ID, code: callback.code, redirect_uri: OPENAI_OAUTH_REDIRECT_URI, code_verifier: session.verifier }) });
  let payload: Record<string, unknown>;
  try {
    payload = await response.json() as Record<string, unknown>;
  } catch {
    throw new Error("OpenAI token exchange returned a non-JSON body (HTTP " + response.status + ")");
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("OpenAI token exchange returned an unexpected body (HTTP " + response.status + ")");
  }
  if (!response.ok || typeof payload.access_token !== "string" || typeof payload.refresh_token !== "string") throw new Error("OpenAI token exchange failed (HTTP " + response.status + ")");
  sessions.delete(sessionId);
  const idToken = typeof payload.id_token === "string" ? payload.id_token : "";
  const accountId = openAIOAuthAccountId(idToken, payload.access_token);
  return { kind: "openai-oauth", accessToken: payload.access_token, refreshToken: payload.refresh_token, expiresAt: Math.floor(Date.now() / 1000) + Math.max(1, Number(payload.expires_in) || 3600), ...(accountId ? { chatgptAccountId: accountId } : {}) };
}
export function resetOpenAIOAuthForTests(): void { sessions.clear(); }
