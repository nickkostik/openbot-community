import { createHash } from "node:crypto";
import { OPENBOT_MARKER, type AbsPath } from "../domain/types.ts";
import { PAYLOAD_FINGERPRINT_FILES, PAYLOAD_VERSION_FILE, payloadFingerprint } from "../host/payload-fingerprint.ts";
import { refreshPayloadStamp, stripWrap, wrapHostSource } from "../host/wrap.ts";
import { joinAbs } from "./paths.ts";
import { type SupervisorDeps } from "./observe.ts";

const MAX_SOURCE = 48 * 1024 * 1024;
const COMPAT_MARKER = "/* openbot-latest-box-compatibility-v1 */";
const sha = (value: string): string => createHash("sha256").update(value).digest("hex");
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const count = (source: string, needle: string): number => source.split(needle).length - 1;

type FileProof = { path: AbsPath; limit: number; directories: readonly AbsPath[]; value: string | undefined };
export type EngineSelection = {
  readonly source: string;
  /** Original vendor bytes survive independently of the composed BOX stock. */
  readonly vendor?: string;
  readonly vendorRecord?: string;
  readonly officialRestore: boolean;
  readonly unchanged: () => boolean;
};

/** A policy selects an artifact; it never grants permission to invent a runner. */
function requireRunner(source: string): void {
  const anchor = "  async sendPrompt(prompt, options2) {";
  const body = source.split(anchor)[1]?.split("\n  nextTurnEpoch(")[0];
  if (count(source, anchor) !== 1 || body === undefined || body.length < 4000 ||
      !source.includes("runServerScheduledAutomation") || !source.includes("runAgentTurn")) {
    throw new Error("Pinned engine has no complete native BOX runner");
  }
}

/**
 * Read-only preparation. Reconcile syntax-checks the candidate and rechecks
 * every artifact plus host/mode before publishing it. No policy is modified.
 */
