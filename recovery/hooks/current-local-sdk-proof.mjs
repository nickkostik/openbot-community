/** Verify a detached fresh owner without rewriting its current Local SDK. */
import fs from 'node:fs';
import path from 'node:path';
import Module, { createRequire } from 'node:module';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { boundedOwnedFile, requireNonWalHeader, forEachSqliteRow, digest,
  scanIdentifierStrings, classifyRawBlob, WORKER_SHA } from './history-identifier-sdk-check.mjs';

export const CODEC_SHA = '74278c1ec791b38a235bf68e386dbbe86428bd2f9966baceb29a1173bc574dc3';
export const ALLOCATOR_SHA = '4c1ff1a360b58c6010695fb531638efe147a493ea2da9c16f35fced1f8a2ccaa';
const require = createRequire(import.meta.url), MAX_FILE = 128 * 1024 ** 2;
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const SHA = /^[0-9a-f]{64}$/;
export const ROOT_SLOT = Buffer.from('sand-live-conversation-root-v1__').toString('hex');
const hex = bytes => Buffer.from(bytes).toString('hex');
function refuse(code) { const error = new Error(code); error.code = code; throw error; }
function check(condition, code) { if (!condition) refuse(code); }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

export function validatePlan(plan) {
  check(object(plan) && plan.schemaVersion === 1 && UUID.test(plan.sourceId) && UUID.test(plan.targetId) &&
    plan.sourceId !== plan.targetId, 'PLAN_IDENTITY');
  check(SHA.test(plan.expectedRootSHA) && SHA.test(plan.expectedStoreSHA), 'PLAN_CHECKPOINT');
  check(Array.isArray(plan.sourceGraphs) && plan.sourceGraphs.length > 0 && plan.sourceGraphs.length <= 32 &&
    object(plan.sourceRoots), 'PLAN_SOURCE_GRAPHS');
  const ids = new Set();
  for (const source of plan.sourceGraphs) {
    check(object(source) && UUID.test(source.agentId) && !ids.has(source.agentId) && typeof source.path === 'string' &&
      SHA.test(source.sha256) && (source.rootSHA === null || SHA.test(source.rootSHA)), 'PLAN_SOURCE_GRAPH');
    ids.add(source.agentId); check(plan.sourceRoots[source.agentId] === source.rootSHA, 'PLAN_SOURCE_ROOTS');
  }
  check(Object.keys(plan.sourceRoots).length === ids.size && ids.has(plan.sourceId) &&
    plan.sourceRoots[plan.sourceId] === plan.expectedRootSHA, 'PLAN_CURRENT_ROOT');
  const route = plan.selectedRoute;
  check(object(route) && typeof route.modelId === 'string' && route.modelId.length > 0 && route.modelId.length <= 1024 &&
    Number.isSafeInteger(route.contextTokens) && route.contextTokens > 0 && route.contextTokens < 2 ** 32 &&
    Number.isSafeInteger(route.maxOutputTokens) && route.maxOutputTokens > 0 && route.maxOutputTokens < route.contextTokens,
  'PLAN_CAPACITY');
  return [...ids].sort();
}

function privateCandidate(directory) {
  check(typeof directory === 'string' && path.isAbsolute(directory) && path.normalize(directory) === directory &&
    path.basename(directory).startsWith('openbot-history-tail-'), 'CANDIDATE_PATH');
  const info = fs.lstatSync(directory);
  check(info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid?.() && (info.mode & 0o077) === 0,
    'CANDIDATE_PRIVATE');
  const marker = path.join(directory, '.openbot-detached-history-tail.json');
  boundedOwnedFile(marker, 1024, { privateFile: true });
  const declared = JSON.parse(fs.readFileSync(marker));
  check(declared.schemaVersion === 1 && declared.detached === true, 'CANDIDATE_MARKER');
  for (const file of ['store.db', 'conversation-blobs.db']) {
    const filename = path.join(directory, file); boundedOwnedFile(filename, MAX_FILE, { privateFile: true });
    check(fs.statSync(filename).nlink === 1, 'CANDIDATE_HARDLINK');
    checkDatabase(filename);
  }
  for (const file of ['current-local-sdk-report.json', 'current-local-sdk-proof.json']) {
    check(!fs.existsSync(path.join(directory, file)), 'PROOF_EXISTS');
  }
  return directory;
}

function checkDatabase(filename) {
  boundedOwnedFile(filename, MAX_FILE);
  for (const suffix of ['-wal', '-shm', '-journal']) check(!fs.existsSync(filename + suffix), 'DATABASE_SIDECAR');
  requireNonWalHeader(fs.readFileSync(filename).subarray(0, 100));
}

