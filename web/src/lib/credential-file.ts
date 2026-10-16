export const MAX_CREDENTIAL_FILE_BYTES = 256 * 1024;
export type CredentialFileEntry = { id: string; label: string; value: string };

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function supported(value: Record<string, unknown>): boolean {
  return record(value.tokens) || value.kind === "openai-oauth" || value.type === "api" || value.type === "oauth" ||
    typeof value.OPENAI_API_KEY === "string" || typeof value.access_token === "string";
}

/** Select one entry from a login file; unrelated account tokens stay in the browser. */
export function credentialFileEntries(raw: string): CredentialFileEntry[] {
  if (new TextEncoder().encode(raw).length > MAX_CREDENTIAL_FILE_BYTES) throw new Error("Credential file exceeds 256 KiB.");
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { throw new Error("Credential file is not valid JSON."); }
  if (!record(parsed)) throw new Error("Credential file must contain an object.");
  if (supported(parsed)) return [{ id: "login", label: record(parsed.tokens) ? "Codex login" : "Saved credential", value: JSON.stringify(parsed) }];
  const entries: CredentialFileEntry[] = [];
  for (const [key, value] of Object.entries(parsed)) {
    if (!record(value) || !supported(value)) continue;
    if (entries.length >= 64) throw new Error("Credential file contains more than 64 accounts.");
    const label = /^[a-z][a-z0-9 ._/-]{0,79}$/i.test(key) ? key : `Account ${entries.length + 1}`;
    entries.push({ id: String(entries.length), label, value: JSON.stringify(value) });
  }
  if (!entries.length) throw new Error("No supported Codex or OpenCode credential entries were found.");
  return entries;
}