export function selectLocalEngine(
  deps: SupervisorDeps, kind: "custom" | "official", current: string, fingerprint: string,
): EngineSelection | undefined {
  const read = deps.fs.readOwned;
  // Old test adapters cannot assert ownership. They keep the strict census
  // path instead of silently treating an untrusted policy as a trusted pin.
  if (read === undefined) return undefined;
  if (deps.fs.writeAtomic === undefined) throw new Error("Pinned engines require atomic source publication");
  const root = deps.paths.sandData;
  const proofs: FileProof[] = [];
  function owned(path: AbsPath, limit: number, directories: readonly AbsPath[] = [root]): string | undefined {
    const value = read!.call(deps.fs, path, limit, directories);
    proofs.push({ path, limit, directories, value });
    return value;
  }
  const recordPath = joinAbs(root, "openbot-official-host.json");
  const vendorPath = joinAbs(root, "openbot-official-host.cjs");
  function vendorRecord(): string {
    const raw = owned(recordPath, 16384);
    if (raw === undefined) {
      // Existing installations predate the independent snapshot. Reuse only
      // their authenticated, exact-hash vendor capture, never the composed
      // engine's stock.cjs (which still contains local execution ports).
      const captured = owned(joinAbs(root, "openbot-upstream-sync-state.json"), 262144);
      const pin = owned(joinAbs(root, "openbot-local-engine.json"), 16384);
      const state = captured === undefined ? {} : JSON.parse(captured) as Record<string, unknown>;
      const policy = pin === undefined ? {} : JSON.parse(pin) as Record<string, unknown>;
      const authority = state.upstreamVersion && state.upstreamSourceSha256 ? state : policy;
      if (typeof authority.upstreamVersion !== "string" || !/^[0-9a-f]{7,40}$/.test(authority.upstreamVersion) ||
          !digest(authority.upstreamSourceSha256)) throw new Error("Original Official vendor snapshot is missing");
      const base = joinAbs(root, "openbot-upstream");
      const releases = joinAbs(base, "releases");
      const directory = joinAbs(releases, authority.upstreamVersion);
      const dirs = [root, base, releases, directory];
      const metadataRaw = owned(joinAbs(directory, "vendor.json"), 16384, dirs);
      const source = owned(joinAbs(directory, "host-main.cjs"), MAX_SOURCE, dirs);
      const metadata = metadataRaw === undefined ? {} : JSON.parse(metadataRaw) as Record<string, unknown>;
      if (source === undefined || metadata.sourceSha256 !== authority.upstreamSourceSha256 ||
          sha(source) !== authority.upstreamSourceSha256 || source.includes(OPENBOT_MARKER) || source.includes(COMPAT_MARKER)) {
        throw new Error("Authenticated Official vendor capture is invalid");
      }
      return source;
    }
    const value = JSON.parse(raw) as Record<string, unknown>;
    const source = owned(vendorPath, MAX_SOURCE);
    if (value.schemaVersion !== 1 || !digest(value.sha256) || source === undefined || sha(source) !== value.sha256 ||
        source.includes(OPENBOT_MARKER) || source.includes(COMPAT_MARKER)) {
      throw new Error("Original Official vendor snapshot is invalid");
    }
    return source;
  }
  const unchanged = (): boolean => proofs.every((proof) =>
    read!.call(deps.fs, proof.path, proof.limit, proof.directories) === proof.value);
  if (kind === "official") {
    // A normal vendor update already in place is authoritative. Only undo a
    // composed BOX engine, never downgrade fresh stock to a stale snapshot.
    const saved = owned(recordPath, 16384);
    if (saved === undefined && !current.includes(COMPAT_MARKER)) return undefined;
    let wasSelected = current.includes(COMPAT_MARKER);
    if (!wasSelected && saved !== undefined) {
      const value = JSON.parse(saved) as Record<string, unknown>;
      const normalized = refreshPayloadStamp(current, "0000000000000000").source;
      wasSelected = digest(value.localNormalizedSha256) && sha(normalized) === value.localNormalizedSha256;
    }
    if (!wasSelected) return undefined;
    return { source: vendorRecord(), officialRestore: true, unchanged };
  }
  const policyPath = joinAbs(root, "openbot-local-engine.json");
  const policyRaw = owned(policyPath, 16384);
  if (policyRaw === undefined) return undefined;
  const policy = JSON.parse(policyRaw) as Record<string, unknown>;
  if (policy.schemaVersion !== 1 || typeof policy.enabled !== "boolean") throw new Error("Unsupported local engine policy");
  if (!policy.enabled) return undefined;
  if (!digest(policy.stockSha256) || !digest(policy.wrappedSha256)) throw new Error("Local engine requires both exact SHA256 hashes");
  let directory = joinAbs(root, "openbot-local-engine");
  const directories: AbsPath[] = [root, directory];
  if (policy.generationId !== undefined) {
    if (typeof policy.generationId !== "string" || !/^[0-9a-f]{32}$/.test(policy.generationId)) throw new Error("Invalid local engine generation");
    directory = joinAbs(directory, "generations");
    directories.push(directory);
    directory = joinAbs(directory, policy.generationId);
    directories.push(directory);
  }
  const stock = owned(joinAbs(directory, "stock.cjs"), MAX_SOURCE, directories);
  const wrapped = owned(joinAbs(directory, "wrapped.cjs"), MAX_SOURCE, directories);
  if (stock === undefined || wrapped === undefined || sha(stock) !== policy.stockSha256 || sha(wrapped) !== policy.wrappedSha256) {
    throw new Error("Pinned local engine pair is missing or its hash differs");
  }
  requireRunner(stock);
  requireRunner(wrapped);
  const proof = wrapHostSource({ source: stock, runtimePath: deps.paths.runtime, payloadFingerprint: fingerprint });
  const selected = refreshPayloadStamp(wrapped, fingerprint).source;
  if (stock.includes(OPENBOT_MARKER) || count(wrapped, OPENBOT_MARKER) !== 1 || proof.kind !== "wrapped" || proof.source !== selected) {
    throw new Error("Pinned engine pair is not the canonical wrapper for this installation");
  }
  const normalized = (source: string): string => refreshPayloadStamp(source, "0000000000000000").source;
  const alreadyLocal = current.includes(COMPAT_MARKER) || normalized(current) === normalized(wrapped) || current === stock;
  const vendor = alreadyLocal ? vendorRecord() : stripWrap(current);
  if (vendor.includes(OPENBOT_MARKER) || vendor.includes(COMPAT_MARKER)) throw new Error("Cannot preserve a local engine as Official vendor");
  // Include both targets in the compare set, even when absent, to avoid
  // overwriting another save's snapshot after the syntax check yields.
  if (!alreadyLocal) { owned(recordPath, 16384); owned(vendorPath, MAX_SOURCE); }
  const record = JSON.stringify({ schemaVersion: 1, sha256: sha(vendor), localNormalizedSha256: sha(normalized(selected)) }) + "\n";
  return { source: selected, vendor, vendorRecord: record, officialRestore: false, unchanged };
}