/** The native semantic proof is compiled only after all executable bytes pin. */
export function reviewedSources(hostRaw, workerRaw, allocatorRaw) {
  check(digest(hostRaw) === CODEC_SHA, 'UNREVIEWED_HOST');
  check(digest(workerRaw) === WORKER_SHA, 'UNREVIEWED_WORKER');
  const original = hostRaw.toString('utf8'), boot = 'bootSandHost(runHostMain).catch(';
  const start = original.indexOf(boot), end = original.indexOf('/*! Bundled license information:', start);
  check(start > 0 && end > start && original.indexOf(boot, start + 1) < 0, 'HOST_BOOT_BOUNDARY');
  const exports = `module.exports={Root:ConversationStateStructure,Turn:ConversationTurnStructure,User:UserMessage,
    context:createContext,parseCore:parseCoreMessageJson,derive:deriveConversationStateFromStructure,
    Handle:ConversationStateHandle,redact:toRedactedConversationStateStructure,
    unredact:messages=>fromRedactedCoreMessages(messages,PrivacyCapability.UNSAFE_ALWAYS_ALLOWED),
    estimate:estimateTokenCount,metadata:agentMetadataSerde,parse:parseTranscriptEntry,spend:parseSandSpendInitiationEntry,
    prepare:prepareStatements,window:readTranscriptWindow};\n`;
  const worker = workerRaw.toString('utf8'), main = '\nmain();\n';
  check(worker.split(main).length === 2, 'WORKER_BOOT_BOUNDARY');
  const allocator = allocatorRaw.toString('utf8'), begin = 'var __openbot_box_BOOT_TURN = "b";';
  const finish = '\n// src/host/extensions/transcript/turn-runtime.ts';
  const a = allocator.indexOf(begin), b = allocator.indexOf(finish, a);
  check(a >= 0 && b > a && allocator.indexOf(begin, a + 1) < 0 && allocator.indexOf(finish, b + finish.length) < 0,
    'ALLOCATOR_BOUNDARY');
  const mint = allocator.slice(a, b); check(digest(mint) === ALLOCATOR_SHA, 'UNREVIEWED_ALLOCATOR');
  return { host: original.slice(0, start) + exports + original.slice(end),
    worker: worker.replace(main, '\nmodule.exports={walk:collectReachableBlobHexIds,Step:ConversationStep};\n'), allocator: mint };
}

function denySideEffects() {
  const restores = [], counts = { networkAttempts: 0, toolAttempts: 0 };
  function network() { counts.networkAttempts++; refuse('NETWORK_DENIED'); }
  function tool() { counts.toolAttempts++; refuse('TOOL_DENIED'); }
  function patch(target, key, value) {
    const old = Object.getOwnPropertyDescriptor(target, key); Object.defineProperty(target, key, { value, configurable: true, writable: true });
    restores.push(() => old ? Object.defineProperty(target, key, old) : delete target[key]);
  }
  patch(globalThis, 'fetch', network); patch(globalThis, 'WebSocket', network);
  for (const name of ['node:http', 'node:https']) for (const key of ['request', 'get']) patch(require(name), key, network);
  for (const key of ['connect', 'createConnection']) patch(require('node:net'), key, network);
  patch(require('node:net').Socket.prototype, 'connect', network); patch(require('node:tls'), 'connect', network);
  patch(require('node:http2'), 'connect', network); patch(require('node:dgram'), 'createSocket', network);
  for (const key of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) patch(require('node:child_process'), key, tool);
  patch(require('node:worker_threads'), 'Worker', tool); patch(require('node:cluster'), 'fork', tool);
  return { counts, restore() { for (const restore of restores.reverse()) restore(); } };
}

function snapshotGraph(db) {
  const graph = new Map();
  forEachSqliteRow(db, 'SELECT id,data FROM blobs ORDER BY id', row => {
    check(typeof row.id === 'string' && row.data instanceof Uint8Array && !graph.has(row.id), 'BLOB_SCHEMA');
    graph.set(row.id, digest(row.data)); check(graph.size <= 200000, 'BLOB_BOUND');
  });
  return graph;
}

export function verifyArchiveGraph(candidate, source, rootSHA) {
  if (rootSHA === null) check(source.size === 0, 'ABSENT_ARCHIVE_ROOT');
  else check(source.get(ROOT_SLOT) === rootSHA && candidate.get(rootSHA) === rootSHA, 'SOURCE_ROOT_ARCHIVE');
  for (const [id, hash] of source) if (id !== ROOT_SLOT) check(candidate.get(id) === hash, 'SOURCE_BLOB_ARCHIVE');
}

