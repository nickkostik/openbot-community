import { localTransfer } from "./local-transfer.ts";
import { localEngineReadiness } from "../supervisor/local-engine.ts";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LOOPBACK, SERVICE_PORT, type Catalog, type Snapshot, type TunnelObserved } from "../domain/types.ts";
import { grokSkillsStatus, installGrokSkills, isSkillSlug } from "../grok-skills.ts";
import { customBoxFromCatalog, officialBox } from "../parse/argv.ts";
import { fetchModelsForProvider } from "../catalog/provider-models.ts";
import { createCatalogManager } from "../catalog/model-catalog.ts";
import { createModelRefresh } from "../catalog/model-refresh.ts";
import { catalogAfterSave, parseUiProviderSave } from "../parse/ui.ts";
import { renderQrAscii } from "../qrcode.ts";
import { boxPathsFrom, joinAbs } from "../supervisor/paths.ts";
import { discoverNativeHostMain } from "../supervisor/host-location.ts";
import { catalogFromPlanJson, compileCustomPlan, planToJson } from "../supervisor/plan.ts";
import { observe, type SupervisorDeps } from "../supervisor/observe.ts";
import {
  GUARD_WATCH_DISABLE_ENV,
  GUARD_WATCH_INTERVAL_MS,
  GUARD_WATCH_STARTUP_DELAY_MS,
  createGuardWatchState,
  runGuardWatchTick,
} from "../supervisor/guard-watch.ts";
import { nodeFs, nodeProcs } from "../supervisor/procs.ts";
import { reconcile } from "../supervisor/reconcile.ts";
import { loadSecrets, saveSecrets, upsertSecret } from "../supervisor/secrets.ts";
import { readExposeFile } from "../supervisor/tunnel.ts";
import { COMPUTER_RECOVERY_INTERVAL_MS, runComputerRecoveryWatchTick } from "../supervisor/computer-recovery-watch.ts";
import {
  TUNNEL_WATCH_DISABLE_ENV,
  TUNNEL_WATCH_INTERVAL_MS,
  TUNNEL_WATCH_STARTUP_DELAY_MS,
  createTunnelWatchState,
  runTunnelWatchTick,
} from "../supervisor/tunnel-watch.ts";
import { completeOpenAIOAuth, startOpenAIOAuth } from "../supervisor/openai-oauth.ts";
import { CredentialDrafts } from "../supervisor/credential-import.ts";
import { handleBotModelsApi } from "./bot-models.ts";
import { createLogReader } from "./log-reader.ts";
import { isInjectionMode, parseInjectionLayers, readDeliverySettings, writeDeliverySettings } from "./delivery-settings.ts";

type LogSettings = {
  loggingEnabled: boolean;
  logBodies: boolean;
  logBodiesOnError: boolean;
  logRetentionDays: number;
  maxBodyCaptureBytes: number;
  /** Deprecated: read-compat only, the log store no longer enforces it. */
  maxRecords: number;
};

type LogList = {
  items: unknown[];
  total: number;
  page: number;
  pageSize: number;
  approximate: boolean;
};

type LogInjectionFacet = { value: string; count: number };

type LogInjectionStats = {
  records: number;
  candidates: number;
  wouldApply: number;
  l2Attempted: number;
  l2Triggered: number;
  applied: number;
  remediationFailures: number;
  fallbackOriginalTerminal: number;
  unresolved: number;
  terminalReleased: number;
  skipped: number;
  classificationSkipped: number;
  extraCalls: number;
  extraLatencyMs: number;
  averageExtraLatencyMs: number | null;
  families: LogInjectionFacet[];
  skipReasons: LogInjectionFacet[];
  classificationSkippedReasons: LogInjectionFacet[];
  outcomes: LogInjectionFacet[];
  modes: LogInjectionFacet[];
};

type LogStats = {
  records: number;
  scanned: number;
  approximate: boolean;
  ok: number;
  errors: number;
  tokenBasis?: "provider";
  providerSampleRecords?: number;
  excludedHarnessRecords?: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  bodyBytes: number;
  bodyFiles: number;
  bodyDiskBytes: number;
  bodiesApproximate: boolean;
  diskBytes: number;
  /** Omitted for legacy/off-mode logs with no injection metadata. */
  injection?: LogInjectionStats;
};

type LogFacetOption = { value: string; count: number };

type LogFacets = {
  sampled: number;
  total: number;
  model: { values: LogFacetOption[]; approximate: boolean };
  provider: { values: LogFacetOption[]; approximate: boolean };
  channel: { values: LogFacetOption[]; approximate: boolean };
  status: { values: LogFacetOption[]; approximate: boolean };
  bots: Array<{ botId?: string; botName?: string }>;
  chatTypes: Array<"group" | "dm" | "routine">;
};

type LogEventList = {
  items: unknown[];
  total: number;
};

type LogUsageBucket = {
  key: string;
  requests: number;
  ok: number;
  fail: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  cachedTokens: number;
  reasoningTokens: number;
  cacheKnownInputTokens: number;
  cacheUnknownInputTokens: number;
  cacheCoverage: number;
  cacheHitRate: number | null;
  avgLatencyMs: number;
  avgFirstTokenMs: number | null;
  avgTps: number | null;
};

type LogUsageSummary = LogUsageBucket & {
  successRate: number;
};

type LogUsage = {
  approximate: boolean;
  scanned: number;
  total: number;
  basis: "provider";
  excludedHarnessRecords: number;
  from: string;
  to: string;
  byDay: LogUsageBucket[];
  byModel: LogUsageBucket[];
  byProvider: LogUsageBucket[];
  byPurpose: LogUsageBucket[];
  summary: LogUsageSummary;
  buckets: LogUsageBucket[];
  bucketMs: number;
};

const require = createRequire(import.meta.url);
const hop = require("../../payload/hop-handler.cjs") as {
  handleHopRequest: (req: http.IncomingMessage, res: http.ServerResponse) => Promise<boolean>;
  loadKey: (providerId: string, provider: unknown) => Promise<string>;
};
const turnLease = require("../../payload/turn-lease.cjs") as {
  touch: () => void;
  snapshot: () => unknown;
};
const requestLog = require("../../payload/request-log.cjs") as {
  loadSettings: () => LogSettings;
  saveSettings: (input: unknown) => LogSettings;
  listRequests: (query: Record<string, unknown>) => LogList;
  getRequest: (id: string) => unknown;
  clearRequests: () => void;
  pruneNowAsync: (settings?: unknown, onBatch?: unknown) => Promise<unknown>;
  cleanupNowAsync: (settings?: unknown) => Promise<Record<string, unknown>>;
  statsNow: () => LogStats;
  facetsNow: () => LogFacets;
  appendEvent: (entry: Record<string, unknown>) => unknown;
  queryEvents: (query: Record<string, unknown>) => LogEventList;
  stripBodiesAsync: () => Promise<{ stripped: number }>;
  usageNow: (query: Record<string, unknown>) => LogUsage;
};

