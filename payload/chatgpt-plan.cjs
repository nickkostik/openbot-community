"use strict";

const ORIGIN = "https://api.openai.com/v1";
const TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token";
const SCOPES = ["resource.invoke", "chatgpt.tokens.use.direct"];

function isDirectOrigin(origin) {
  return /^https:\/\/api\.openai\.com(?:\/v1)?\/?$/.test(origin);
}

function isClientId(value) {
  return typeof value === "string" && /^oaiapp_[A-Za-z0-9_-]+$/.test(value) && value.length <= 256;
}

function hasPlanScopes(scopes) {
  return Array.isArray(scopes) && scopes.every(scope => typeof scope === "string") && SCOPES.every(scope => scopes.includes(scope));
}

function readCredential(raw) {
  if (typeof raw !== "string" || !raw.startsWith("{")) return undefined;
  let data;
  try { data = JSON.parse(raw); } catch { return undefined; }
  if (data?.kind !== "chatgpt-plan") return undefined;
  const fields = [data.subject, data.idToken, data.accessToken, data.refreshToken];
  if (!isClientId(data.clientId) || !fields.every(value => typeof value === "string" && value.trim()) ||
      !Number.isFinite(data.expiresAt) || data.expiresAt < 0 || !hasPlanScopes(data.scopes)) {
    throw new Error("ChatGPT plan credential is incomplete. Sign in again.");
  }
  return data;
}

module.exports = { ORIGIN, TOKEN_ENDPOINT, SCOPES, isDirectOrigin, isClientId, hasPlanScopes, readCredential };