export function ownerReferences(value, ownerIds) {
  const scanned = scanIdentifierStrings(value, ownerIds);
  check(scanned.structuralReferences.length === 0, 'STRUCTURAL_OWNER_REFERENCE');
  return scanned.literalReferences;
}

/** A completed shell step records where that PAST command ran. Its namespace
 * must remain unchanged; it is not the next turn's owner binding. This narrow
 * exception requires a native successful result, completed timestamps, exact
 * binary roundtrip (checked by caller), and complete raw occurrence coverage. */
export function completedShellOwnerProvenance(step, raw, ownerIds) {
  const call = step.message?.value, shell = call?.tool?.value, args = shell?.args;
  check(step.message?.case === 'toolCall' && call?.tool?.case === 'shellToolCall' &&
    typeof call.startedAtMs === 'bigint' && typeof call.completedAtMs === 'bigint' &&
    call.startedAtMs >= 0n && call.completedAtMs > 0n && call.completedAtMs >= call.startedAtMs &&
    shell?.result?.result?.case === 'success' && object(shell.result.result.value) &&
    object(args) && ownerIds.includes(args.conversationId), 'UNREVIEWED_STEP_OWNER_REFERENCE');
  const sanitized = { ...step, message: { ...step.message, value: { ...call,
    tool: { ...call.tool, value: { ...shell, args: { ...args, conversationId: undefined } } } } } };
  ownerReferences(sanitized, ownerIds);
  const data = Buffer.from(raw);
  for (const id of ownerIds) {
    const needle = Buffer.from(id); let count = 0, offset = 0;
    while ((offset = data.indexOf(needle, offset)) >= 0) { count++; offset += needle.length; }
    check(count === (args.conversationId === id ? 1 : 0), 'UNKNOWN_STEP_OWNER_REFERENCE');
  }
  return { path: 'message.value.tool.value.args.conversationId', completed: true, result: 'success', bytesUnchanged: true };
}