export type BotInfo = {
  botId: string;
  botName: string;
  deleted: boolean;
  hidden?: boolean;
  updatedAtMs: number | null;
  createdAtMs: number | null;
};
export type PauseBotsState = { pausedBotIds: string[] };

let logReader: ReturnType<typeof createLogReader> | undefined;
function logs() {
  return logReader ??= createLogReader({ sandData: paths().sandData });
}

async function readBots(current: SupervisorDeps): Promise<BotInfo[]> {
  const root = process.env.OPENBOT_AGENT_DATA ?? "/home/box/agent-data";
  const agentsDir = path.join(root, "agents");
  const ids = new Set<string>();
  try {
    for (const entry of fs.readdirSync(agentsDir, { withFileTypes: true })) {
      if (entry.isDirectory()) ids.add(entry.name);
    }
  } catch {
    // Keep known deleted bots visible below the live agents.
  }
  for (const id of readPauseBots(current).pausedBotIds) ids.add(id);
  try {
    const assignmentsPath = process.env.OPENBOT_BOT_MODELS ?? path.join(String(current.paths.sandData), "openbot-bot-models.json");
    const assignments = JSON.parse(fs.readFileSync(assignmentsPath, "utf8")) as { assignments?: unknown };
    if (assignments.assignments && typeof assignments.assignments === "object") {
      for (const id of Object.keys(assignments.assignments)) ids.add(id);
    }
  } catch { /* absent or corrupt assignment state */ }
  try {
    for (const bot of (await logs().read<LogFacets>("facetsNow")).bots) if (bot.botId) ids.add(bot.botId);
  } catch { /* log facets are best effort */ }
  return [...ids].map((botId) => {
    const dir = path.join(agentsDir, botId);
    let hidden = false;
    try {
      const settings = JSON.parse(fs.readFileSync(path.join(dir, "settings.json"), "utf8")) as { hiddenFromSidebar?: unknown };
      hidden = settings?.hiddenFromSidebar === true;
    } catch { /* missing or malformed settings do not hide a live bot */ }
    let dirStat: fs.Stats;
    try {
      dirStat = fs.statSync(dir);
    } catch (error) {
      return { botId, botName: botId, deleted: isMissingFileError(error), hidden, updatedAtMs: null, createdAtMs: null };
    }

    const profilePath = path.join(dir, "profile.json");
    let profileStat: fs.Stats;
    try {
      profileStat = fs.statSync(profilePath);
    } catch (error) {
      return {
        botId,
        botName: botId,
        deleted: isMissingFileError(error),
        hidden,
        updatedAtMs: dirStat.mtimeMs,
        createdAtMs: null,
      };
    }

    let profileText: string;
    try {
      profileText = fs.readFileSync(profilePath, "utf8");
    } catch {
      return { botId, botName: botId, deleted: false, hidden, updatedAtMs: dirStat.mtimeMs, createdAtMs: profileCreatedAt(profileStat) };
    }

    let profile: { name?: unknown };
    try {
      profile = JSON.parse(profileText) as { name?: unknown };
    } catch {
      return { botId, botName: botId, deleted: true, hidden, updatedAtMs: dirStat.mtimeMs, createdAtMs: profileCreatedAt(profileStat) };
    }

    const name = typeof profile.name === "string" && profile.name.trim() ? profile.name.trim() : botId;
    return { botId, botName: name, deleted: false, hidden, updatedAtMs: dirStat.mtimeMs, createdAtMs: profileCreatedAt(profileStat) };
  }).sort((a, b) => Number(a.deleted) - Number(b.deleted) || (b.updatedAtMs ?? -1) - (a.updatedAtMs ?? -1) || (b.createdAtMs ?? -1) - (a.createdAtMs ?? -1) || a.botName.localeCompare(b.botName));
}

function isMissingFileError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}

function profileCreatedAt(stat: fs.Stats): number | null {
  return Number.isFinite(stat.birthtimeMs) ? stat.birthtimeMs : null;
}

function readPauseBots(current: SupervisorDeps): PauseBotsState {
  const raw = current.fs.read(current.paths.pauseBots);
  if (raw === undefined) return { pausedBotIds: [] };
  try {
    const parsed = JSON.parse(raw) as { pausedBotIds?: unknown };
    const ids = Array.isArray(parsed.pausedBotIds)
      ? parsed.pausedBotIds.filter((id): id is string => typeof id === "string" && id.trim().length > 0)
      : [];
    return { pausedBotIds: [...new Set(ids)] };
  } catch {
    return { pausedBotIds: [] };
  }
}

function writePauseBots(current: SupervisorDeps, state: PauseBotsState): PauseBotsState {
  const normalized = { pausedBotIds: [...new Set(state.pausedBotIds.filter((id) => id.trim()))].sort() };
  const body = JSON.stringify(normalized, null, 2) + "\n";
  const lastSlash = current.paths.pauseBots.lastIndexOf("/");
  const dir = current.paths.pauseBots.slice(0, lastSlash + 1) as typeof current.paths.pauseBots;
  const tmpPath = joinAbs(dir, "openbot-pause-bots.json.tmp");
  current.fs.mkdirp(current.paths.sandData);
  fs.writeFileSync(tmpPath, body, { encoding: "utf8", mode: 0o644 });
  fs.renameSync(tmpPath, current.paths.pauseBots);
  return normalized;
}