export function publishOfficialSnapshot(deps: SupervisorDeps, selection: EngineSelection): void {
  if (selection.vendor === undefined || selection.vendorRecord === undefined) return;
  deps.fs.writeAtomic!(joinAbs(deps.paths.sandData, "openbot-official-host.cjs"), selection.vendor, 0o600);
  deps.fs.writeAtomic!(joinAbs(deps.paths.sandData, "openbot-official-host.json"), selection.vendorRecord, 0o600);
}

type Readiness = { state: "ready" | "unavailable" | "not-pinned"; nativeDeliveryVerified: false };
let readinessCache: { key: string; until: number; checker: SupervisorDeps["procs"]["dependencyCheck"]; value: Readiness } | undefined;

function readinessKey(deps: SupervisorDeps): string | undefined {
  if (deps.fs.identity === undefined || deps.fs.readOwned === undefined) return undefined;
  const root = deps.paths.sandData;
  const policy = joinAbs(root, "openbot-local-engine.json");
  const raw = deps.fs.readOwned(policy, 16384, [root]);
  let engine = joinAbs(root, "openbot-local-engine");
  const files = [root, policy, engine, deps.paths.hostMain, deps.paths.mode, deps.paths.plan,
    joinAbs(root, "openbot-official-host.cjs"), joinAbs(root, "openbot-official-host.json"), joinAbs(root, "openbot-upstream-sync-state.json")];
  if (raw !== undefined) {
    const generation = (JSON.parse(raw) as { generationId?: unknown }).generationId;
    if (typeof generation === "string" && /^[0-9a-f]{32}$/.test(generation)) {
      engine = joinAbs(engine, "generations"); files.push(engine);
      engine = joinAbs(engine, generation); files.push(engine);
    }
  }
  files.push(joinAbs(engine, "stock.cjs"), joinAbs(engine, "wrapped.cjs"));
  files.push(...[...PAYLOAD_FINGERPRINT_FILES, PAYLOAD_VERSION_FILE].map((name) => joinAbs(deps.paths.repoRoot, "payload", name)));
  return files.map((file) => `${file}=${deps.fs.identity!(file) ?? "absent"}`).join("\n");
}

/** Safe UI hint only. Explicit saves repeat every check before publication. */
export function localEngineReadiness(deps: SupervisorDeps): Readiness {
  let key: string | undefined;
  try {
    key = readinessKey(deps);
    if (key !== undefined && readinessCache?.key === key && readinessCache.checker === deps.procs.dependencyCheck && readinessCache.until > Date.now()) {
      return readinessCache.value;
    }
    const value = inspectLocalEngineReadiness(deps);
    if (key !== undefined) readinessCache = { key, checker: deps.procs.dependencyCheck, until: Date.now() + 5000, value };
    return value;
  } catch {
    return { state: "unavailable", nativeDeliveryVerified: false };
  }
}

function inspectLocalEngineReadiness(deps: SupervisorDeps): Readiness {
  try {
    const current = deps.fs.read(deps.paths.hostMain);
    if (current === undefined) return { state: "unavailable", nativeDeliveryVerified: false };
    const mode = deps.fs.read(deps.paths.mode);
    const fingerprint = payloadFingerprint({ payloadDir: joinAbs(deps.paths.repoRoot, "payload"), read: (p) => deps.fs.read(p as AbsPath) });
    const selection = selectLocalEngine(deps, "custom", current, fingerprint);
    if (selection === undefined) return { state: "not-pinned", nativeDeliveryVerified: false };
    const dependencies = deps.procs.dependencyCheck?.(selection.source, deps.paths.hostMain);
    if (!dependencies?.ok || deps.fs.read(deps.paths.hostMain) !== current || deps.fs.read(deps.paths.mode) !== mode || !selection.unchanged()) {
      return { state: "unavailable", nativeDeliveryVerified: false };
    }
    return { state: "ready", nativeDeliveryVerified: false };
  } catch {
    return { state: "unavailable", nativeDeliveryVerified: false };
  }
}
