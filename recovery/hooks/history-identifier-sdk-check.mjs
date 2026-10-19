/** Read-only, version-reviewed proof that publication aliases are not active SDK identifiers. */
import fs from 'node:fs';
import path from 'node:path';
import Module, { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

export const WORKER_SHA = 'e7d54d24d6ce6e82fb0c1b76d7572dbbb51b9437f958f04e6057b59e673a80fb';
export const BOUNDS = Object.freeze({ database: 128 * 1024 ** 2, worker: 128 * 1024 ** 2,
  idsFile: 1024 ** 2, ids: 1024, idBytes: 4096, blobs: 200000 });
const require = createRequire(import.meta.url);
const SLOT = Buffer.from('sand-live-conversation-root-v1__').toString('hex');
// Node 22's SQLite iterator does not keep its StatementSync alive itself.
// Keep a strong reference until iteration finishes, including inside callbacks
// which parse large graphs and can cause garbage collection.
const iteratingStatements = new Set();
const hex = value => Buffer.from(value).toString('hex');
export const digest = value => createHash('sha256').update(value).digest('hex');
function refuse(code) { const error = new Error(code); error.code = code; throw error; }
function check(condition, code) { if (!condition) refuse(code); }

export function validateIdentifiers(value) {
  check(Array.isArray(value) && value.length > 0 && value.length <= BOUNDS.ids, 'IDENTIFIER_LIST_BOUND');
  const ids = [];
  for (const id of value) {
    check(typeof id === 'string' && !/[\x00-\x1f\x7f]/.test(id) &&
      Buffer.byteLength(id) > 128 && Buffer.byteLength(id) <= BOUNDS.idBytes &&
      /^.+~openbot-branch-[0-9a-f]{64}~*$/.test(id), 'IDENTIFIER_SYNTHETIC_ALIAS');
    check(!ids.includes(id), 'IDENTIFIER_DUPLICATE'); ids.push(id);
  }
  return ids.sort();
}

function noSymlinks(value) {
  check(typeof value === 'string' && path.isAbsolute(value) && path.normalize(value) === value && !value.includes('\0'), 'PATH_ABSOLUTE');
  let current = path.parse(value).root;
  for (const part of value.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const stat = fs.lstatSync(current);
    check(!stat.isSymbolicLink(), 'PATH_SYMLINK');
    if (current !== value) check(stat.isDirectory(), 'PATH_PARENT');
  }
}
export function boundedOwnedFile(value, limit, { privateFile = false, allowRoot = false } = {}) {
  noSymlinks(value); const stat = fs.lstatSync(value), uid = process.getuid?.();
  check(stat.isFile() && stat.size > 0 && stat.size <= limit, 'PATH_FILE_BOUND');
  check(uid === undefined || stat.uid === uid || (allowRoot && stat.uid === 0), 'PATH_OWNER');
  check((stat.mode & (privateFile ? 0o077 : 0o022)) === 0, 'PATH_PERMISSIONS');
  return value;
}
export function privateOutput(value) {
  check(typeof value === 'string' && path.isAbsolute(value) && path.normalize(value) === value && path.extname(value) === '.json', 'OUTPUT_PATH');
  const parent = path.dirname(value); noSymlinks(parent);
  const stat = fs.lstatSync(parent);
  check(stat.isDirectory() && stat.uid === process.getuid?.() && (stat.mode & 0o077) === 0, 'OUTPUT_PRIVATE');
  check(!fs.existsSync(value), 'OUTPUT_EXISTS'); return value;
}

/** Pure scan: an alias anywhere except a literal string field is structural. */
export function scanIdentifierStrings(value, ids, prefix = 'root') {
  const structuralReferences = [], literalReferences = [];
  const literalKeys = new Set(['text', 'content', 'summary', 'reasoning']);
  let visited = 0;
  function visit(current, at, key, depth) {
    check(++visited <= 2000000 && depth <= 200, 'STRUCTURE_BOUND');
    if (typeof current === 'string') {
      const hits = ids.filter(id => current.includes(id));
      if (hits.length) {
        if (literalKeys.has(key)) literalReferences.push({ path: at, sha256: digest(current) });
        else for (const id of hits) structuralReferences.push({ path: at, id });
      }
      return;
    }
    if (!current || typeof current !== 'object' || current instanceof Uint8Array) return;
    if (Array.isArray(current)) {
      for (let index = 0; index < current.length; index++) visit(current[index], at + '[' + index + ']', key, depth + 1);
    } else for (const [childKey, child] of Object.entries(current)) {
      for (const id of ids.filter(alias => childKey.includes(alias))) structuralReferences.push({ path: at + '.<key>', id });
      visit(child, at + '.' + childKey, childKey, depth + 1);
    }
  }
  visit(value, prefix, '', 0); return { structuralReferences, literalReferences };
}

/** Unknown active binary locations may not be classified as harmless quoted text. */
export function classifyRawBlob({ id, data, activeIds, coreJsonIds, ids }) {
  const hits = ids.filter(alias => Buffer.from(data).includes(Buffer.from(alias)));
  if (!hits.length) return null;
  check(!activeIds.has(id) || coreJsonIds.has(id), 'ACTIVE_UNKNOWN_ALIAS_REFERENCE');
  if (activeIds.has(id)) {
    let json; try { json = JSON.parse(Buffer.from(data).toString('utf8')); } catch { refuse('ACTIVE_CORE_JSON_REQUIRED'); }
    const scan = scanIdentifierStrings(json, ids, 'core');
    check(scan.structuralReferences.length === 0, 'ACTIVE_STRUCTURAL_ALIAS_REFERENCE');
    check(scan.literalReferences.length > 0, 'ACTIVE_ALIAS_NOT_LITERAL');
  }
  return { id, sha256: digest(data), active: activeIds.has(id), matches: hits.length };
}

export function reviewedWorker(raw) {
  check(digest(raw) === WORKER_SHA, 'UNREVIEWED_WORKER');
  const source = raw.toString('utf8'), boundary = '\nmain();\n';
  check(source.split(boundary).length === 2, 'WORKER_BOOT_BOUNDARY');
  return source.replace(boundary, '\nmodule.exports={Root:ConversationStateStructure,Turn:ConversationTurnStructure,User:UserMessage,walk:collectReachableBlobHexIds};\n');
}

export function requireNonWalHeader(header) {
  check(header.length >= 100 && header.subarray(0, 16).equals(Buffer.from('SQLite format 3\0')) &&
    header[18] === 1 && header[19] === 1, 'SDK_NON_WAL_SNAPSHOT_REQUIRED');
}

function denySideEffects() {
  const restores = []; let networkAttempts = 0, toolAttempts = 0;
  function network() { networkAttempts++; refuse('NETWORK_DENIED'); }
  function tool() { toolAttempts++; refuse('TOOL_DENIED'); }
  function patch(target, key, value) {
    const old = Object.getOwnPropertyDescriptor(target, key);
    Object.defineProperty(target, key, { value, configurable: true, writable: true });
    restores.push(() => old ? Object.defineProperty(target, key, old) : delete target[key]);
  }
  patch(globalThis, 'fetch', network); patch(globalThis, 'WebSocket', network);
  for (const module of ['node:http', 'node:https']) for (const key of ['request', 'get']) patch(require(module), key, network);
  for (const key of ['connect', 'createConnection']) patch(require('node:net'), key, network);
  patch(require('node:net').Socket.prototype, 'connect', network);
  patch(require('node:tls'), 'connect', network); patch(require('node:http2'), 'connect', network);
  patch(require('node:dgram'), 'createSocket', network);
  for (const key of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) patch(require('node:child_process'), key, tool);
  patch(require('node:worker_threads'), 'Worker', tool); patch(require('node:cluster'), 'fork', tool);
  return { counts: () => ({ networkAttempts, toolAttempts }), restore() { for (const restore of restores.reverse()) restore(); } };
}

export function forEachSqliteRow(db, query, visit) {
  const statement = db.prepare(query); iteratingStatements.add(statement);
  try { for (const row of statement.iterate()) visit(row); }
  finally { iteratingStatements.delete(statement); }
}

function graphSnapshot(db) {
  const graph = new Map();
  forEachSqliteRow(db, 'SELECT id,data FROM blobs ORDER BY id', row => {
    check(typeof row.id === 'string' && row.data instanceof Uint8Array && !graph.has(row.id), 'SDK_BLOB_SCHEMA');
    graph.set(row.id, digest(row.data)); check(graph.size <= BOUNDS.blobs, 'SDK_BLOB_BOUND');
  });
  return graph;
}
const graphSHA = graph => digest(JSON.stringify([...graph]));

/** Read-only audit, factored so the truly empty room store has a small fixture. */
export function auditDatabase(db, sdk, ids) {
  ids = validateIdentifiers(ids);
  assert.equal(db.prepare('PRAGMA quick_check').get().quick_check, 'ok');
  const before = graphSnapshot(db);
  const get = id => db.prepare('SELECT data FROM blobs WHERE id=?').get(id)?.data;
  const rootBytes = get(SLOT);
  if (!rootBytes) {
    check(before.size === 0, 'SDK_ROOT_MISSING_WITH_BLOBS');
    assert.deepEqual(graphSnapshot(db), before);
    return { rootSha256: null, rootRoundtrip: false, rootAbsent: true, checkedIds: ids,
      structuralReferences: [], literalBlobHashes: [], archivedBlobHashes: [], nativeReachableRefs: 0,
      unresolvedProtoRefs: 0, graphSha256: graphSHA(before), graphBlobs: 0, graphUnchanged: true };
  }
  check(sdk && typeof sdk.walk === 'function', 'SDK_CODEC_REQUIRED');
  const root = sdk.Root.fromBinary(rootBytes);
  assert.ok(Buffer.from(root.toBinary()).equals(Buffer.from(rootBytes)), 'Native root roundtrip');
  const structural = [], literal = [];
  function scan(value, prefix) {
    const found = scanIdentifierStrings(value, ids, prefix);
    structural.push(...found.structuralReferences); literal.push(...found.literalReferences);
  }
  scan(root, 'root');
  for (let index = 0; index < root.turns.length; index++) {
    const body = get(hex(root.turns[index])); check(body, 'MISSING_SDK_TURN');
    const turn = sdk.Turn.fromBinary(body); scan(turn, 'turns[' + index + ']');
    if (turn.turn.case === 'agentConversationTurn') {
      const user = get(hex(turn.turn.value.userMessage)); check(user, 'MISSING_SDK_USER');
      scan(sdk.User.fromBinary(user), 'users[' + index + ']');
    }
  }
  const coreJsonIds = new Set(root.rootPromptMessagesJson.map(hex)), literalBlobHashes = new Set();
  for (const id of coreJsonIds) {
    const body = get(id); check(body, 'MISSING_CORE_JSON');
    let core; try { core = JSON.parse(Buffer.from(body).toString('utf8')); } catch { refuse('CORE_JSON_INVALID'); }
    const found = scanIdentifierStrings(core, ids, 'core');
    structural.push(...found.structuralReferences); literal.push(...found.literalReferences);
    if (found.literalReferences.length) literalBlobHashes.add(digest(body));
  }
  check(structural.length === 0, 'ACTIVE_STRUCTURAL_ALIAS_REFERENCE');
  const closure = sdk.walk({ rootBytes, getBlobByHexId: get,
    onBlobReference: id => check(get(id), 'MISSING_SDK_REFERENCE') });
  check(closure.unresolvedProtoRefs === 0, 'UNRESOLVED_SDK_GRAPH');
  const activeIds = new Set([SLOT, ...closure.blobTypeByHexId.keys()]), archivedBlobHashes = new Set();
  forEachSqliteRow(db, 'SELECT id,data FROM blobs ORDER BY id', row => {
    const hit = classifyRawBlob({ id: row.id, data: row.data, activeIds, coreJsonIds, ids });
    if (hit) (hit.active ? literalBlobHashes : archivedBlobHashes).add(hit.sha256);
  });
  const after = graphSnapshot(db); assert.deepEqual(after, before, 'Every SDK blob remains byte-identical');
  return { rootSha256: digest(rootBytes), rootRoundtrip: true, rootAbsent: false, checkedIds: ids,
    structuralReferences: [], literalBlobHashes: [...literalBlobHashes].sort(), archivedBlobHashes: [...archivedBlobHashes].sort(),
    literalStringReferences: literal.length, nativeReachableRefs: closure.blobTypeByHexId.size,
    unresolvedProtoRefs: 0, graphSha256: graphSHA(before), graphBlobs: before.size, graphUnchanged: true };
}

export function checkSdkReferences(options) {
  boundedOwnedFile(options.worker, BOUNDS.worker, { allowRoot: true });
  boundedOwnedFile(options.blobsDb, BOUNDS.database);
  boundedOwnedFile(options.idsFile, BOUNDS.idsFile, { privateFile: true }); privateOutput(options.output);
  const workerRaw = fs.readFileSync(options.worker), source = reviewedWorker(workerRaw);
  for (const suffix of ['-wal', '-shm', '-journal']) check(!fs.existsSync(options.blobsDb + suffix), 'SDK_SIDECAR_PRESENT');
  // Read-only SQLite can create WAL/SHM files for a WAL-mode database. Require
  // an independent snapshot normalized to DELETE mode by the caller instead.
  // The codec proof checks blob bytes; this checker never rewrites a DB header.
  const header = fs.readFileSync(options.blobsDb).subarray(0, 100);
  requireNonWalHeader(header);
  const ids = validateIdentifiers(JSON.parse(fs.readFileSync(options.idsFile, 'utf8')));
  const dbBefore = digest(fs.readFileSync(options.blobsDb)), guard = denySideEffects();
  let db;
  try {
    const { DatabaseSync } = require('node:sqlite');
    db = new DatabaseSync(options.blobsDb, { readOnly: true });
    const mod = new Module(options.worker); mod.filename = options.worker;
    mod.paths = Module._nodeModulePaths(path.dirname(options.worker)); mod._compile(source, options.worker);
    const proof = { schema: 'openbot-identifier-sdk-reference-proof/v1', workerSha256: digest(workerRaw),
      ...auditDatabase(db, mod.exports, ids), liveWrites: 0, sourceUpdated: false, providerCalls: 0, ...guard.counts() };
    check(proof.networkAttempts === 0 && proof.toolAttempts === 0, 'SDK_SIDE_EFFECT');
    db.close(); db = undefined;
    check(digest(fs.readFileSync(options.blobsDb)) === dbBefore, 'SDK_DATABASE_CHANGED');
    for (const suffix of ['-wal', '-shm', '-journal']) check(!fs.existsSync(options.blobsDb + suffix), 'SDK_SIDECAR_CREATED');
    proof.blobsDbSha256 = dbBefore;
    fs.writeFileSync(options.output, JSON.stringify(proof) + '\n', { mode: 0o600, flag: 'wx' });
    return proof;
  } finally { db?.close(); guard.restore(); }
}

function main(args) {
  check(args.length === 8, 'CLI_ARGUMENTS'); const options = {};
  const names = { '--worker': 'worker', '--blobs-db': 'blobsDb', '--ids-file': 'idsFile', '--output': 'output' };
  for (let index = 0; index < args.length; index += 2) {
    const key = names[args[index]]; check(key && !Object.hasOwn(options, key), 'CLI_ARGUMENTS'); options[key] = args[index + 1];
  }
  const proof = checkSdkReferences(options);
  process.stdout.write(JSON.stringify({ ok: true, proof: options.output, rootSha256: proof.rootSha256,
    checkedIds: proof.checkedIds.length, structuralReferences: 0, sourceUpdated: false }) + '\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); process.exit(0); } catch (error) {
    const code = typeof error?.code === 'string' && /^[A-Z_]+$/.test(error.code) ? error.code : 'SDK_REFERENCE_CHECK_FAILED';
    process.stderr.write(JSON.stringify({ ok: false, error: code, sourceUpdated: false }) + '\n'); process.exit(1);
  }
}