async function handlePauseBotsApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<boolean> {
  const current = deps();
  if (req.method === "GET" && url.pathname === "/api/bots") {
    sendJson(res, 200, await readBots(current));
    return true;
  }
  if (req.method === "GET" && url.pathname === "/api/pause-bots") {
    sendJson(res, 200, readPauseBots(current));
    return true;
  }
  if (req.method !== "PUT" || url.pathname !== "/api/pause-bots") return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readBody(req)) as unknown;
  } catch {
    sendJson(res, 400, { error: "invalid json" });
    return true;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    sendJson(res, 400, { error: "body must be an object" });
    return true;
  }
  const body = parsed as { botId?: unknown; paused?: unknown; pausedBotIds?: unknown };
  const rawIds: unknown = body.pausedBotIds;
  const replacementIds = Array.isArray(rawIds)
    ? rawIds.filter((id): id is string => typeof id === "string" && id.trim().length > 0)
    : undefined;
  if (Array.isArray(rawIds) && replacementIds !== undefined && replacementIds.length !== rawIds.length) {
    sendJson(res, 400, { error: "pausedBotIds must be an array of strings" });
    return true;
  }
  if (replacementIds === undefined && (typeof body.botId !== "string" || !body.botId.trim() || typeof body.paused !== "boolean")) {
    sendJson(res, 400, { error: "botId and paused are required" });
    return true;
  }
  let nextIds: string[] = replacementIds ?? [];
  const toggleBotId = replacementIds === undefined && typeof body.botId === "string" ? body.botId.trim() : "";
  const togglePaused = body.paused === true;
  // Toggle read-modify-write must run inside the save queue: if another save is
  // in flight, reading outside the queue could base the replacement list on
  // stale ids and drop a concurrently added pause.
  const state = await enqueueSave(async () => {
    if (replacementIds === undefined) {
      const ids = new Set(readPauseBots(current).pausedBotIds);
      if (togglePaused) ids.add(toggleBotId);
      else ids.delete(toggleBotId);
      nextIds = [...ids];
    }
    return writePauseBots(current, { pausedBotIds: nextIds });
  });
  sendJson(res, 200, state);
  return true;
}

const repoRoot = process.env.OPENBOT_REPO ?? fileURLToPath(new URL("../..", import.meta.url));
const uiDir = path.join(repoRoot, "ui");
const host = process.env.OPENBOT_UI_HOST ?? LOOPBACK;
const port = Number(process.env.OPENBOT_UI_PORT ?? String(SERVICE_PORT));

let saveChain: Promise<void> = Promise.resolve();

let catalogManager: ReturnType<typeof createCatalogManager> | undefined;

function modelCatalog() {
  if (catalogManager === undefined) {
    catalogManager = createCatalogManager({
      fs: nodeFs(),
      cachePath: paths().modelCatalog,
    });
  }
  return catalogManager;
}

let modelRefresh: ReturnType<typeof createModelRefresh> | undefined;
function fetchConfiguredModels(input: Parameters<typeof fetchModelsForProvider>[0]) {
  return fetchModelsForProvider({ ...input, refreshOAuth: async (providerId) => {
    const provider = input.catalog.providers.find(row => row.id === providerId);
    await hop.loadKey(providerId, provider);
    const current = deps();
    const credential = loadSecrets(current.fs, current.paths.secrets).providers[providerId];
    if (!credential) throw new Error("Refreshed credential was not persisted");
    return credential;
  } });
}
function automaticModels() {
  if (!modelRefresh) {
    const current = deps();
    modelRefresh = createModelRefresh({ fs: current.fs, sandData: current.paths.sandData,
      planPath: current.paths.plan, secretsPath: current.paths.secrets, manager: modelCatalog(), enqueue: enqueueSave,
      fetchProvider: fetchConfiguredModels });
  }
  return modelRefresh;
}

function enqueueSave<T>(work: () => Promise<T>): Promise<T> {
  const run = saveChain.then(work, work);
  saveChain = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

const resolvedHostMain = discoverNativeHostMain(process.env.OPENBOT_HOST_MAIN);
function paths() {
  return boxPathsFrom({
    repoRoot,
    hostMain: resolvedHostMain,
    sandData: process.env.OPENBOT_SAND_DATA,
  });
}

function deps(): SupervisorDeps {
  return { paths: paths(), fs: nodeFs(), procs: nodeProcs() };
}

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

function send(res: http.ServerResponse, status: number, body: string | Buffer, type: string): void {
  const buf = typeof body === "string" ? Buffer.from(body) : body;
  res.writeHead(status, {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "Content-Length": String(buf.length),
  });
  res.end(buf);
}

function sendJson(res: http.ServerResponse, status: number, payload: unknown): void {
  send(res, status, JSON.stringify(payload), "application/json; charset=utf-8");
}

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

/**
 * Strict read of the mode file. Only the literal token "official" means
 * official; anything else (missing, empty, garbage) means custom. Users own
 * custom state and often have zero official quota, so an unreadable mode file
 * must never reconcile chat back to official.
 */
export function wrapMode(raw: string | undefined): "official" | "custom" {
  return raw?.trim() === "official" ? "official" : "custom";
}

function tunnelForUi(tunnel: TunnelObserved): TunnelObserved & { qr?: string } {
  if (tunnel.kind !== "cloudflare-quick") {
    return tunnel;
  }
  try {
    return { ...tunnel, qr: renderQrAscii(tunnel.url) };
  } catch {
    return tunnel;
  }
}

function snapshotForUi(snapshot: Snapshot): Snapshot & { tunnel: TunnelObserved & { qr?: string } } {
  return { ...snapshot, tunnel: tunnelForUi(snapshot.tunnel) };
}

function logSettings(): LogSettings {
  return requestLog.loadSettings();
}

function publicState(current: SupervisorDeps, catalog?: Catalog) {
  const saved = catalog ?? catalogFromPlanJson(current.fs.read(current.paths.plan));
  const store = loadSecrets(current.fs, current.paths.secrets);
  const keyedProviders = Object.keys(store.providers);
  const active = saved.bindings.find((row) => row.conversation.kind === "wildcard");
  return {
    providers: saved.providers,
    models: saved.models,
    keyedProviders,
    activeModelId: active?.modelId ?? null,
    localEngine: localEngineReadiness(current),
    logSettings: logSettings(),
  };
}

function parseLogsQuery(url: URL): Record<string, unknown> {
  const q = url.searchParams.get("q") ?? "";
  const model = url.searchParams.get("model") ?? "";
  const botId = url.searchParams.get("botId") ?? "";
  const botName = url.searchParams.get("botName") ?? "";
  const chatType = url.searchParams.get("chatType") ?? "";
  const from = url.searchParams.get("from") ?? "";
  const to = url.searchParams.get("to") ?? "";
  const okRaw = url.searchParams.get("ok");
  const page = Number(url.searchParams.get("page"));
  const pageSize = Number(url.searchParams.get("pageSize"));
  const query: Record<string, unknown> = {};
  if (q.trim()) {
    query.q = q;
  }
  if (model.trim()) {
    query.model = model;
  }
  if (botId.trim()) {
    query.botId = botId;
  }
  if (botName.trim()) {
    query.botName = botName;
  }
  if (chatType.trim()) {
    query.chatType = chatType;
  }
  if (from.trim()) {
    query.from = from;
  }
  if (to.trim()) {
    query.to = to;
  }
  const channel = url.searchParams.get("channel") ?? "";
  if (channel.trim()) {
    query.channel = channel.trim();
  }
  if (okRaw === "true") {
    query.ok = true;
  } else if (okRaw === "false") {
    query.ok = false;
  }
  if (Number.isInteger(page)) {
    query.page = page;
  }
  if (Number.isInteger(pageSize)) {
    query.pageSize = pageSize;
  }
  return query;
}

function providerIdFromFetchPath(pathname: string): string | undefined {
  const prefix = "/api/providers/";
  const suffix = "/fetch-models";
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return undefined;
  }
  const rest = pathname.slice(prefix.length, pathname.length - suffix.length);
  if (!rest || rest.includes("/")) {
    return undefined;
  }
  try {
    return decodeURIComponent(rest);
  } catch {
    return undefined;
  }
}

