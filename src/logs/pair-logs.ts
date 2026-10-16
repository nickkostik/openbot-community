/**
 * Group hop + custom-host rows that belong to one custom chat turn.
 *
 * Recording still stores both layers (AGENTS.md). Pairing is UI-only:
 * Prefer the shared requestId; legacy rows use model, identity and the time
 * window. Official/auxiliary rows stay single.
 */

export const PAIR_WINDOW_MS = 8_000;

export type PairableLog = {
  id: string;
  startedAt: string;
  channel?: string;
  requestId?: string;
  providerId?: string;
  botId?: string;
  conversationId?: string;
  purpose?: string;
  model?: string;
  ok?: boolean;
  status?: number;
  latencyMs?: number;
  totalTokens?: number;
  stream?: boolean;
  error?: string;
};

export type LogRowPair<T extends PairableLog = PairableLog> =
  | { kind: "single"; record: T }
  | { kind: "pair"; hop: T; harness: T };

function isHop(channel: string | undefined): boolean {
  return channel === "hop" || channel === undefined || channel === "";
}

function isHarness(channel: string | undefined): boolean {
  return channel === "custom-host";
}

function startedMs(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : Number.NaN;
}

function modelIdentity(row: PairableLog): { provider: string | undefined; slug: string } {
  const model = row.model ?? "";
  const colon = model.indexOf(":");
  const prefix = colon > 0 ? model.slice(0, colon) : undefined;
  const qualified = prefix && (!row.providerId || row.providerId === prefix);
  return { provider: row.providerId || prefix, slug: qualified ? model.slice(colon + 1) : model };
}

function sameModel(a: PairableLog, b: PairableLog): boolean {
  const left = modelIdentity(a), right = modelIdentity(b);
  return !(left.provider && right.provider && left.provider !== right.provider) && left.slug === right.slug;
}

function sameKnownBot(a: PairableLog, b: PairableLog): boolean {
  return !(a.botId && b.botId && a.botId !== b.botId);
}

function legacyIdentity(a: PairableLog, b: PairableLog): boolean {
  return sameKnownBot(a, b) && !(a.conversationId && b.conversationId && a.conversationId !== b.conversationId) && sameModel(a, b);
}

function hopList<T extends PairableLog>(records: readonly T[]): T[] {
  return records.filter((row) => isHop(row.channel) && (!row.purpose || row.purpose === "chat"));
}

function harnessList<T extends PairableLog>(records: readonly T[]): T[] {
  return records.filter((row) => isHarness(row.channel));
}

/**
 * Pair shared request IDs first, even when the provider slug differs from the
 * harness's qualified model ID or queueing exceeds the legacy time window.
 * Remaining legacy rows use the nearest unused matching model and identity.
 * Prefer a harness that started at or before the hop
 * (host wrap starts, then POST /v1/chat/completions). Walk hops oldest-first
 * so consecutive tool-loop turns do not steal each other's mate.
 * Output order follows the input list (API is newest-first).
 */
