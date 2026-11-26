export const OPENCODE_GO_ORIGIN = "https://opencode.ai/inference/go";

export function isOpenCodeGoConsoleOrigin(origin: string): boolean {
  return /^https:\/\/opencode\.ai\/inference\/go(?:\/(?:openai|anthropic)\/v1)?\/*$/.test(origin);
}

export function isOpenCodeGoOrigin(origin: string): boolean {
  return isOpenCodeGoConsoleOrigin(origin) || /^https:\/\/opencode\.ai\/zen\/go(?:\/v1)?\/*$/.test(origin);
}