function parseEventsQuery(url: URL): Record<string, unknown> {
  const query: Record<string, unknown> = {};
  const severity = url.searchParams.get("severity") ?? "";
  const type = url.searchParams.get("type") ?? "";
  const requestId = url.searchParams.get("requestId") ?? "";
  if (severity.trim()) query.severity = severity.trim().toUpperCase();
  if (type.trim()) query.type = type.trim();
  if (requestId.trim()) query.requestId = requestId.trim();
  const limit = Number(url.searchParams.get("limit"));
  if (Number.isInteger(limit)) query.limit = limit;
  return query;
}

function parseUsageQuery(url: URL): Record<string, unknown> {
  const query: Record<string, unknown> = {};
  const from = url.searchParams.get("from") ?? "";
  const to = url.searchParams.get("to") ?? "";
  const model = url.searchParams.get("model") ?? "";
  const provider = url.searchParams.get("provider") ?? "";
  if (from.trim()) query.from = from.trim();
  if (to.trim()) query.to = to.trim();
  if (model.trim()) query.model = model.trim();
  if (provider.trim()) query.provider = provider.trim();
  const range = url.searchParams.get("range") ?? "";
  if (range.trim()) query.range = range.trim();
  return query;
}

function logIdFromPath(pathname: string): string | undefined {
  const prefix = "/api/logs/";
  if (!pathname.startsWith(prefix)) {
    return undefined;
  }
  const rest = pathname.slice(prefix.length);
  if (!rest || rest.includes("/")) {
    return undefined;
  }
  try {
    return decodeURIComponent(rest);
  } catch {
    return undefined;
  }
}

async function handleLogsApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<boolean> {
  if (req.method === "GET" && url.pathname === "/api/logs/settings") {
    sendJson(res, 200, logSettings());
    return true;
  }
  if (req.method === "PUT" && url.pathname === "/api/logs/settings") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(await readBody(req)) as unknown;
    } catch {
      sendJson(res, 400, { error: "invalid json" });
      return true;
    }
    // maxRecords is read-compat only and never enforced: ignore it on the
    // way in (saveSettings also refuses to persist it) so a stale count cap
    // can never be mistaken for an active retention knob.
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      delete (parsed as Record<string, unknown>).maxRecords;
    }
    try {
      await enqueueSave(async () => {
        const before = logSettings();
        const saved = requestLog.saveSettings(parsed);
        if (before.loggingEnabled !== saved.loggingEnabled) {
          try {
            requestLog.appendEvent({
              type: "logs.settings",
              severity: "INFO",
              message: `Request recording ${saved.loggingEnabled ? "enabled" : "disabled"}.`,
            });
          } catch {
            /* event write is best-effort */
          }
        }
        let wrapBytesChanged = false;
        let wrapError: string | undefined;
        const current = deps();
        if (wrapMode(current.fs.read(current.paths.mode)) === "official") {
          const result = await reconcile(
            officialBox(current.paths, readExposeFile(current.fs, current.paths.expose)),
            current,
            { source: "ui:logs-settings" },
          );
          if (result.kind === "refused") {
            wrapError = result.error.kind;
          } else {
            wrapBytesChanged = result.wrapBytesChanged;
          }
        }
        sendJson(res, 200, { ...saved, wrapBytesChanged, wrapError });
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "invalid log settings";
      sendJson(res, 400, { error: message });
    }
    return true;
  }
  if (req.method === "POST" && url.pathname === "/api/logs/clear") {
    requestLog.clearRequests();
    try {
      requestLog.appendEvent({
        type: "logs.clear",
        severity: "WARN",
        message: "All request records and captured bodies were cleared.",
      });
    } catch {
      /* best-effort */
    }
    sendJson(res, 200, { ok: true });
    return true;
  }
  if (req.method === "GET" && url.pathname === "/api/logs") {
    sendJson(res, 200, await logs().read("listRequests", parseLogsQuery(url)));
    return true;
  }
  if (req.method === "GET" && url.pathname === "/api/logs/stats") {
    sendJson(res, 200, await logs().read("statsNow"));
    return true;
  }
  if (req.method === "GET" && url.pathname === "/api/logs/facets") {
    sendJson(res, 200, await logs().read("facetsNow"));
    return true;
  }
  if (req.method === "GET" && url.pathname === "/api/logs/events") {
    sendJson(res, 200, await logs().read("queryEvents", parseEventsQuery(url)));
    return true;
  }
  // NOTE: kept above the generic /api/logs/:id matcher so "usage" is not
  // mistaken for a record id.
  if (req.method === "GET" && url.pathname === "/api/logs/usage") {
    sendJson(res, 200, await logs().read("usageNow", parseUsageQuery(url)));
    return true;
  }
  if (req.method === "POST" && url.pathname === "/api/logs/cleanup") {
    const result = await requestLog.cleanupNowAsync();
    sendJson(res, 200, { ok: true, ...result });
    return true;
  }
  if (req.method === "POST" && url.pathname === "/api/logs/strip-bodies") {
    const result = await requestLog.stripBodiesAsync();
    sendJson(res, 200, { ok: true, ...result });
    return true;
  }
  if (req.method === "GET") {
    const id = logIdFromPath(url.pathname);
    if (id) {
      const detail = await logs().read("getRequest", id);
      if (detail === null || detail === undefined) {
        sendJson(res, 404, { error: "not found" });
        return true;
      }
      sendJson(res, 200, detail);
      return true;
    }
  }
  return false;
}

