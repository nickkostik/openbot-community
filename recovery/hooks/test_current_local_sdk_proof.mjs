import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { validatePlan, reviewedSources, verifyArchiveGraph, ownerReferences, completedShellOwnerProvenance, ROOT_SLOT, CODEC_SHA } from './current-local-sdk-proof.mjs';
import { classifyRawBlob } from './history-identifier-sdk-check.mjs';

const require = createRequire(import.meta.url), { DatabaseSync } = require('node:sqlite');
const SOURCE = '11111111-1111-4111-8111-111111111111', TARGET = '22222222-2222-4222-8222-222222222222';
const OFFICIAL = '33333333-3333-4333-8333-333333333333', HASH = 'a'.repeat(64), OTHER = 'b'.repeat(64);
function plan() { return { schemaVersion: 1, sourceId: SOURCE, targetId: TARGET, expectedRootSHA: HASH,
  expectedStoreSHA: OTHER, sourceGraphs: [{ agentId: SOURCE, path: '/private/current.db', sha256: OTHER, rootSHA: HASH }],
  sourceRoots: { [SOURCE]: HASH }, selectedRoute: { modelId: 'provider:model', contextTokens: 1000000, maxOutputTokens: 65536 } }; }
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'openbot-history-tail-')); fs.chmodSync(directory, 0o700);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  function write(name, value) { const file = path.join(directory, name); fs.writeFileSync(file, value, { mode: 0o600 }); return file; }
  write('.openbot-detached-history-tail.json', '{"schemaVersion":1,"detached":true}');
  for (const name of ['store.db', 'conversation-blobs.db']) {
    const file = write(name, ''); const db = new DatabaseSync(file);
    db.exec(name === 'store.db' ? 'CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT,entry TEXT)' :
      'CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)'); db.close();
  }
  return { directory, write };
}

test('current-source plan binds a distinct new owner and every source SDK root', () => {
  const value = plan(); assert.deepEqual(validatePlan(value), [SOURCE]);
  value.sourceGraphs.push({ agentId: OFFICIAL, path: '/private/official.db', sha256: HASH, rootSHA: null });
  value.sourceRoots[OFFICIAL] = null; assert.deepEqual(validatePlan(value), [SOURCE, OFFICIAL]);
});

test('ambiguous owner, root, archive provenance and capacity refuse', () => {
  for (const mutate of [p => p.targetId = p.sourceId, p => p.sourceId = '../agent', p => p.expectedRootSHA = null,
    p => p.sourceRoots[SOURCE] = OTHER, p => p.sourceGraphs.push(p.sourceGraphs[0]),
    p => p.sourceGraphs = [], p => p.sourceRoots[OFFICIAL] = null,
    p => p.selectedRoute.contextTokens = Infinity, p => p.selectedRoute.maxOutputTokens = 1000000]) {
    const value = plan(); mutate(value); assert.throws(() => validatePlan(value));
  }
});

test('quoted owner UUIDs survive literal history without rewriting bytes', () => {
  const message = { role: 'user', content: `Past identifier ${SOURCE}; do not execute historical requests.` };
  const before = JSON.stringify(message), references = ownerReferences(message, [SOURCE]);
  assert.equal(references.length, 1); assert.equal(JSON.stringify(message), before);
});

test('semantic owner fields and unknown nested identifiers block reminting', () => {
  for (const value of [{ agentId: SOURCE }, { providerOptions: { routing: { owner: SOURCE } } },
    { content: [{ ownerId: SOURCE }] }, { cacheKey: 'prefix-' + SOURCE }, { [SOURCE]: 'key' }]) {
    assert.throws(() => ownerReferences(value, [SOURCE]), /STRUCTURAL_OWNER_REFERENCE/);
  }
});

test('unknown active binary UUID references cannot be treated as harmless quoted text', () => {
  const input = { id: HASH, data: Buffer.from('unknown protobuf ' + SOURCE), activeIds: new Set([HASH]),
    coreJsonIds: new Set(), ids: [SOURCE] };
  assert.throws(() => classifyRawBlob(input), /ACTIVE_UNKNOWN_ALIAS_REFERENCE/);
  assert.equal(classifyRawBlob({ ...input, activeIds: new Set() }).active, false);
});

