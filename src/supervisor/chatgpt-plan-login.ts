import { createHash, createPublicKey, randomBytes, randomUUID, verify } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  ORIGIN as CHATGPT_PLAN_ORIGIN, TOKEN_ENDPOINT as TOKEN, SCOPES as REQUIRED_SCOPES,
  hasPlanScopes, isClientId, type ChatGPTPlanCredential,
} from "../../payload/chatgpt-plan.cjs";

const ISSUER = "https://auth.openai.com";
const AUTHORIZE = `${ISSUER}/api/accounts/authorize`;
const JWKS = `${ISSUER}/.well-known/jwks.json`;
const REGISTRATION_CLIENT = "dynamic_agent_client";
const SIGN_IN_TIMEOUT_MS = 30 * 60 * 1000;

type Attempt = {
  nonce: string;
  verifier: string;
  state: string;
  redirectUri: string;
  previous: Pick<ChatGPTPlanCredential, "clientId" | "subject"> | undefined;
  providerId: string | undefined;
  expiresAt: number;
  phase: "pending" | "exchanging" | "ready" | "failed";
  result?: string;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

export function loadChatGPTPlanHostId(directory: string): string {
  const file = path.join(directory, "openbot-chatgpt-host-id");
  try { writeFileSync(file, `urn:uuid:${randomUUID()}\n`, { mode: 0o600, flag: "wx" }); }
  catch (error) {
    if (!(error instanceof Error && "code" in error && error.code === "EEXIST")) throw error;
  }
  const id = readFileSync(file, "utf8").trim();
  if (!/^urn:uuid:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id)) throw new Error("OpenBot's ChatGPT host ID is invalid.");
  return id;
}

function decodeSegment(part: string): unknown {
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
}