function parseInstallSlug(parsed: unknown): { ok: true; slug?: string } | { ok: false; error: string } {
  if (parsed === null || parsed === undefined) {
    return { ok: true };
  }
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "invalid json" };
  }
  if (!("slug" in parsed)) {
    return { ok: true };
  }
  const raw = (parsed as { slug?: unknown }).slug;
  if (raw === undefined || raw === null || raw === "") {
    return { ok: true };
  }
  if (typeof raw !== "string" || !isSkillSlug(raw)) {
    return { ok: false, error: "invalid slug" };
  }
  return { ok: true, slug: raw };
}

export type GatewayPause = {
  paused: boolean;
  at: string | null;
  note: string | null;
};

/**
 * Global gateway pause flag. The payload-side interceptor (another worker's
 * area) reads this file; the UI layer here only owns the read/write API.
 * Missing or unreadable file means "not paused" -- pausing must be an
 * explicit user act, never a crash default.
 */
export function readPause(current: SupervisorDeps): GatewayPause {
  const raw = current.fs.read(current.paths.pause);
  if (raw === undefined) {
    return { paused: false, at: null, note: null };
  }
  try {
    const parsed = JSON.parse(raw) as { paused?: unknown; at?: unknown; note?: unknown };
    return {
      paused: parsed.paused === true,
      at: typeof parsed.at === "string" && parsed.at ? parsed.at : null,
      note: typeof parsed.note === "string" && parsed.note ? parsed.note : null,
    };
  } catch {
    return { paused: false, at: null, note: null };
  }
}

/**
 * Persist the pause flag with tmp+rename atomicity (same discipline as the
 * request-log rewrites) so a crash can never leave a truncated JSON behind.
 */
export function writePause(current: SupervisorDeps, input: { paused: boolean; note?: string | undefined }): GatewayPause {
  const state: GatewayPause = {
    paused: input.paused,
    at: new Date().toISOString(),
    note: typeof input.note === "string" && input.note.trim() ? input.note.trim().slice(0, 500) : null,
  };
  const body = `${JSON.stringify(state, null, 2)}\n`;
  // Tmp+rename: FsDeps has no rename, so build the sibling tmp path with
  // joinAbs (keeps the AbsPath brand) and move it with node:fs -- the UI
  // server always runs on real fs (deps() wires nodeFs).
  const lastSlash = current.paths.pause.lastIndexOf("/");
  const dir = current.paths.pause.slice(0, lastSlash + 1) as typeof current.paths.pause;
  const tmpPath = joinAbs(dir, "openbot-pause.json.tmp");
  current.fs.mkdirp(current.paths.sandData);
  fs.writeFileSync(tmpPath, body, { encoding: "utf8", mode: 0o644 });
  fs.renameSync(tmpPath, current.paths.pause);
  try {
    requestLog.appendEvent({
      type: "gateway.pause",
      severity: state.paused ? "WARN" : "INFO",
      message: state.paused
        ? `Gateway paused${state.note ? `: ${state.note}` : "."} New chat traffic is held.`
        : "Gateway resumed. Chat traffic flows again.",
    });
  } catch {
    /* event write is best-effort */
  }
  return state;
}

async function handlePauseApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<boolean> {
  const current = deps();
  if (req.method === "GET" && url.pathname === "/api/pause") {
    sendJson(res, 200, readPause(current));
    return true;
  }
  if (req.method === "PUT" && url.pathname === "/api/pause") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(await readBody(req)) as unknown;
    } catch {
      sendJson(res, 400, { error: "invalid json" });
      return true;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      sendJson(res, 400, { error: "body must be an object" });
      return true;
    }
    const { paused, note } = parsed as { paused?: unknown; note?: unknown };
    if (typeof paused !== "boolean") {
      sendJson(res, 400, { error: "paused must be a boolean" });
      return true;
    }
    if (note !== undefined && typeof note !== "string") {
      sendJson(res, 400, { error: "note must be a string" });
      return true;
    }
    const state = await enqueueSave(async () => writePause(current, { paused, note }));
    sendJson(res, 200, state);
    return true;
  }
  return false;
}

async function handleGrokSkillsApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<boolean> {
  if (req.method === "GET" && url.pathname === "/api/grok-skills") {
    const report = await grokSkillsStatus({ repoRoot });
    sendJson(res, 200, report);
    return true;
  }
  if (req.method === "POST" && url.pathname === "/api/grok-skills/install") {
    let parsed: unknown = {};
    const raw = await readBody(req);
    if (raw.trim()) {
      try {
        parsed = JSON.parse(raw) as unknown;
      } catch {
        sendJson(res, 400, { error: "invalid json" });
        return true;
      }
    }
    const slug = parseInstallSlug(parsed);
    if (!slug.ok) {
      sendJson(res, 400, { error: slug.error });
      return true;
    }
    const result = await enqueueSave(async () => {
      if (slug.slug !== undefined) {
        return installGrokSkills({ repoRoot, slug: slug.slug });
      }
      return installGrokSkills({ repoRoot });
    });
    if (result.ok) {
      sendJson(res, 200, { ok: true, ...result.report });
      return true;
    }
    sendJson(res, result.status, { ok: false, error: result.error, ...result.report });
    return true;
  }
  return false;
}

/**
 * Delivery follow-up ("injection hardening") switch. The hop hot-reads the file
 * this writes, so a change lands on the next request and needs no restart. The
 * file is read fresh every call because it may also be edited by hand.
 */