function canonical(value) {
  if (value instanceof Uint8Array) return { bytes: value.length, sha256: digest(value) };
  if (Array.isArray(value)) return value.map(canonical);
  if (object(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}

export function proveCurrentSdk(options, plan) {
  const ownerIds = validatePlan(plan), directory = privateCandidate(options.candidateDir);
  boundedOwnedFile(options.planFile, 8 * 1024 ** 2, { privateFile: true });
  const planBytes = fs.readFileSync(options.planFile);
  check(JSON.stringify(canonical(JSON.parse(planBytes))) === JSON.stringify(canonical(plan)), 'PLAN_FILE_MISMATCH');
  for (const file of [options.host, options.worker, options.allocatorHost]) boundedOwnedFile(file, MAX_FILE, { allowRoot: true });
  const hostRaw = fs.readFileSync(options.host), workerRaw = fs.readFileSync(options.worker), allocatorRaw = fs.readFileSync(options.allocatorHost);
  const sources = reviewedSources(hostRaw, workerRaw, allocatorRaw);
  const beforeFiles = new Map();
  const storePath = path.join(directory, 'store.db'), blobsPath = path.join(directory, 'conversation-blobs.db');
  for (const file of [storePath, blobsPath]) beforeFiles.set(file, digest(fs.readFileSync(file)));
  check(beforeFiles.get(storePath) === plan.expectedStoreSHA, 'STORE_CHECKPOINT');
  for (const source of plan.sourceGraphs) {
    checkDatabase(source.path); check(digest(fs.readFileSync(source.path)) === source.sha256, 'SOURCE_CHECKPOINT');
    beforeFiles.set(source.path, source.sha256);
  }
  const guard = denySideEffects(), opened = [];
  try {
    const mod = new Module(options.host); mod.filename = path.join(path.dirname(options.host), 'openbot-current-local-sdk-check.cjs');
    mod.paths = [...new Set([options.host, options.worker, process.execPath].flatMap(file => Module._nodeModulePaths(path.dirname(file))))];
    mod._compile(sources.host, mod.filename);
    const worker = new Module(options.worker); worker.filename = options.worker;
    worker.paths = Module._nodeModulePaths(path.dirname(options.worker)); worker._compile(sources.worker, options.worker);
    const sandbox = {}; vm.createContext(sandbox); vm.runInContext(sources.allocator + '\nthis.mint=__openbot_box_nextEntryId;', sandbox, { timeout: 1000 });
    const sdk = { ...mod.exports, walk: worker.exports.walk, Step: worker.exports.Step, nextId: sandbox.mint };
    const { DatabaseSync } = require('node:sqlite');
    function open(filename) { const db = new DatabaseSync(filename, { readOnly: true }); opened.push(db);
      assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok'); return db; }
    const sql = open(storePath), blobs = open(blobsPath), beforeGraph = snapshotGraph(blobs);
    const getStatement = blobs.prepare('SELECT data FROM blobs WHERE id=?'), get = id => getStatement.get(id)?.data;
    const rootBytes = get(ROOT_SLOT); check(rootBytes && digest(rootBytes) === plan.expectedRootSHA, 'CURRENT_ROOT_CHECKPOINT');
    const archiveReports = [];
    for (const source of plan.sourceGraphs) {
      const sourceDb = open(source.path), archived = snapshotGraph(sourceDb); verifyArchiveGraph(beforeGraph, archived, source.rootSHA);
      const root = sourceDb.prepare('SELECT data FROM blobs WHERE id=?').get(ROOT_SLOT)?.data;
      if (root) {
        const decoded = sdk.Root.fromBinary(root); assert.ok(Buffer.from(decoded.toBinary()).equals(Buffer.from(root)));
        const closure = sdk.walk({ rootBytes: root, getBlobByHexId: get, onBlobReference: id => check(get(id), 'MISSING_ARCHIVE_REFERENCE') });
        check(closure.unresolvedProtoRefs === 0, 'UNRESOLVED_ARCHIVE_GRAPH');
      }
      archiveReports.push({ agentId: source.agentId, rootSha256: source.rootSHA, blobs: archived.size, byteExact: true });
    }
    const root = sdk.Root.fromBinary(rootBytes); assert.ok(Buffer.from(root.toBinary()).equals(Buffer.from(rootBytes)));
    check(root.pendingToolCalls.length === 0, 'PENDING_TOOLS');
    const literal = [...ownerReferences(root, ownerIds)], sdkUserIds = new Set();
    for (const ref of root.turns) {
      const turn = sdk.Turn.fromBinary(get(hex(ref))); literal.push(...ownerReferences(turn, ownerIds));
      if (turn.turn.case === 'agentConversationTurn') {
        const user = sdk.User.fromBinary(get(hex(turn.turn.value.userMessage))); literal.push(...ownerReferences(user, ownerIds)); sdkUserIds.add(user.messageId);
      }
    }
    const coreIds = new Set(root.rootPromptMessagesJson.map(hex)), messages = [];
    for (const id of coreIds) {
      const body = get(id); check(body, 'MISSING_CORE_MESSAGE');
      const raw = JSON.parse(Buffer.from(body).toString('utf8')); literal.push(...ownerReferences(raw, ownerIds));
    }
    for (const ref of root.rootPromptMessagesJson) messages.push(sdk.parseCore(Buffer.from(get(hex(ref))).toString('utf8')));
    const closure = sdk.walk({ rootBytes, getBlobByHexId: get, onBlobReference: id => check(get(id), 'MISSING_CURRENT_REFERENCE') });
    check(closure.unresolvedProtoRefs === 0, 'UNRESOLVED_CURRENT_GRAPH');
    const activeIds = new Set([ROOT_SLOT, ...closure.blobTypeByHexId.keys()]), literalBlobs = new Set(), archivedHits = new Set();
    const completedToolProvenance = [];
    forEachSqliteRow(blobs, 'SELECT id,data FROM blobs ORDER BY id', row => {
      if (closure.blobTypeByHexId.get(row.id) === 'agent.v1.ConversationStep' &&
        ownerIds.some(id => Buffer.from(row.data).includes(Buffer.from(id)))) {
        const step = sdk.Step.fromBinary(row.data); assert.ok(Buffer.from(step.toBinary()).equals(Buffer.from(row.data)));
        completedToolProvenance.push({ blobSha256: digest(row.data), ...completedShellOwnerProvenance(step, row.data, ownerIds) });
        return;
      }
      const hit = classifyRawBlob({ id: row.id, data: row.data, activeIds, coreJsonIds: coreIds, ids: ownerIds });
      if (hit) (hit.active ? literalBlobs : archivedHits).add(hit.sha256);
    });
    const context = sdk.context(), store = { getBlob: async (_context, id) => get(hex(id)) };
    return verifyHandle({ sdk, sql, blobs, store, root, rootBytes, messages, context, beforeGraph, sdkUserIds,
      plan, options, planFileSha256: digest(planBytes), directory, beforeFiles, opened, guard, literal, literalBlobs, archivedHits, completedToolProvenance, archiveReports, closure });
  } catch (error) { for (const db of opened.reverse()) { try { db.close(); } catch {} } guard.restore(); throw error; }
}

async function verifyHandle(state) {
  const { sdk, sql, blobs, store, root, rootBytes, messages, context, beforeGraph, sdkUserIds,
    plan, options, planFileSha256, directory, beforeFiles, opened, guard, literal, literalBlobs, archivedHits, completedToolProvenance, archiveReports, closure } = state;
  try {
    const derived = await sdk.derive(context, root, store); assert.equal(derived.turns.length, root.turns.length);
    const builder = { messages: [], clearMessages() { this.messages = []; }, appendMessages(rows) { this.messages.push(...rows); }, getState() { return this.messages; } };
    const handle = await sdk.Handle.fromConversationStateStructure(context, store, sdk.redact(root, 2), builder, {}, 'fresh-current-local-sdk');
    const plain = sdk.unredact(builder.messages); assert.equal(JSON.stringify(canonical(plain)), JSON.stringify(canonical(messages)));
    for (const todo of handle.todos) await todo.get(context);
    const usedTokens = Math.max(sdk.estimate(messages, { includeNonTextContent: true }), root.tokenDetails?.usedTokens ?? 0);
    check(Number.isFinite(usedTokens) && usedTokens >= 0 && usedTokens + plan.selectedRoute.maxOutputTokens <= plan.selectedRoute.contextTokens, 'CONTEXT_CAPACITY');
    const metadataRaw = sql.prepare('SELECT value FROM kv WHERE key=?').get('metadata')?.value;
    check(typeof metadataRaw === 'string', 'METADATA_REQUIRED');
    const metadata = sdk.metadata.deserialize(Buffer.from(metadataRaw, 'hex'));
    check(metadata.agentId === plan.targetId && hex(metadata.latestRootBlobId ?? []) === ROOT_SLOT, 'TARGET_METADATA_OWNER');
    const rows = sql.prepare('SELECT id,entry FROM transcript_entries ORDER BY seq').all(); check(rows.length <= 200000, 'UI_RECORD_BOUND');
    const accepted = [], occupied = new Set();
    for (const row of rows) {
      check(!occupied.has(row.id), 'UI_DUPLICATE_ID'); occupied.add(row.id);
      const raw = JSON.parse(row.entry); check(raw.agentId === undefined || raw.agentId === plan.targetId, 'UI_OWNER_NOT_REMAPPED');
      const entry = sdk.parse(row.entry); check((entry ?? sdk.spend(row.entry))?.id === row.id, 'UI_NATIVE_PARSE');
      if (entry) accepted.push(entry);
    }
    for (const message of messages) for (const key of ['id', 'messageId', 'message_id']) if (typeof message[key] === 'string') sdkUserIds.add(message[key]);
    const allIds = new Set([...occupied, ...sdkUserIds]), nextIds = {}, afterUserNextIds = {};
    for (const kind of ['user-message', 'user-attachment', 'assistant-message', 'send-message']) {
      const id = sdk.nextId(accepted, kind, occupied); check(typeof id === 'string' && !allIds.has(id), 'NEXT_ID_COLLISION'); nextIds[kind] = id;
    }
    const afterUser = [...accepted, { kind: 'message', role: 'user', id: nextIds['user-message'] }];
    for (const kind of ['user-attachment', 'assistant-message', 'send-message']) {
      const id = sdk.nextId(afterUser, kind, occupied); check(typeof id === 'string' && !allIds.has(id) && id !== nextIds['user-message'], 'NEXT_ID_COLLISION'); afterUserNextIds[kind] = id;
    }
    const statements = sdk.prepare(sql), paging = [];
    for (const limit of [37, 500]) {
      const entries = []; let beforeSeq, pages = 0, prior = Infinity;
      do { const page = sdk.window(statements, { limit, beforeSeq }, () => ({})); entries.unshift(...page.entries); beforeSeq = page.nextBeforeSeq;
        check(beforeSeq === undefined || Number.isSafeInteger(beforeSeq) && beforeSeq < prior, 'PAGE_PROGRESS'); prior = beforeSeq; check(++pages <= 10000, 'PAGE_BOUND');
      } while (beforeSeq !== undefined);
      assert.deepEqual(entries, accepted); paging.push({ limit, pages, entries: entries.length, exact: true });
    }
    assert.deepEqual(snapshotGraph(blobs), beforeGraph); check(guard.counts.networkAttempts === 0 && guard.counts.toolAttempts === 0, 'SIDE_EFFECT');
    for (const db of opened.reverse()) db.close(); opened.length = 0;
    for (const [file, hash] of beforeFiles) { check(digest(fs.readFileSync(file)) === hash, 'SOURCE_BYTES_CHANGED'); checkDatabase(file); }
    check(digest(fs.readFileSync(options.planFile)) === planFileSha256, 'PLAN_BYTES_CHANGED');
    const reportPath = path.join(directory, 'current-local-sdk-report.json');
    const report = { schema: 'openbot-current-local-sdk-report/v1', sourceId: plan.sourceId, targetId: plan.targetId,
      helperSha256: digest(fs.readFileSync(fileURLToPath(import.meta.url))), planFileSha256,
      engineSha256: CODEC_SHA, workerSha256: WORKER_SHA,
      allocatorSha256: ALLOCATOR_SHA, executionHostSha256: digest(fs.readFileSync(options.allocatorHost)),
      rootSlot: ROOT_SLOT, rootSha256: digest(rootBytes), rootRoundtrip: true, currentRootByteExact: true,
      contextMessages: plain.length, derivedTurns: derived.turns.length, completeContextExact: true, noPendingTools: true,
      nativeReachableRefs: closure.blobTypeByHexId.size, unresolvedProtoRefs: 0, sourceArchives: archiveReports,
      structuralOwnerReferences: [], quotedHistoryUnchanged: true, literalOwnerReferences: literal.length,
      literalBlobHashes: [...literalBlobs].sort(), archivedOwnerReferenceBlobHashes: [...archivedHits].sort(),
      completedHistoricalToolProvenance: completedToolProvenance,
      graphUnchanged: true, allSourcesByteUnchanged: true, metadataOwnerVerified: true,
      capacity: { estimated: true, usedTokens, contextTokens: plan.selectedRoute.contextTokens, maxOutputTokens: plan.selectedRoute.maxOutputTokens, fits: true },
      ui: { records: rows.length, readable: accepted.length, paging }, nextIds, afterUserNextIds,
      liveApplied: false, sourceUpdated: false, providerCalls: 0, ...guard.counts };
    const reportRaw = Buffer.from(JSON.stringify(report) + '\n'); fs.writeFileSync(reportPath, reportRaw, { mode: 0o600, flag: 'wx' });
    const proof = { schema: 'openbot-sdk-merge-proof/v1', engineSha256: CODEC_SHA, rootSlot: ROOT_SLOT,
      rootSha256: digest(rootBytes), blobs: Object.fromEntries(beforeGraph), sourceRoots: plan.sourceRoots,
      nativeReport: { path: reportPath, sha256: digest(reportRaw) } };
    const proofPath = path.join(directory, 'current-local-sdk-proof.json'), raw = Buffer.from(JSON.stringify(proof) + '\n');
    fs.writeFileSync(proofPath, raw, { mode: 0o600, flag: 'wx' }); return { proof, report, proofPath, proofSha256: digest(raw) };
  } finally { for (const db of opened.reverse()) { try { db.close(); } catch {} } guard.restore(); }
}

async function main(args) {
  check(args.length === 10, 'CLI_ARGUMENTS'); const options = {};
  const names = { '--candidate-dir': 'candidateDir', '--plan-file': 'planFile', '--host': 'host', '--worker': 'worker', '--allocator-host': 'allocatorHost' };
  for (let index = 0; index < args.length; index += 2) { const key = names[args[index]]; check(key && !Object.hasOwn(options, key), 'CLI_ARGUMENTS'); options[key] = args[index + 1]; }
  boundedOwnedFile(options.planFile, 8 * 1024 ** 2, { privateFile: true });
  const result = await proveCurrentSdk(options, JSON.parse(fs.readFileSync(options.planFile)));
  process.stdout.write(JSON.stringify({ ok: true, proof: result.proofPath, sha256: result.proofSha256, sourceUpdated: false }) + '\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(() => process.exit(0), error => {
    const code = typeof error?.code === 'string' && /^[A-Z_]+$/.test(error.code) ? error.code : 'CURRENT_SDK_PROOF_FAILED';
    process.stderr.write(JSON.stringify({ ok: false, error: code, sourceUpdated: false }) + '\n'); process.exit(1);
  });
}