async function checkedSubject(token: string, clientId: string, nonce: string, fetchFn: typeof fetch): Promise<string> {
  const parts = token.split(".");
  const invalid = "OpenAI returned an invalid ChatGPT identity.";
  if (token.length > 32768 || parts.length !== 3 || parts.some(part => !/^[A-Za-z0-9_-]+$/.test(part))) throw new Error(invalid);
  const [header, claims] = [decodeSegment(parts[0]!), decodeSegment(parts[1]!)];
  const now = Math.floor(Date.now() / 1000);
  if (!record(header) || header.alg !== "RS256" || !nonempty(header.kid) || !record(claims) ||
      claims.iss !== ISSUER || claims.nonce !== nonce || !nonempty(claims.sub) || typeof claims.exp !== "number" || !Number.isFinite(claims.exp) || claims.exp <= now ||
      (claims.nbf !== undefined && (typeof claims.nbf !== "number" || claims.nbf > now)) ||
      !(claims.aud === clientId || (Array.isArray(claims.aud) && claims.aud.includes(clientId) && (claims.aud.length === 1 || claims.azp === clientId))) ||
      (claims.azp !== undefined && claims.azp !== clientId)) throw new Error(invalid);
  const response = await fetchFn(JWKS, { redirect: "error", signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error("OpenAI identity verification is unavailable.");
  const keys: unknown = await response.json();
  const matching = record(keys) && Array.isArray(keys.keys) ? keys.keys.filter(key => record(key) && key.kid === header.kid &&
    key.kty === "RSA" && (key.use === undefined || key.use === "sig") && (key.alg === undefined || key.alg === "RS256")) : [];
  if (matching.length !== 1) throw new Error(invalid);
  const key = createPublicKey({ key: matching[0], format: "jwk" });
  if (!verify("RSA-SHA256", Buffer.from(`${parts[0]}.${parts[1]}`, "ascii"), key, Buffer.from(parts[2]!, "base64url"))) throw new Error(invalid);
  return claims.sub;
}

export class ChatGPTPlanLogin {
  private readonly attempts = new Map<string, Attempt>();

  start(redirectUri: string, hostId: string, providerId?: string, previous?: ChatGPTPlanCredential) {
    const callback = new URL(redirectUri);
    if (callback.protocol !== "http:" || callback.host !== `127.0.0.1:${callback.port}` || !callback.port ||
        callback.pathname !== "/auth/callback" || callback.username || callback.password || callback.search || callback.hash) {
      throw new Error("ChatGPT sign-in needs an OpenBot loopback callback.");
    }
    const now = Date.now();
    for (const [id, attempt] of this.attempts) if (attempt.expiresAt <= now) this.attempts.delete(id);
    if (this.attempts.size >= 32) throw new Error("Too many pending ChatGPT sign-ins. Finish one or wait for it to expire.");
    const attempt: Attempt = { state: randomBytes(32).toString("hex"), nonce: randomBytes(32).toString("hex"),
      verifier: randomBytes(48).toString("base64url"), redirectUri,
      previous: previous ? { clientId: previous.clientId, subject: previous.subject } : undefined,
      providerId, expiresAt: now + SIGN_IN_TIMEOUT_MS, phase: "pending" };
    const sessionId = randomUUID();
    this.attempts.set(sessionId, attempt);
    const query = new URLSearchParams({ client_id: previous?.clientId ?? REGISTRATION_CLIENT, ext_agent_host_id: hostId,
      redirect_uri: redirectUri, response_type: "code", resource: CHATGPT_PLAN_ORIGIN,
      scope: `openid profile email offline_access ${REQUIRED_SCOPES.join(" ")}`, state: attempt.state, nonce: attempt.nonce,
      code_challenge_method: "S256", code_challenge: createHash("sha256").update(attempt.verifier).digest("base64url") });
    if (!previous) query.set("agent_name_hint", "OpenBot");
    return { sessionId, authorizationUrl: `${AUTHORIZE}?${query}`, expiresIn: SIGN_IN_TIMEOUT_MS / 1000 };
  }

  status(sessionId: string, redirectUri: string) {
    const attempt = this.attempts.get(sessionId);
    if (!attempt || attempt.expiresAt <= Date.now() || attempt.redirectUri !== redirectUri) return { phase: "expired" } as const;
    if (attempt.phase === "ready") {
      return { phase: "ready", credentialId: attempt.result! } as const;
    }
    if (attempt.phase === "failed") return { phase: "failed", error: attempt.result! } as const;
    return { phase: "pending" } as const;
  }

  async callback(url: URL, stage: (credential: ChatGPTPlanCredential, providerId?: string) => string, fetchFn: typeof fetch = fetch): Promise<boolean> {
    const state = url.searchParams.get("state");
    const redirectUri = url.origin + url.pathname;
    const match = [...this.attempts].find(([, attempt]) => attempt.state === state && attempt.redirectUri === redirectUri &&
      attempt.phase === "pending" && attempt.expiresAt > Date.now());
    if (!match) return false;
    const [sessionId, attempt] = match;
    attempt.phase = "exchanging";
    try {
      if (url.searchParams.has("error")) throw new Error("ChatGPT sign-in was declined or cancelled.");
      const code = url.searchParams.get("code");
      const returnedClientId = url.searchParams.get("client_id");
      const clientId = attempt.previous?.clientId ?? returnedClientId;
      if (!code || !isClientId(clientId) || (returnedClientId && returnedClientId !== clientId)) {
        throw new Error("OpenAI did not return the expected OpenBot registration.");
      }
      const response = await fetchFn(TOKEN, { method: "POST", redirect: "error", signal: AbortSignal.timeout(15000),
        headers: { accept: "application/json", "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ grant_type: "authorization_code", client_id: clientId, code, code_verifier: attempt.verifier,
          redirect_uri: attempt.redirectUri, resource: CHATGPT_PLAN_ORIGIN }) });
      if (!response.ok) throw new Error(`OpenAI rejected sign-in (HTTP ${response.status}). Start again.`);
      const payload: unknown = await response.json();
      if (!record(payload) || !nonempty(payload.access_token) || !nonempty(payload.refresh_token) || !nonempty(payload.id_token) ||
          typeof payload.expires_in !== "number" || !Number.isFinite(payload.expires_in) || payload.expires_in <= 0) {
        throw new Error("OpenAI returned an incomplete ChatGPT login.");
      }
      const scopes = typeof payload.scope === "string" ? payload.scope.split(/\s+/).filter(Boolean) : [];
      if (!hasPlanScopes(scopes)) throw new Error("ChatGPT plan usage permission was not granted.");
      let subject: string;
      try { subject = await checkedSubject(payload.id_token, clientId, attempt.nonce, fetchFn); }
      catch { throw new Error("OpenAI's ChatGPT account identity could not be verified."); }
      if (attempt.previous && attempt.previous.subject !== subject) throw new Error("Sign in to the ChatGPT account already linked to this provider.");
      attempt.result = stage({ kind: "chatgpt-plan", clientId, subject, idToken: payload.id_token, accessToken: payload.access_token,
        refreshToken: payload.refresh_token, expiresAt: Math.floor(Date.now() / 1000 + payload.expires_in), scopes }, attempt.providerId);
      attempt.phase = "ready";
      return true;
    } catch (error) {
      // Never return provider bodies, callback parameters, tokens, or HTTP client errors to the browser.
      const message = error instanceof Error ? error.message : "";
      attempt.result = /^(?:ChatGPT|OpenAI|Sign in to the ChatGPT)/.test(message) ? message : "ChatGPT sign-in could not be completed. Start again.";
      attempt.phase = "failed";
      return false;
    }
  }
}