async function handleDeliverySettingsApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<boolean> {
  if (url.pathname !== "/api/settings/delivery") return false;
  const current = deps();
  const sandData = String(current.paths.sandData);
  if (req.method === "GET") {
    sendJson(res, 200, readDeliverySettings(sandData));
    return true;
  }
  if (req.method !== "PUT") {
    sendJson(res, 405, { error: "method not allowed" });
    return true;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readBody(req)) as unknown;
  } catch {
    sendJson(res, 400, { error: "invalid json" });
    return true;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    sendJson(res, 400, { error: "body must be an object" });
    return true;
  }
  const { mode, layers } = parsed as { mode?: unknown; layers?: unknown };
  if (!isInjectionMode(mode)) {
    sendJson(res, 400, { error: "mode must be one of off, dry-run, enforce" });
    return true;
  }
  const parsedLayers = parseInjectionLayers(layers);
  if (!parsedLayers.ok) {
    sendJson(res, 400, { error: parsedLayers.error });
    return true;
  }
  if (mode === "off" && parsedLayers.layers !== undefined) {
    // `off` deliberately keeps the file's layer blocks so switching back on
    // restores the tuning. Silently dropping a layers field the caller sent
    // would report success for a write that never happened; the web client
    // only sends layers alongside an enabled mode.
    sendJson(res, 400, { error: "layers cannot be set while mode is off" });
    return true;
  }
  const state = await enqueueSave(async () => writeDeliverySettings(sandData, { mode, layers: parsedLayers.layers }));
  sendJson(res, 200, { ok: true, ...state });
  return true;
}

const credentialDrafts = new CredentialDrafts();

async function handleApi(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<void> {
  const current = deps();
  if (await handleBotModelsApi(req, res, url, current, readBody, sendJson, catalogFromPlanJson)) return;
  if (req.method === "POST" && url.pathname === "/api/oauth/openai/start") {
    sendJson(res, 200, startOpenAIOAuth());
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/oauth/openai/complete") {
    try {
      const parsed = JSON.parse(await readBody(req)) as { sessionId?: unknown; callbackUrl?: unknown };
      if (typeof parsed.sessionId !== "string" || typeof parsed.callbackUrl !== "string") throw new Error("sessionId and callbackUrl are required");
      const credential = await completeOpenAIOAuth(parsed.sessionId, parsed.callbackUrl);
      // Completing sign-in does not replace any saved account. The opaque draft
      // is consumed only after the user adds an independently named account.
      sendJson(res, 200, { ok: true, ...credentialDrafts.stage(credential) });
    } catch (err) {
      sendJson(res, 400, { error: err instanceof Error ? err.message : "OAuth failed" });
    }
    return;
  }
  if (req.method === "GET" && (url.pathname === "/api/snapshot" || url.pathname === "/api/state")) {
    const snapshot = await observe(current);
    sendJson(res, 200, { snapshot: snapshotForUi(snapshot), ...publicState(current) });
    return;
  }
  if (req.method === "GET" && url.pathname === "/api/model-catalog") {
    const modelId = url.searchParams.get("modelId") ?? undefined;
    const providerId = url.searchParams.get("providerId");
    const provider = providerId === null ? undefined : catalogFromPlanJson(current.fs.read(current.paths.plan)).providers.find(p => p.id === providerId);
    if (providerId !== null && !provider) {
      sendJson(res, 404, { error: { kind: "provider-not-found", message: "provider not found" } });
      return;
    }
    sendJson(res, 200, { ...modelCatalog().snapshot(modelId, provider?.origin), autoRefresh: automaticModels().snapshot() });
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/model-catalog/refresh") {
    const { startedAt } = modelCatalog().refresh();
    void automaticModels().run().catch(() => {});
    sendJson(res, 202, { ok: true, status: "loading", startedAt });
    return;
  }
  if (req.method === "POST") {
    const providerId = providerIdFromFetchPath(url.pathname);
    if (providerId !== undefined) {
      const catalog = catalogFromPlanJson(current.fs.read(current.paths.plan));
      const store = loadSecrets(current.fs, current.paths.secrets);
      const result = await fetchConfiguredModels({ providerId, catalog, secretStore: store });
      sendJson(res, result.status, result.body);
      return;
    }
  }
  if (await handleGrokSkillsApi(req, res, url)) {
    return;
  }
  if (await handlePauseApi(req, res, url)) {
    return;
  }
  if (await handleDeliverySettingsApi(req, res, url)) {
    return;
  }
  if (await handlePauseBotsApi(req, res, url)) {
    return;
  }
  if (await handleLogsApi(req, res, url)) {
    return;
  }
  if (url.pathname === "/api/local-transfer" && (req.method === "GET" || req.method === "POST")) {
    try {
      let action: "start" | "resume" | undefined;
      if (req.method === "POST") {
        let body: unknown;
        try { body = JSON.parse(await readBody(req)); }
        catch { sendJson(res, 400, { error: "Expected a JSON transfer action." }); return; }
        if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).length !== 1 ||
            !("action" in body) || (body.action !== "start" && body.action !== "resume")) {
          sendJson(res, 400, { error: "Expected only action: start or resume." });
          return;
        }
        action = body.action;
      }
      const progress = await localTransfer(current, action);
      sendJson(res, action && !progress.available ? 409 : action ? 202 : 200,
        action && !progress.available ? { ...progress, error: "Install the owned Local transfer helper before starting." } : progress);
    } catch {
      // Never return helper stderr or control-plane exception bodies.
      sendJson(res, 409, { error: "Local transfer is unavailable. Use an opted-in Custom installation with owned recovery helpers." });
    }
    return;
  }
  if (req.method === "POST" && url.pathname === "/api/save") {
    await enqueueSave(async () => {
      let parsedBody: unknown = JSON.parse(await readBody(req));
      let credentialId: string | undefined;
      if (parsedBody !== null && typeof parsedBody === "object" && !Array.isArray(parsedBody)) {
        const body = parsedBody as Record<string, unknown>;
        if (body.credentialId !== undefined) {
          if (body.kind !== "add-provider" || body.familyId !== "openai" || typeof body.credentialId !== "string" || body.secret !== undefined) {
            throw new Error("A login draft must be added as a new OpenAI account without another credential.");
          }
          credentialId = body.credentialId;
          parsedBody = { ...body, secret: credentialDrafts.read(credentialId) };
        } else if (body.secret === "oauth" && (body.kind === "custom" || body.kind === "upsert-provider")) {
          throw new Error("Reload the page and add the account using its completed login draft.");
        }
      }
      const saved = catalogFromPlanJson(current.fs.read(current.paths.plan));
      const parsed = parseUiProviderSave(parsedBody, current.paths, saved, {
        expose: readExposeFile(current.fs, current.paths.expose),
        mode: wrapMode(current.fs.read(current.paths.mode)),
        reservedProviderIds: Object.keys(loadSecrets(current.fs, current.paths.secrets).providers),
      });
      const result = await reconcile(parsed.desired, current, {
        source: `ui:save:${parsed.kind}`,
        // If an account addition also repairs an outdated wrap, wait for the
        // current turns to finish before loading that repair into the host.
        ...(parsed.kind === "add-provider" ? { deferHostBounce: true } : {}),
      });
      if (result.kind === "refused") {
        sendJson(res, 409, result);
        return;
      }
      if (parsed.catalogWrite && parsed.catalogWrite.providers.length === 0) {
        current.fs.remove(current.paths.plan);
      } else if (parsed.catalogWrite && parsed.desired.kind === "official") {
        const custom = customBoxFromCatalog({ paths: current.paths, catalog: parsed.catalogWrite, expose: parsed.desired.expose });
        current.fs.write(current.paths.plan, planToJson(compileCustomPlan(custom)), 0o600);
      }
      if (parsed.secret) {
        const store = loadSecrets(current.fs, current.paths.secrets);
        saveSecrets(
          current.fs,
          current.paths.secrets,
          upsertSecret(store, parsed.secret.providerId, parsed.secret.bytes),
        );
      }
      if (credentialId) credentialDrafts.remove(credentialId);
      const catalog = catalogAfterSave(parsed, catalogFromPlanJson(current.fs.read(current.paths.plan)));
      sendJson(res, 200, {
        ok: true,
        wrapBytesChanged: result.wrapBytesChanged,
        ...(parsed.createdProviderId ? { createdProviderId: parsed.createdProviderId } : {}),
        snapshot: snapshotForUi(result.snapshot),
        ...publicState(current, catalog),
      });
    });
    return;
  }
  sendJson(res, 404, { error: "not found" });
}