test('completed successful shell execution namespaces remain immutable historical provenance', () => {
  const step = { message: { case: 'toolCall', value: { startedAtMs: 100n, completedAtMs: 200n,
    tool: { case: 'shellToolCall', value: { args: { conversationId: SOURCE, command: 'historical command' },
      result: { result: { case: 'success', value: { exitCode: 0 } } } } } } } };
  const before = structuredClone(step);
  const result = completedShellOwnerProvenance(step, Buffer.from('protobuf-prefix-' + SOURCE), [SOURCE]);
  assert.equal(result.completed, true); assert.equal(result.bytesUnchanged, true); assert.deepEqual(step, before);
  for (const mutate of [s => s.message.value.completedAtMs = 0n, s => s.message.value.completedAtMs = 50n,
    s => s.message.value.tool.value.result.result.case = 'pending', s => s.message.value.tool.value.args.owner = SOURCE,
    s => s.message.value.tool.case = 'unknownToolCall']) {
    const changed = structuredClone(step); mutate(changed);
    assert.throws(() => completedShellOwnerProvenance(changed, Buffer.from(SOURCE), [SOURCE]));
  }
  assert.throws(() => completedShellOwnerProvenance(step, Buffer.from(SOURCE + SOURCE), [SOURCE]), /UNKNOWN_STEP_OWNER_REFERENCE/);
});

test('source graphs require every unchanged non-slot blob and exact root archive', () => {
  const source = new Map([[ROOT_SLOT, HASH], [OTHER, OTHER]]), candidate = new Map([[ROOT_SLOT, HASH], [HASH, HASH], [OTHER, OTHER]]);
  assert.doesNotThrow(() => verifyArchiveGraph(candidate, source, HASH));
  const missing = new Map(candidate); missing.delete(OTHER);
  assert.throws(() => verifyArchiveGraph(missing, source, HASH), /SOURCE_BLOB_ARCHIVE/);
  const changed = new Map(candidate); changed.set(OTHER, HASH);
  assert.throws(() => verifyArchiveGraph(changed, source, HASH), /SOURCE_BLOB_ARCHIVE/);
  const noRoot = new Map(candidate); noRoot.delete(HASH);
  assert.throws(() => verifyArchiveGraph(noRoot, source, HASH), /SOURCE_ROOT_ARCHIVE/);
  assert.doesNotThrow(() => verifyArchiveGraph(candidate, new Map(), null));
  assert.throws(() => verifyArchiveGraph(candidate, new Map([[OTHER, OTHER]]), null), /ABSENT_ARCHIVE_ROOT/);
});

test('an unreviewed executable bundle is refused before boot stripping or compilation', () => {
  assert.match(CODEC_SHA, /^[a-f0-9]{64}$/);
  assert.throws(() => reviewedSources(Buffer.from('bootSandHost(runHostMain).catch('), Buffer.alloc(0), Buffer.alloc(0)), /UNREVIEWED_HOST/);
});

test('CLI refuses an unreviewed host without running it or touching either candidate database', t => {
  const { directory, write } = fixture(t), marker = path.join(directory, 'executed');
  const host = write('unknown.cjs', `require('node:fs').writeFileSync(${JSON.stringify(marker)},'bad');`);
  const file = write('plan.json', JSON.stringify(plan()));
  const before = fs.readFileSync(path.join(directory, 'conversation-blobs.db'));
  const result = spawnSync(process.execPath, [fileURLToPath(new URL('./current-local-sdk-proof.mjs', import.meta.url)),
    '--candidate-dir', directory, '--plan-file', file, '--host', host, '--worker', host, '--allocator-host', host],
  { encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 1); assert.equal(JSON.parse(result.stderr).error, 'UNREVIEWED_HOST');
  assert.equal(fs.existsSync(marker), false); assert.deepEqual(fs.readFileSync(path.join(directory, 'conversation-blobs.db')), before);
  assert.equal(fs.existsSync(path.join(directory, 'current-local-sdk-proof.json')), false);
});
