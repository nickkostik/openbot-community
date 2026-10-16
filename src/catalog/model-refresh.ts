import { randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import path from "node:path";
import { makeModel } from "../domain/model.ts";
import type { AbsPath, Catalog, Model, ModelId, ModelSlug, Provider } from "../domain/types.ts";
import { catalogFromPlanJson } from "../supervisor/plan.ts";
import type { FsDeps } from "../supervisor/procs.ts";
import { loadSecrets } from "../supervisor/secrets.ts";
import type { CatalogManager } from "./model-catalog.ts";
import { modelImportFields, modelRefreshFields } from "./model-fields.ts";
import { fetchModelsForProvider, type FetchProviderResult } from "./provider-models.ts";

export const MODEL_REFRESH_INTERVAL_MS = 6 * 60 * 60 * 1000;
type ProviderList = { provider: Provider; result: FetchProviderResult };
export type ModelRefreshStatus = {
  state: "idle" | "running" | "disabled" | "completed" | "failed";
  lastAttemptAt?: string;
  lastCompletedAt?: string;
  nextAttemptAt?: string;
  added?: number;
  updated?: number;
  reasoningHeld?: number;
  providers?: { id: string; fetched: number; errorKind?: string }[];
};

function record(v: unknown): v is Record<string, unknown> { return v !== null && typeof v === "object" && !Array.isArray(v); }
function providerKey(p: Provider): string { return JSON.stringify([p.id, p.name, p.origin, p.apiType ?? "chat-completions", p.maxTokensDefault, p.mapFile]); }
export function canDiscoverProvider(provider: Provider): boolean {
  // Automatic discovery may visit saved direct families; it must never send
  // keys to the retired N5/MetaKey relay or import OpenCode Zen PAYG listings.
  if (/\b(?:n5|meta[ -]?key)\b/i.test([provider.id, provider.name, provider.origin].join(" "))) return false;
  if (/^opencode/i.test(provider.id)) {
    try { const url = new URL(provider.origin); return url.hostname === "opencode.ai" && /^\/zen\/go(?:\/v1)?\/?$/.test(url.pathname); }
    catch { return false; }
  }
  return true;
}

/** Merge against the newest saved plan after network awaits. Never remove a
 * model, change a family, choose a default, or reset user/bot reasoning. */
export function mergeModelRefresh(raw: string, lists: ProviderList[], manager: Pick<CatalogManager, "snapshot">, botRaw?: string) {
  const plan: unknown = JSON.parse(raw);
  if (!record(plan) || plan.kind !== "custom" || !record(plan.catalog) || !Array.isArray(plan.catalog.models)) throw new Error("No saved custom catalog");
  const catalog = catalogFromPlanJson(raw);
  const byProvider = new Map(lists.map(list => [list.provider.id, list]));
  const protectedEfforts = new Map<string, Set<string>>();
  let unreadableBotSettings = false;
  try {
    const settings: unknown = JSON.parse(botRaw ?? "{}");
    if (record(settings) && record(settings.reasoning)) for (const choices of Object.values(settings.reasoning)) {
      if (!record(choices)) continue;
      for (const [id, effort] of Object.entries(choices)) if (typeof effort === "string") {
        if (!protectedEfforts.has(id)) protectedEfforts.set(id, new Set());
        protectedEfforts.get(id)!.add(effort);
      }
    }
  } catch { unreadableBotSettings = true; }
  let added = 0, updated = 0, reasoningHeld = 0;
  const models = [...plan.catalog.models];
  const seen = new Set(catalog.models.map(model => String(model.id)));
  const seenRoutes = new Set(catalog.models.map(model => `${model.providerId}\0${model.slug}`));
  for (const provider of catalog.providers) {
    const list = byProvider.get(provider.id);
    // A provider edited/removed during the fetch must not acquire stale routes.
    if (!list || providerKey(list.provider) !== providerKey(provider)) continue;
    for (let i = 0; i < models.length; i++) {
      const row = models[i];
      const current = catalog.models.find(model => model.id === (record(row) ? row.id : undefined));
      if (!current || current.providerId !== provider.id) continue;
      const lookup = manager.snapshot(current.slug, provider.origin).lookup;
      if (!lookup?.found) continue;
      const fields = modelRefreshFields(current, lookup.model);
      const levels = fields.reasoningLevels;
      const required = new Set([current.activeReasoning, ...(protectedEfforts.get(current.id) ?? [])]);
      if (levels && (unreadableBotSettings || [...required].some(effort => !levels.includes(effort)))) {
        fields.reasoningLevels = current.reasoningLevels;
        reasoningHeld++;
      }
      const next = { ...(models[i] as Record<string, unknown>), ...fields };
      if (!isDeepStrictEqual(next, models[i])) { models[i] = next; updated++; }
    }
    if (!list.result.ok) continue; // A provider outage must never prune saved routes.
    for (const fetched of list.result.models) {
      if (!fetched.id.trim() || /\s/.test(fetched.id)) continue;
      const id = `${provider.id}:${fetched.id}` as ModelId;
      const route = `${provider.id}\0${fetched.id}`;
      if (seen.has(id) || seenRoutes.has(route)) continue;
      const lookup = manager.snapshot(fetched.id, provider.origin).lookup;
      const fields = modelImportFields(fetched, lookup?.found ? lookup.model : undefined);
      const model: Model = makeModel({ ...fields, id, providerId: provider.id, slug: fetched.id as ModelSlug,
        activeReasoning: "default", parameters: [], ...(!fields.apiType && provider.apiType ? { apiType: provider.apiType } : {}) });
      models.push(model); seen.add(id); seenRoutes.add(route); added++;
    }
  }
  if (!added && !updated) return { raw, added, updated, reasoningHeld };
  plan.catalog = { ...plan.catalog, models };
  return { raw: JSON.stringify(plan, null, 2) + "\n", added, updated, reasoningHeld };
}

export function createModelRefresh(deps: {
  fs: FsDeps;
  sandData: AbsPath;
  planPath: AbsPath;
  secretsPath: AbsPath;
  botModelsPath?: AbsPath;
  manager: CatalogManager;
  enqueue: <T>(work: () => Promise<T>) => Promise<T>;
  fetchProvider?: typeof fetchModelsForProvider;
  now?: () => number;
}) {
  const now = deps.now ?? Date.now;
  const statusPath = path.join(deps.sandData, "openbot-model-refresh-status.json") as AbsPath;
  const settingsPath = path.join(deps.sandData, "openbot-model-refresh.json") as AbsPath;
  let active: Promise<ModelRefreshStatus> | undefined;
  let status: ModelRefreshStatus = { state: "idle" };
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { const saved: unknown = JSON.parse(deps.fs.read(statusPath) ?? "{}"); if (record(saved)) status = { ...saved, state: "idle" } as ModelRefreshStatus; } catch { /* Missing status runs now. */ }
  function config() {
    try {
      const value: unknown = JSON.parse(deps.fs.read(settingsPath) ?? "{}");
      if (record(value)) return { enabled: value.enabled !== false, intervalMs: typeof value.intervalHours === "number"
        ? Math.min(168, Math.max(1, value.intervalHours)) * 3600000 : MODEL_REFRESH_INTERVAL_MS };
    } catch { /* A broken settings file must not cause deletion or a tight loop. */ }
    return { enabled: true, intervalMs: MODEL_REFRESH_INTERVAL_MS };
  }
  function writeAtomic(target: AbsPath, raw: string, mode = 0o600) {
    if (!deps.fs.rename) throw new Error("Atomic file replacement is required");
    const tmp = `${target}.refresh-${randomUUID()}` as AbsPath;
    try { deps.fs.write(tmp, raw, mode); deps.fs.rename(tmp, target); }
    finally { deps.fs.remove(tmp); }
  }
  function persist() { writeAtomic(statusPath, JSON.stringify(status, null, 2) + "\n"); }

  async function update(): Promise<ModelRefreshStatus> {
    if (!config().enabled) { status = { ...status, state: "disabled" }; persist(); return status; }
    const stamp = new Date(now()).toISOString();
    status = { ...status, state: "running", lastAttemptAt: stamp, nextAttemptAt: new Date(now() + config().intervalMs).toISOString() };
    persist();
    try {
      await deps.manager.refresh().done;
      const catalog = catalogFromPlanJson(deps.fs.read(deps.planPath));
      const store = loadSecrets(deps.fs, deps.secretsPath);
      const lists: ProviderList[] = [];
      let next = 0;
      // Two discovery calls at most; chat and compaction keep their own lanes.
      await Promise.all(Array.from({ length: Math.min(2, catalog.providers.length) }, async () => {
        while (next < catalog.providers.length) {
          const provider = catalog.providers[next++]!;
          if (!canDiscoverProvider(provider)) {
            lists.push({ provider, result: { ok: false, errorKind: "not-supported", message: "Retired discovery endpoint" } });
            continue;
          }
          const response = await (deps.fetchProvider ?? fetchModelsForProvider)({ providerId: provider.id, catalog, secretStore: store });
          const body = response.body;
          const kind = record(body) && record(body.error) && ["unauthorized", "unreachable", "not-supported", "parse-error", "internal"].includes(String(body.error.kind)) ? body.error.kind as "unauthorized" | "unreachable" | "not-supported" | "parse-error" | "internal" : "unreachable";
          const result: FetchProviderResult = record(body) && body.ok === true && Array.isArray(body.models)
            ? { ok: true, models: body.models, skipped: 0, skippedReasons: [] }
            : { ok: false, errorKind: kind, message: "Provider discovery unavailable" };
          lists.push({ provider, result });
        }
      }));
      const merged = await deps.enqueue(async () => {
        const raw = deps.fs.read(deps.planPath);
        if (!raw) return { added: 0, updated: 0, reasoningHeld: 0 };
        const out = mergeModelRefresh(raw, lists, deps.manager, deps.fs.read(deps.botModelsPath ?? (process.env.OPENBOT_BOT_MODELS ?? path.join(deps.sandData, "openbot-bot-models.json")) as AbsPath));
        // No await from newest read through atomic replace: same-process panel
        // saves are serialized and external edits fail comparison rather than
        // getting overwritten. The hop reloads the plan on its next request.
        if (out.raw !== raw) {
          if (deps.fs.read(deps.planPath) !== raw) throw new Error("Catalog changed during refresh");
          writeAtomic(deps.planPath, out.raw);
        }
        return out;
      });
      status = { ...status, state: "completed", lastCompletedAt: new Date(now()).toISOString(),
        added: merged.added, updated: merged.updated, reasoningHeld: merged.reasoningHeld,
        providers: lists.map(list => ({ id: list.provider.id, fetched: list.result.ok ? list.result.models.length : 0,
          ...(!list.result.ok ? { errorKind: list.result.errorKind } : {}) })) };
    } catch {
      status = { ...status, state: "failed" };
    }
    persist();
    return status;
  }

  function run() {
    return active ??= update().finally(() => { active = undefined; });
  }
  function check() {
    const due = Date.parse(status.nextAttemptAt ?? "");
    return config().enabled && (!Number.isFinite(due) || due <= now()) ? run() : Promise.resolve(status);
  }
  function start() {
    stopped = false;
    const tick = () => {
      if (stopped) return;
      void check().catch(() => {});
      timer = setTimeout(tick, 60_000); timer.unref();
    };
    if (timer) clearTimeout(timer);
    timer = setTimeout(tick, 15_000); timer.unref();
  }
  return { run, check, start, snapshot: () => ({ ...status, enabled: config().enabled, intervalHours: config().intervalMs / 3600000 }),
    stop() { stopped = true; if (timer) clearTimeout(timer); timer = undefined; } };
}