function safeUiPath(urlPath: string): string | undefined {
  const rel = urlPath === "/" ? "/index.html" : urlPath;
  const resolved = path.resolve(uiDir, `.${rel}`);
  // Resolve both sides so a Unix-style env path (e.g. OPENBOT_REPO=/Code/...) and
  // the drive-lettered cwd resolve to the same absolute, normalized form on win32.
  const base = path.resolve(uiDir);
  const compare = (p: string) => (process.platform === "win32" ? p.toLowerCase() : p);
  const resolvedKey = compare(resolved);
  const baseKey = compare(base);
  // Require a trailing separator so a sibling prefix like "<ui>-evil" cannot pass.
  if (resolvedKey !== baseKey && !resolvedKey.startsWith(baseKey + path.sep)) {
    return undefined;
  }
  return resolved;
}

export async function handleRequest(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  try {
    const url = new URL(req.url ?? "/", `http://${host}:${String(port)}`);
    if (url.pathname.startsWith("/api/")) {
      await handleApi(req, res, url);
      return;
    }
    if (await hop.handleHopRequest(req, res)) {
      return;
    }
    const file = safeUiPath(url.pathname);
    if (file === undefined) {
      send(res, 403, "forbidden", "text/plain; charset=utf-8");
      return;
    }
    let body: Buffer;
    try {
      const stat = fs.statSync(file);
      if (!stat.isFile()) {
        send(res, 404, "not found", "text/plain; charset=utf-8");
        return;
      }
      body = fs.readFileSync(file);
    } catch (err) {
      const code = err && typeof err === "object" && "code" in err ? err.code : undefined;
      if (code === "ENOENT") {
        send(res, 404, "not found", "text/plain; charset=utf-8");
        return;
      }
      throw err;
    }
    const ext = path.extname(file);
    const type = TYPES[ext] ?? "application/octet-stream";
    send(res, 200, body, type);
  } catch (err) {
    // A handler (sync or async) threw: return a structured 500 rather than
    // letting the rejection escape into an uncaught exception.
    const message = err instanceof Error ? err.message : "internal error";
    if (!res.headersSent) {
      sendJson(res, 500, { error: { kind: "internal", message } });
    }
  }
}

const server = http.createServer((req, res) => {
  void handleRequest(req, res);
});

/**
 * Last-resort process guards. This is a single-user local tool: staying up
 * beats dying, so an unexpected exception or rejection is logged (with stack)
 * and the process keeps serving. These are a final safety net only — they are
 * NOT a substitute for local error handling; every child_process spawn and
 * async handler must still surface its own failures.
 */