export function pairLogRows<T extends PairableLog>(
  records: readonly T[],
  windowMs: number = PAIR_WINDOW_MS,
): Array<LogRowPair<T>> {
  const hops = hopList(records);
  const harnesses = harnessList(records);
  const usedHarness = new Set<string>();
  const mateByHopId = new Map<string, T>();
  const mateByHarnessId = new Map<string, T>();

  const hopsByTime = hops.slice().sort((left, right) => {
    const a = startedMs(left.startedAt);
    const b = startedMs(right.startedAt);
    if (a === b) return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
    if (!Number.isFinite(a)) return 1;
    if (!Number.isFinite(b)) return -1;
    return a - b;
  });

  function pair(hop: T, harness: T): void {
    usedHarness.add(harness.id);
    mateByHopId.set(hop.id, harness);
    mateByHarnessId.set(harness.id, hop);
  }

  // Reserve exact mates before any legacy heuristic can steal one.
  for (const hop of hopsByTime) {
    if (!hop.requestId) continue;
    const mate = harnesses.find(harness => !usedHarness.has(harness.id) && harness.requestId === hop.requestId && sameKnownBot(hop, harness));
    if (mate) pair(hop, mate);
  }

  for (const hop of hopsByTime) {
    if (mateByHopId.has(hop.id)) continue;
    const hopT = startedMs(hop.startedAt);
    let best: T | undefined;
    let bestScore = Infinity;
    for (const harness of harnesses) {
      if (usedHarness.has(harness.id)) continue;
      if (hop.requestId && harness.requestId) continue; // distinct requests
      if (!legacyIdentity(hop, harness)) continue;
      const harT = startedMs(harness.startedAt);
      if (!Number.isFinite(hopT) || !Number.isFinite(harT)) continue;
      const dist = Math.abs(hopT - harT);
      if (dist > windowMs) continue;
      const beforeBonus = harT <= hopT ? 0 : 0.5;
      const score = dist + beforeBonus;
      if (score < bestScore) {
        best = harness;
        bestScore = score;
      }
    }
    if (best) {
      pair(hop, best);
    }
  }

  const emitted = new Set<string>();
  const out: Array<LogRowPair<T>> = [];
  for (const rec of records) {
    if (emitted.has(rec.id)) continue;
    if (isHop(rec.channel)) {
      const mate = mateByHopId.get(rec.id);
      if (mate) {
        out.push({ kind: "pair", hop: rec, harness: mate });
        emitted.add(rec.id);
        emitted.add(mate.id);
        continue;
      }
    }
    if (isHarness(rec.channel)) {
      const mate = mateByHarnessId.get(rec.id);
      if (mate) {
        out.push({ kind: "pair", hop: mate, harness: rec });
        emitted.add(rec.id);
        emitted.add(mate.id);
        continue;
      }
    }
    out.push({ kind: "single", record: rec });
    emitted.add(rec.id);
  }
  return out;
}

export function pairKey(pair: LogRowPair): string {
  return pair.kind === "pair" ? `${pair.hop.id}+${pair.harness.id}` : pair.record.id;
}

export function pairContainsId(pair: LogRowPair, id: string): boolean {
  if (pair.kind === "single") return pair.record.id === id;
  return pair.hop.id === id || pair.harness.id === id;
}

export function pairOpenId(pair: LogRowPair): string {
  return pair.kind === "pair" ? pair.hop.id : pair.record.id;
}

export function pairIds(pair: LogRowPair): string[] {
  return pair.kind === "pair" ? [pair.hop.id, pair.harness.id] : [pair.record.id];
}

export function pairStartedAt(pair: LogRowPair): string {
  if (pair.kind === "single") return pair.record.startedAt;
  return pair.hop.startedAt <= pair.harness.startedAt ? pair.hop.startedAt : pair.harness.startedAt;
}

export function pairModel(pair: LogRowPair): string | undefined {
  if (pair.kind === "single") return pair.record.model;
  return pair.hop.model ?? pair.harness.model;
}

export function pairStatus(pair: LogRowPair): number {
  if (pair.kind === "single") return pair.record.status ?? 0;
  const hopStatus = pair.hop.status ?? 0;
  const harnessStatus = pair.harness.status ?? 0;
  if (pair.hop.ok === false || hopStatus >= 400) return hopStatus;
  if (pair.harness.ok === false || harnessStatus >= 400) return harnessStatus;
  return hopStatus || harnessStatus;
}

export function pairError(pair: LogRowPair): string | undefined {
  if (pair.kind === "single") return pair.record.error;
  return pair.hop.error || pair.harness.error;
}

export function pairLatency(pair: LogRowPair): number | undefined {
  if (pair.kind === "single") return pair.record.latencyMs;
  return pair.harness.latencyMs ?? pair.hop.latencyMs;
}

export function pairTokens(pair: LogRowPair): number | undefined {
  if (pair.kind === "single") return pair.record.totalTokens;
  return pair.hop.totalTokens ?? pair.harness.totalTokens;
}

export function pairStream(pair: LogRowPair): boolean {
  if (pair.kind === "single") return Boolean(pair.record.stream);
  return Boolean(pair.hop.stream || pair.harness.stream);
}

export function pairChannels(pair: LogRowPair): Array<string | undefined> {
  if (pair.kind === "single") return [pair.record.channel];
  return ["hop", "custom-host"];
}

export function findPairById<T extends PairableLog>(
  pairs: readonly LogRowPair<T>[],
  id: string,
): LogRowPair<T> | undefined {
  return pairs.find((pair) => pairContainsId(pair, id));
}
