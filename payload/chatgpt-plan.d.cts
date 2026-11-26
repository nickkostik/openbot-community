export type ChatGPTPlanCredential = {
  kind: "chatgpt-plan";
  clientId: string;
  subject: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  scopes: string[];
  idToken: string;
};
export const ORIGIN: string;
export const TOKEN_ENDPOINT: string;
export const SCOPES: readonly string[];
export function isDirectOrigin(origin: string): boolean;
export function isClientId(value: unknown): value is string;
export function hasPlanScopes(scopes: unknown): scopes is string[];
export function readCredential(raw: string | undefined): ChatGPTPlanCredential | undefined;