export function registerProcessFallbacks(
  log: (line: string) => void = (line) => process.stderr.write(line),
): void {
  process.on("uncaughtException", (err) => {
    log(`OpenBot: uncaught exception (process kept alive): ${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  });
  process.on("unhandledRejection", (reason) => {
    log(
      `OpenBot: unhandled rejection (process kept alive): ${
        reason instanceof Error ? (reason.stack ?? reason.message) : String(reason)
      }\n`,
    );
  });
}

// Automatic log cleanup: retention + cap enforcement runs ~every 30 minutes
// (plus once shortly after startup), batched with event-loop yields so it
// never blocks chat. Every run appends a logs.cleanup event.
export const LOG_CLEANUP_INTERVAL_MS = 30 * 60 * 1000;
const LOG_CLEANUP_STARTUP_DELAY_MS = 60 * 1000;

let logCleanupTimer: ReturnType<typeof setInterval> | undefined;

function runScheduledCleanup(): void {
  try {
    const pending = requestLog.cleanupNowAsync() as Promise<unknown>;
    pending.catch(() => {
      /* best-effort: cleanup must never take down the control service */
    });
  } catch {
    /* best-effort */
  }
}

export function scheduleLogCleanup(): void {
  if (logCleanupTimer !== undefined) return;
  const startup = setTimeout(runScheduledCleanup, LOG_CLEANUP_STARTUP_DELAY_MS);
  if (typeof startup.unref === "function") startup.unref();
  logCleanupTimer = setInterval(runScheduledCleanup, LOG_CLEANUP_INTERVAL_MS);
  if (typeof logCleanupTimer.unref === "function") logCleanupTimer.unref();
}

export function stopLogCleanup(): void {
  if (logCleanupTimer !== undefined) {
    clearInterval(logCleanupTimer);
    logCleanupTimer = undefined;
  }
}

// Watch the watcher: the guard daemon is the only thing that heals host wrap
// drift, and nothing restarts it -- install.sh starts it once and reconcile
// only ever stops it. This service is the one long-lived process on the box,
// so it checks the daemon's pidfile on a timer and starts one the way
// install.sh does when a custom box has none. Full policy: guard-watch.ts.
let guardWatchTimer: ReturnType<typeof setInterval> | undefined;
const guardWatchState = createGuardWatchState();

function runScheduledGuardWatch(): void {
  try {
    const current = deps();
    runGuardWatchTick(current, guardWatchState, {
      // wrapMode owns the mode rule: only a literal "official" token is
      // official, so an unreadable mode file never hides a custom box's guard.
      mode: wrapMode(current.fs.read(current.paths.mode)),
      event: (entry) => {
        try {
          requestLog.appendEvent(entry);
        } catch {
          /* event write is best-effort */
        }
      },
      log: (line) => process.stderr.write(line),
    });
  } catch {
    /* best-effort: the babysitter must never take down the control service */
  }
}

export function scheduleGuardWatch(env: NodeJS.ProcessEnv = process.env): void {
  if (env[GUARD_WATCH_DISABLE_ENV] === "0") return;
  if (guardWatchTimer !== undefined) return;
  const startup = setTimeout(runScheduledGuardWatch, GUARD_WATCH_STARTUP_DELAY_MS);
  if (typeof startup.unref === "function") startup.unref();
  guardWatchTimer = setInterval(runScheduledGuardWatch, GUARD_WATCH_INTERVAL_MS);
  if (typeof guardWatchTimer.unref === "function") guardWatchTimer.unref();
}

export function stopGuardWatch(): void {
  if (guardWatchTimer !== undefined) {
    clearInterval(guardWatchTimer);
    guardWatchTimer = undefined;
  }
}

// Keep the public link alive: reconcile starts cloudflared when there is none
// to adopt, but nothing restarted it when the process died, so the user's
// trycloudflare URL went dark until they ran `openbot tunnel on` by hand. This
// service is the one long-lived process on the box, so it owns the same kind
// of bounded babysitter the guard has. Full policy: tunnel-watch.ts.
let tunnelWatchTimer: ReturnType<typeof setInterval> | undefined;
const tunnelWatchState = createTunnelWatchState();

function runScheduledTunnelWatch(): void {
  try {
    const current = deps();
    // The tick waits for cloudflared's hostname, so it is a promise; a slow
    // start must never surface as an unhandled rejection on the service.
    void runTunnelWatchTick(current, tunnelWatchState, {
      // Same rule as the guard watch: only a literal "official" token counts.
      mode: wrapMode(current.fs.read(current.paths.mode)),
      expose: readExposeFile(current.fs, current.paths.expose),
      event: (entry) => {
        try {
          requestLog.appendEvent(entry);
        } catch {
          /* event write is best-effort */
        }
      },
      log: (line) => process.stderr.write(line),
    }).catch(() => {
      /* best-effort: a failed restart is retried on a later tick */
    });
  } catch {
    /* best-effort: the babysitter must never take down the control service */
  }
}

export function scheduleTunnelWatch(env: NodeJS.ProcessEnv = process.env): void {
  if (env[TUNNEL_WATCH_DISABLE_ENV] === "0") return;
  if (tunnelWatchTimer !== undefined) return;
  const startup = setTimeout(runScheduledTunnelWatch, TUNNEL_WATCH_STARTUP_DELAY_MS);
  if (typeof startup.unref === "function") startup.unref();
  tunnelWatchTimer = setInterval(runScheduledTunnelWatch, TUNNEL_WATCH_INTERVAL_MS);
  if (typeof tunnelWatchTimer.unref === "function") tunnelWatchTimer.unref();
}

export function stopTunnelWatch(): void {
  if (tunnelWatchTimer !== undefined) {
    clearInterval(tunnelWatchTimer);
    tunnelWatchTimer = undefined;
  }
}

// Persistent local patches and the named tunnel are recovered independently of
// the native host bundle. The recovery helper uses its own inter-process lock.
let computerRecoveryTimer: ReturnType<typeof setInterval> | undefined;
const computerRecoveryState = { notBeforeMs: 0 };
function runScheduledComputerRecovery(): void {
  try { runComputerRecoveryWatchTick(deps(), computerRecoveryState); }
  catch { /* A failed recovery never stops the control service; retry on the next tick. */ }
}
export function scheduleComputerRecovery(env: NodeJS.ProcessEnv = process.env): void {
  if (env.OPENBOT_COMPUTER_RECOVERY === "0" || computerRecoveryTimer !== undefined) return;
  runScheduledComputerRecovery();
  computerRecoveryTimer = setInterval(runScheduledComputerRecovery, COMPUTER_RECOVERY_INTERVAL_MS);
  computerRecoveryTimer.unref();
}
export function stopComputerRecovery(): void {
  if (computerRecoveryTimer !== undefined) clearInterval(computerRecoveryTimer);
  computerRecoveryTimer = undefined;
}

function startServer(): void {
  // Direct server startup must use the same files as the supervisor and UI.
  process.env.OPENBOT_SECRETS ??= paths().secrets;
  process.env.OPENBOT_PLAN ??= paths().plan;
  registerProcessFallbacks();
  // Request-level fallbacks must not keep a failed, non-listening startup
  // alive. That duplicate must never publish ownership or start watchers.
  server.once("error", (error) => {
    process.stderr.write(`OpenBot: service listen failed: ${error.message}\n`);
    process.exit(1);
  });
  server.listen(port, host, () => {
    const box = paths();
    fs.mkdirSync(box.sandData, { recursive: true });
    fs.writeFileSync(box.uiPid, `${String(process.pid)}\n`);
    scheduleLogCleanup();
    scheduleComputerRecovery();
    scheduleGuardWatch();
    scheduleTunnelWatch();
    // A fresh service writes a turn lease immediately: from this moment a
    // deferred host bounce has a real idle oracle instead of "no evidence".
    // Best-effort — the lease must never keep the control page from serving.
    try {
      turnLease.touch();
    } catch {
      /* the lease is best-effort */
    }
    // Non-blocking: load the public catalog cache from disk, then re-fetch in the background.
    void modelCatalog().start();
    automaticModels().start();
    process.stdout.write(`openbot listening on http://${host}:${String(port)}\n`);
  });
}

// Start only when this file is the entry point; importing it (e.g. from tests)
// must not open a listener or install the process guards.
if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startServer();
}
