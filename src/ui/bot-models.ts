import fs from "node:fs";
import path from "node:path";
import type http from "node:http";
import type { Catalog, ReasoningLevel } from "../domain/types.ts";
import type { SupervisorDeps } from "../supervisor/observe.ts";

const EFFORTS: readonly string[] = ["default", "none", "low", "medium", "high", "xhigh", "max"];
type Settings = { assignments: Record<string, string>; reasoning: Record<string, Record<string, ReasoningLevel>>; raw: Record<string, unknown> };
function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function safeKey(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && !["__proto__", "constructor", "prototype"].includes(value);
}
function filePath(current: SupervisorDeps): string {
  return process.env.OPENBOT_BOT_MODELS ?? path.join(current.paths.sandData, "openbot-bot-models.json");
}
function readSettings(current: SupervisorDeps): Settings {
  let raw: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(filePath(current), "utf8"));
    if (isRecord(parsed)) raw = parsed;
  } catch { /* Missing legacy file inherits model defaults. */ }
  const assignments = Object.fromEntries(Object.entries(isRecord(raw.assignments) ? raw.assignments : {})
    .filter(([id, value]) => safeKey(id) && typeof value === "string" && value.trim())) as Record<string, string>;
  const reasoning: Settings["reasoning"] = {};
  for (const [id, choices] of Object.entries(isRecord(raw.reasoning) ? raw.reasoning : {})) {
    if (!safeKey(id) || !isRecord(choices)) continue;
    reasoning[id] = Object.fromEntries(Object.entries(choices).filter(([modelId, value]) => safeKey(modelId) && typeof value === "string" && EFFORTS.includes(value))) as Record<string, ReasoningLevel>;
  }
  return { assignments, reasoning, raw };
}
function writeSettings(current: SupervisorDeps, settings: Settings): void {
  const target = filePath(current);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temp = `${target}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify({ ...settings.raw, assignments: settings.assignments, reasoning: settings.reasoning }, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, target);
}

export async function handleBotModelsApi(
  req: http.IncomingMessage, res: http.ServerResponse, url: URL, current: SupervisorDeps,
  readBody: (req: http.IncomingMessage) => Promise<string>,
  sendJson: (res: http.ServerResponse, status: number, payload: unknown) => void,
  catalogFromPlan: (raw: string | undefined) => Catalog,
): Promise<boolean> {
  if (url.pathname !== "/api/bot-models" || (req.method !== "GET" && req.method !== "PUT")) return false;
  if (req.method === "GET") {
    const settings = readSettings(current);
    sendJson(res, 200, { assignments: settings.assignments, reasoning: settings.reasoning, available: catalogFromPlan(current.fs.read(current.paths.plan)).models.map((model) => String(model.id)) });
    return true;
  }
  let body: unknown;
  try { body = JSON.parse(await readBody(req)); }
  catch { sendJson(res, 400, { error: "invalid json" }); return true; }
  const invalid = (error: string) => { sendJson(res, 400, { error }); return true; };
  if (!isRecord(body)) return invalid("expected botId/botIds and model or reasoning settings");
  // Read AFTER the asynchronous body read. From here through rename there is
  // no await, so another UI save cannot be lost between read and commit.
  const next = readSettings(current);
  const catalog = catalogFromPlan(current.fs.read(current.paths.plan));
  const available = catalog.models.map((model) => String(model.id));
  if ("assignments" in body) {
    if ("reasoningEffort" in body) return invalid("save assignments and reasoning separately");
    if (!isRecord(body.assignments)) return invalid("assignments must be an object");
    const replacement: Record<string, string> = {};
    for (const [id, value] of Object.entries(body.assignments)) {
      if (!safeKey(id) || typeof value !== "string" || !available.includes(value)) return invalid(`unknown modelId: ${String(value)}`);
      replacement[id] = value;
    }
    next.assignments = replacement;
  } else {
    const ids = Array.isArray(body.botIds) ? body.botIds : [body.botId];
    if (ids.length === 0 || !ids.every(safeKey)) return invalid("botId or nonempty botIds is required");
    const hasModel = "modelId" in body;
    const hasEffort = "reasoningEffort" in body;
    if (!hasModel && !hasEffort) return invalid("modelId or reasoningEffort is required");
    if (hasModel && body.modelId !== null && body.modelId !== "" && (typeof body.modelId !== "string" || !available.includes(body.modelId))) return invalid(`unknown modelId: ${String(body.modelId)}`);
    // modelIds permits setting the same effort for several selected routes.
    // Without it, an explicit reasoningModelId scopes the effort independently
    // from pinning the bot's model. All values are validated before any write.
    if (hasEffort) {
      const models = Array.isArray(body.modelIds) ? body.modelIds : [body.reasoningModelId ?? body.modelId];
      if (models.length === 0 || !models.every((id) => typeof id === "string" && available.includes(id))) return invalid("reasoningModelId or modelIds must name available models");
      const effort = body.reasoningEffort;
      if (effort !== null && (typeof effort !== "string" || !EFFORTS.includes(effort))) return invalid("unsupported reasoning effort");
      for (const modelId of models as string[]) {
        const model = catalog.models.find((row) => String(row.id) === modelId)!;
        const levels = model.reasoningLevels ?? ["default", "none", "low", "medium", "high"];
        if (effort !== null && !levels.includes(effort as ReasoningLevel)) return invalid(`${modelId} does not support reasoning effort ${String(effort)}`);
      }
      for (const id of ids as string[]) {
        const choices = { ...next.reasoning[id] };
        for (const modelId of models as string[]) {
          if (effort === null) delete choices[modelId]; else choices[modelId] = effort as ReasoningLevel;
        }
        if (Object.keys(choices).length) next.reasoning[id] = choices; else delete next.reasoning[id];
      }
    }
    if (hasModel) {
      for (const id of ids as string[]) {
        if (body.modelId === null || body.modelId === "") delete next.assignments[id]; else next.assignments[id] = body.modelId as string;
      }
    }
  }
  try { writeSettings(current, next); }
  catch (err) { sendJson(res, 500, { error: err instanceof Error ? err.message : "could not save bot settings" }); return true; }
  sendJson(res, 200, { assignments: next.assignments, reasoning: next.reasoning, available });
  return true;
}
