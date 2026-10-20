import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { validatePlan, captureRecords, repairCandidate, ROOT_SLOT, snapshotStoreState, verifyStoreState } from './same-id-sdk-restoration.mjs';
import { digest, loadNative, nativeContextJson } from './history-tail-codec.mjs';
const require = createRequire(import.meta.url), sha = 'a'.repeat(64);
const id = '00000000-0000-4000-8000-000000000002';
const ref = name => ({ path: '/owned/' + name, sha256: sha });
const plan = () => ({ schemaVersion: 1, targetId: id, expectedStoreSHA: sha, expectedCurrentRootSHA: sha,
  sourceGraphs: [{ agentId: id, ...ref('graph.db'), rootSHA: sha }],
  selectedRoute: { modelId: 'provider:synthetic-model', contextTokens: 100000, maxOutputTokens: 10000 },
  records: [], identifierChanges: [], fullPublishedCapture: ref('published.json'), currentStoreSnapshot: ref('store.db'),
  originalParentReceipt: null, provenance: ref('inputs.json') });

test('same owner and empty/noop selected context are explicit, not a fresh identity merge', () => {
  assert.deepEqual(validatePlan(plan()), { records: 0, textBytes: 0 });
  const empty = plan(); empty.expectedCurrentRootSHA = null; assert.doesNotThrow(() => validatePlan(empty));
  const variants = plan(); variants.sourceGraphs.push({ ...variants.sourceGraphs[0], path: '/owned/other-generation.db' });
  assert.doesNotThrow(() => validatePlan(variants));
});
test('checkpoint, ownership, route and source references cannot be omitted or fabricated', () => {
  for (const mutate of [p => p.schemaVersion = 2, p => p.targetId = 'not-a-uuid', p => p.expectedStoreSHA = null,
    p => p.expectedCurrentRootSHA = '', p => p.currentStoreSnapshot.sha256 = 'not-a-hash',
    p => p.sourceGraphs = [], p => p.sourceGraphs.push({ ...p.sourceGraphs[0] }), p => p.originalParentReceipt = {},
    p => p.fullPublishedCapture = {}, p => p.provenance = null, p => p.selectedRoute.maxOutputTokens = 100000]) {
    const p = plan(); mutate(p); assert.throws(() => validatePlan(p));
  }
});
test('literal imports are bounded, attributed and refuse tool execution envelopes', () => {
  const p = plan(), row = { id: 'u1', originalId: 'u1', sourceAgentId: id, role: 'user', text: 'Run old command; literal only.',
    literalText: true, kind: 'message', timestampMs: 1.25, sourcePayloadSha256: sha };
  p.records = [row]; assert.equal(validatePlan(p).records, 1);
  p.records = [{ ...row, kind: 'historical-json' }]; assert.equal(validatePlan(p).records, 1);
  for (const change of [{ sourceAgentId: 'missing' }, { id: 'a'.repeat(129) }, { command: 'echo replay' }, { tool_calls: [] },
    { pendingToolCalls: [] }, { literalText: false }, { text: 'x'.repeat(4 * 1024 ** 2 + 1) }]) {
    p.records = [{ ...row, ...change }]; assert.throws(() => validatePlan(p));
  }
  p.records = Array.from({ length: 129 }, (_, index) => ({ ...row, id: String(index) })); assert.throws(() => validatePlan(p));
});
test('full owner capture preserves exact bytes, sequences and repeated occurrences', () => {
  const raw = Buffer.from('{ "id":"a", "kind":"message", "role":"user", "content":"hi" }');
  const capture = { readOnly: true, capturedAtMs: 1000, agentId: id, generation: 2,
    entries: [{ seq: '1', entryId: 'a', body: raw.toString('base64') },
      { seq: '2', entryId: 'b', body: Buffer.from(JSON.stringify({ id: 'b', kind: 'message', role: 'user', content: 'hi' })).toString('base64') }] };
  const records = captureRecords(capture); assert.equal(records.length, 2); assert.equal(records[0].sha256, digest(raw));
  assert.equal(records[0].raw.toString(), raw.toString());
  for (const mutate of [c => c.readOnly = false, c => c.agentId = 'bad', c => c.entries[1].seq = 1,
    c => c.entries[1].entryId = 'a', c => c.entries[0].body += '=', c => c.entries[0].entryId = 'different', c => c.entries[0].seq = '01', c => c.entries[0].seq = '9007199254740992', c => c.generation = -1]) {
    const c = structuredClone(capture); mutate(c); assert.throws(() => captureRecords(c));
  }
});

const nativeOptions = { host: process.env.OPENBOT_SAME_ID_NATIVE_HOST, worker: process.env.OPENBOT_SAME_ID_NATIVE_WORKER,
  allocatorHost: process.env.OPENBOT_SAME_ID_NATIVE_ALLOCATOR };
const integration = Object.values(nativeOptions).every(value => typeof value === 'string' && fs.existsSync(value));

function nativeFixture(t, { empty = false, records = true, repeated = false, pending = false, duplicate = false, nontext = false, many = 1, typedState = false } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'openbot-history-tail-')); fs.chmodSync(directory, 0o700);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const write = (name, data) => { const filename = path.join(directory, name); fs.writeFileSync(filename, data, { mode: 0o600 }); return filename; };
  const json = (name, value) => { const filename = write(name, JSON.stringify(value)); return { path: filename, sha256: digest(fs.readFileSync(filename)) }; };
  write('.openbot-detached-history-tail.json', JSON.stringify({ schemaVersion: 1, detached: true }));
  const native = loadNative(nativeOptions.host, nativeOptions.worker, nativeOptions.allocatorHost);
  const sdk = native.sdk, root = new sdk.Root(), graph = new Map();
  try {
    if (!empty) {
      const current = { role: 'user', content: 'A current conversation that already advanced after its older archive.' };
      const body = Buffer.from(JSON.stringify(current)), hash = digest(body); graph.set(hash, body); root.rootPromptMessagesJson.push(Buffer.from(hash, 'hex'));
      const user = new sdk.User({ text: 'A completed native user turn.', messageId: 't900u' });
      const userRaw = Buffer.from(user.toBinary()), userHash = digest(userRaw); graph.set(userHash, userRaw);
      const turn = new sdk.Turn({ turn: { case: 'agentConversationTurn', value: { userMessage: Buffer.from(userHash, 'hex'), steps: [], requestId: 'synthetic-completed-request' } } });
      const turnRaw = Buffer.from(turn.toBinary()), turnHash = digest(turnRaw); graph.set(turnHash, turnRaw); root.turns.push(Buffer.from(turnHash, 'hex'));
      root.previousWorkspaceUris = ['file:///synthetic/workspace']; root.selfSummaryCount = 7; root.readPaths = ['synthetic-preserved.txt'];
      root.activeBranchName = 'synthetic-preserved-branch'; root.durableSkillBlocks = ['Preserve this completed context instruction.'];
      root.completedAskQuestionToolCallIds = ['completed-question-not-pending']; root.recentUserMessageIds = ['t900u'];
      if (pending) root.pendingToolCalls = ['synthetic-pending-tool-that-must-not-replay'];
      root.tokenDetails = new sdk.Token({ usedTokens: 17, maxTokens: 100000 });
      graph.set(ROOT_SLOT, Buffer.from(root.toBinary()));
    }
    const { DatabaseSync } = require('node:sqlite'), store = path.join(directory, 'store.db'), blobs = path.join(directory, 'conversation-blobs.db');
    const sql = new DatabaseSync(store);
    sql.exec('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL);CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,entry TEXT);CREATE TABLE automation_completion_inbox(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,text TEXT,attribution TEXT,acknowledged INTEGER DEFAULT 0);');
    const metadata = { agentId: id, latestRootBlobId: empty ? '' : ROOT_SLOT };
    sql.prepare('INSERT INTO kv VALUES(?,?)').run('metadata', Buffer.from(JSON.stringify(metadata)).toString('hex'));
    sql.prepare('INSERT INTO kv VALUES(?,?)').run('unreadState', JSON.stringify({ lastActivityAt: 13, lastUnreadActivityAt: 12, lastViewedAt: 11, unreadCount: 2, isManuallyUnread: true }));
    sql.prepare('INSERT INTO kv VALUES(?,?)').run('unrelated-private-setting', 'preserve-me');
    sql.prepare('INSERT INTO blobs VALUES(?,?)').run('legacy-store-blob', Buffer.from([0, 255, 128, 17]));
    sql.prepare('INSERT INTO automation_completion_inbox VALUES(?,?,?,?,?)').run(1, 'retained-completion', 'A finished tool result awaiting acknowledgment.', 'synthetic-source', 0);
    if (typedState) {
      sql.exec('CREATE TABLE typed_duplicates(i INTEGER,r REAL,t TEXT,b BLOB,n);CREATE TABLE typed_without_rowid(k TEXT PRIMARY KEY,v BLOB) WITHOUT ROWID;');
      for (let index = 0; index < 2; index++) sql.prepare('INSERT INTO typed_duplicates(rowid,i,r,t,b,n)VALUES(?,?,?,?,?,?)').run(index ? 9 : 3, 9007199254740993n, 1.5, 'Preserved 🦊 text', Buffer.from([0, 255]), null);
      sql.prepare('INSERT INTO typed_without_rowid VALUES(?,?)').run('retained', Buffer.from([0, 1, 2]));
    }
    const events = records ? [{ kind: 'message', id: 't1u', role: 'user', content: duplicate ? 'A current conversation that already advanced after its older archive.' : 'An absent old question.', isStreaming: false, timestampMs: 10.125 }] : [];
    for (let index = 1; index < many; index++) events.push({ ...events[0], id: 't' + (index + 1) + 'u', content: 'Historical occurrence ' + index });
    if (repeated) events.push({ ...events[0], id: 't2u' });
    if (nontext) events.push({ kind: 'notice', id: 'historical-notice-1', text: 'A past tool consent notification, not a new action.', timestampMs: 11.5, payload: { command: 'echo historical-only' } });
    for (let index = 0; index < events.length; index++) sql.prepare('INSERT INTO transcript_entries VALUES(?,?,?)').run(index + 1, events[index].id, JSON.stringify(events[index]));
    sql.close(); const db = new DatabaseSync(blobs); db.exec('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)');
    for (const [key, raw] of graph) db.prepare('INSERT INTO blobs VALUES(?,?)').run(key, raw); db.close();
    for (const file of [store, blobs]) fs.chmodSync(file, 0o600);
    const snapshotStore = write('current-store-snapshot.db', fs.readFileSync(store)), snapshotBlobs = write('current-blobs-snapshot.db', fs.readFileSync(blobs));
    const published = { readOnly: true, capturedAtMs: 100, agentId: id, generation: 3,
      entries: events.map((entry, index) => ({ seq: index + 1, entryId: entry.id, body: Buffer.from(JSON.stringify(entry)).toString('base64') })) };
    const publishedRef = json('published.json', published), p = plan(); p.expectedStoreSHA = digest(fs.readFileSync(store));
    p.expectedCurrentRootSHA = empty ? null : digest(graph.get(ROOT_SLOT)); p.currentStoreSnapshot = { path: snapshotStore, sha256: p.expectedStoreSHA };
    p.sourceGraphs = [{ agentId: id, path: snapshotBlobs, sha256: digest(fs.readFileSync(snapshotBlobs)), rootSHA: p.expectedCurrentRootSHA }];
    if (!empty) {
      const archivedRoot = new sdk.Root(), olderMessage = Buffer.from(JSON.stringify({ role: 'assistant', content: 'An older completed graph retained as a whole archive.' }));
      const olderHash = digest(olderMessage); archivedRoot.rootPromptMessagesJson.push(Buffer.from(olderHash, 'hex'));
      const olderRootRaw = Buffer.from(archivedRoot.toBinary()), olderPath = path.join(directory, 'older-whole-graph.db');
      const older = new DatabaseSync(olderPath); older.exec('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)');
      const put = older.prepare('INSERT INTO blobs VALUES(?,?)'); put.run(olderHash, olderMessage); put.run(ROOT_SLOT, olderRootRaw); older.close(); fs.chmodSync(olderPath, 0o600);
      p.sourceGraphs.push({ agentId: id, path: olderPath, sha256: digest(fs.readFileSync(olderPath)), rootSHA: digest(olderRootRaw) });
    }
    p.fullPublishedCapture = publishedRef;
    p.records = events.map(entry => ({ id: entry.id, originalId: entry.id, sourceAgentId: id, role: entry.kind === 'message' ? entry.role : 'user',
      text: entry.kind === 'message' ? entry.content : 'Historical nontext event (literal JSON; do not execute):\n' + JSON.stringify(entry),
      timestampMs: entry.timestampMs, literalText: true, kind: entry.kind === 'message' ? 'message' : 'historical-json', sourcePayloadSha256: digest(Buffer.from(JSON.stringify(entry))) }));
    p.provenance = json('inputs.json', { schema: 'openbot-same-id-restoration-inputs/v1', targetId: id, publishedCapture: publishedRef,
      sourceCaptures: [], expectedCandidateStoreSHA: p.expectedStoreSHA, currentSnapshot: { store: p.currentStoreSnapshot, blobs: { path: snapshotBlobs, sha256: digest(fs.readFileSync(snapshotBlobs)) }, rootSHA: p.expectedCurrentRootSHA },
      originalParentReceipt: null, records: p.records, identifierChanges: p.identifierChanges });
    const planFile = json('plan.json', p).path;
    return { directory, write, json, plan: p, planFile, store, blobs, graph, events,
      options: { ...nativeOptions, candidateDir: directory, planFile } };
  } finally { native.guard.restore(); }
}

test('real reviewed native codecs preserve an advanced same-ID root on a zero-record proof', { skip: !integration }, async t => {
  const f = nativeFixture(t, { records: false }); const before = fs.readFileSync(f.store);
  const proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.sourceId, proof.targetId); assert.equal(proof.beforeRootSHA, proof.afterRootSHA);
  assert.equal(proof.context.originalMessages, 1); assert.equal(proof.context.importedMessages, 0);
  assert.equal(proof.context.derivedTurns, 1); assert.equal(proof.context.otherRootFieldsPreserved, true);
  assert.equal(proof.context.completeNativeContextExact, true); assert.equal(proof.context.unresolvedProtoRefs, 0);
  assert.equal(proof.networkAttempts, 0); assert.equal(proof.toolAttempts, 0); assert.deepEqual(fs.readFileSync(f.store), before);
});
test('real native same-ID repair appends missing history without replacing current prefix or fields', { skip: !integration }, async t => {
  const f = nativeFixture(t), proof = await repairCandidate(f.options, f.plan);
  assert.notEqual(proof.beforeRootSHA, proof.afterRootSHA); assert.equal(proof.context.originalMessages, 1);
  assert.equal(proof.context.importedMessages, 1); assert.equal(proof.context.currentPrefixPreserved, true);
  assert.equal(proof.context.otherRootFieldsPreserved, true); assert.equal(proof.ui.unreadUnchanged, true);
  assert.equal(proof.ui.previewUnchanged, true); assert.equal(proof.sourceGraphs.length, 2); assert.ok(proof.sourceGraphs.every(graph => graph.wholeGraphArchived));
  assert.equal(proof.blobs[proof.beforeRootSHA], proof.beforeRootSHA);
  assert.equal(proof.networkAttempts, 0); assert.equal(proof.toolAttempts, 0);
});
test('real native empty bootstrap preserves repeated events as distinct occurrences', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, repeated: true }), proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.beforeRootSHA, null); assert.equal(proof.context.importedMessages, 2);
  assert.equal(proof.context.derivedTurns, 0); assert.equal(proof.ui.metadataRootInitialized, true);
  assert.equal(proof.ui.records, 2); assert.equal(proof.context.messages, 3);
});
test('real native guards refuse changed roots, omitted published history and forged literal source bytes', { skip: !integration }, async t => {
  for (const [change, expected] of [
    [f => f.plan.expectedCurrentRootSHA = 'b'.repeat(64), /PROVENANCE_BINDING/],
    [f => f.plan.records[0].sourcePayloadSha256 = 'b'.repeat(64), /PROVENANCE_BINDING/],
  ]) {
    const f = nativeFixture(t); change(f); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
    await assert.rejects(repairCandidate(f.options, f.plan), expected);
    assert.equal(fs.existsSync(path.join(f.directory, 'same-id-sdk-restoration-proof.json')), false);
  }
});

test('real native refuses pending actions, duplicated selected history and insufficient capacity without candidate writes', { skip: !integration }, async t => {
  for (const [settings, mutate, expected] of [
    [{ pending: true }, () => {}, /PENDING_TOOLS/],
    [{ duplicate: true }, () => {}, /LITERAL_ALREADY_SELECTED/],
    [{}, f => { f.plan.selectedRoute.contextTokens = 128; f.plan.selectedRoute.maxOutputTokens = 64; }, /CONTEXT_CAPACITY/],
  ]) {
    const f = nativeFixture(t, settings), before = [f.store, f.blobs].map(file => digest(fs.readFileSync(file)));
    mutate(f); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
    await assert.rejects(repairCandidate(f.options, f.plan), expected);
    assert.deepEqual([f.store, f.blobs].map(file => digest(fs.readFileSync(file))), before);
  }
});
test('real native empty bootstrap refuses a truncated full capture and proof reuse before any write', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, repeated: true }); f.plan.records.pop();
  const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path)); inputs.records = f.plan.records;
  f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
  const before = [f.store, f.blobs].map(file => digest(fs.readFileSync(file)));
  await assert.rejects(repairCandidate(f.options, f.plan), /BOOTSTRAP_FULL_CAPTURE_REQUIRED/);
  assert.deepEqual([f.store, f.blobs].map(file => digest(fs.readFileSync(file))), before);
  f.write('same-id-sdk-restoration-proof.json', '{}');
  await assert.rejects(repairCandidate(f.options, f.plan), /RESTORATION_PROOF_EXISTS/);
  assert.deepEqual([f.store, f.blobs].map(file => digest(fs.readFileSync(file))), before);
});

test('real native UI union may add history while preserving original current rows and KV exactly', { skip: !integration }, async t => {
  const f = nativeFixture(t, { repeated: true }), { DatabaseSync } = require('node:sqlite');
  const source = new DatabaseSync(f.plan.currentStoreSnapshot.path); source.prepare('DELETE FROM transcript_entries WHERE id=?').run('t2u'); source.close();
  f.plan.currentStoreSnapshot.sha256 = digest(fs.readFileSync(f.plan.currentStoreSnapshot.path));
  const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path)); inputs.currentSnapshot.store = f.plan.currentStoreSnapshot;
  f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
  assert.notEqual(f.plan.currentStoreSnapshot.sha256, f.plan.expectedStoreSHA);
  const proof = await repairCandidate(f.options, f.plan); assert.equal(proof.ui.originalCurrentRecords, 1);
  assert.equal(proof.ui.records, 2); assert.equal(proof.ui.currentRowsByteExact, true); assert.equal(proof.ui.currentOrderPreserved, true);
});
test('real native UI union cannot silently reset unread state or mutate current message bytes', { skip: !integration }, async t => {
  for (const operation of [db => db.prepare("UPDATE kv SET value='{}' WHERE key='unreadState'").run(),
    db => db.prepare("UPDATE transcript_entries SET entry=replace(entry,'An absent old question.','Changed current question.')").run()]) {
    const f = nativeFixture(t), { DatabaseSync } = require('node:sqlite'), db = new DatabaseSync(f.store); operation(db); db.close();
    f.plan.expectedStoreSHA = digest(fs.readFileSync(f.store));
    const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path)); inputs.expectedCandidateStoreSHA = f.plan.expectedStoreSHA;
    f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
    await assert.rejects(repairCandidate(f.options, f.plan), /retain all current KV|CURRENT_UI_CHANGED/);
  }
});

test('identifier repair plans accept only explicitly listed oversized synthetic aliases', () => {
  const p = plan(), alias = 'a'.repeat(90) + '~openbot-branch-' + sha;
  p.identifierChanges = [{ oldId: alias, newId: 'openbot-branch-' + digest(alias) }]; assert.doesNotThrow(() => validatePlan(p));
  for (const value of [[{ oldId: 'ordinary-id', newId: 'openbot-branch-' + sha }],
    [{ oldId: alias, newId: 'arbitrary-short-id' }], [{ ...p.identifierChanges[0], extra: true }],
    [p.identifierChanges[0], p.identifierChanges[0]]]) { p.identifierChanges = value; assert.throws(() => validatePlan(p)); }
});
function identifierFixture(t, { wrongAllocation = false } = {}) {
  const f = nativeFixture(t), { DatabaseSync } = require('node:sqlite');
  const oldId = 'original-message-identifier-'.repeat(3) + '~openbot-branch-' + sha;
  const newId = 'openbot-branch-' + (wrongAllocation ? 'b'.repeat(64) : digest(oldId));
  for (const [filename, identifier] of [[f.store, newId], [f.plan.currentStoreSnapshot.path, oldId]]) {
    const db = new DatabaseSync(filename), entry = JSON.parse(db.prepare('SELECT entry FROM transcript_entries').get().entry);
    entry.id = identifier; db.prepare('UPDATE transcript_entries SET id=?,entry=?').run(identifier, JSON.stringify(entry)); db.close();
  }
  f.plan.expectedStoreSHA = digest(fs.readFileSync(f.store));
  f.plan.currentStoreSnapshot.sha256 = digest(fs.readFileSync(f.plan.currentStoreSnapshot.path));
  const capture = JSON.parse(fs.readFileSync(f.plan.fullPublishedCapture.path));
  const entry = JSON.parse(Buffer.from(capture.entries[0].body, 'base64')); entry.id = newId;
  capture.entries[0].entryId = newId; capture.entries[0].body = Buffer.from(JSON.stringify(entry)).toString('base64');
  f.plan.fullPublishedCapture = f.json('published.json', capture); f.plan.identifierChanges = [{ oldId, newId }]; f.plan.records = [];
  const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path));
  inputs.currentSnapshot.store = f.plan.currentStoreSnapshot; inputs.expectedCandidateStoreSHA = f.plan.expectedStoreSHA;
  inputs.identifierChanges = f.plan.identifierChanges; inputs.records = []; inputs.publishedCapture = f.plan.fullPublishedCapture;
  f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
  return f;
}
test('real native identifier projection retains every payload and selected SDK byte under the same owner', { skip: !integration }, async t => {
  const f = identifierFixture(t), proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.beforeRootSHA, proof.afterRootSHA); assert.equal(proof.identifierChanges.length, 1);
  assert.equal(proof.ui.currentRowsByteExact, false); assert.equal(proof.ui.currentRowsPayloadExact, true);
  assert.equal(proof.identifierReferenceProof.graphUnchanged, true); assert.deepEqual(proof.identifierReferenceProof.structuralReferences, []);
});
test('real native identifier projection refuses an arbitrary replacement even if it was published', { skip: !integration }, async t => {
  const f = identifierFixture(t, { wrongAllocation: true });
  await assert.rejects(repairCandidate(f.options, f.plan), /IDENTIFIER_NOT_CANONICAL/);
});

test('real native empty bootstrap retains nontext event payload as inert literal history', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, nontext: true }), proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.context.importedMessages, 2); assert.equal(proof.context.messages, 3);
  assert.equal(proof.historicalActionsReplayed, false); assert.equal(proof.toolAttempts, 0); assert.equal(proof.networkAttempts, 0);
  assert.equal(proof.ui.records, 2); assert.equal(proof.ui.currentRowsPayloadExact, true);
});

test('real native complete empty bootstrap accepts more than the append-only 128 record bound', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, many: 129 }), proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.context.importedMessages, 129); assert.equal(proof.ui.records, 129);
  assert.equal(proof.context.messages, 130); assert.equal(proof.bounds.records, 129);
  const nonempty = nativeFixture(t, { many: 129 }); await assert.rejects(repairCandidate(nonempty.options, nonempty.plan), /PLAN_RECORD_BOUND/);
});

function sqlStateFixture(t, { auto = false } = {}) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'openbot-sql-state-')); t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const { DatabaseSync } = require('node:sqlite'), original = path.join(folder, 'original.db');
  const db = new DatabaseSync(original);
  db.exec(`CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY ${auto ? 'AUTOINCREMENT' : ''},id TEXT UNIQUE,entry TEXT);
    CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB);
    CREATE TABLE automation_completion_inbox(seq INTEGER PRIMARY KEY,id TEXT,text TEXT,attribution TEXT,acknowledged INTEGER);
    CREATE TABLE duplicates(i INTEGER,r REAL,t TEXT,b BLOB,n); CREATE TABLE without_rowid(k TEXT PRIMARY KEY,v BLOB) WITHOUT ROWID;`);
  db.prepare('INSERT INTO kv VALUES(?,?)').run('metadata', 'original-metadata');
  db.prepare('INSERT INTO blobs VALUES(?,?)').run('legacy', Buffer.from([0, 255, 128]));
  db.prepare('INSERT INTO automation_completion_inbox VALUES(?,?,?,?,?)').run(1, 'pending-completion', 'Preserve this result.', 'source', 0);
  for (let index = 0; index < 2; index++) db.prepare('INSERT INTO duplicates VALUES(?,?,?,?,?)').run(42n, 1.5, 'Unicode 🦊', Buffer.from([1, 0]), null);
  db.prepare('INSERT INTO without_rowid VALUES(?,?)').run('key', Buffer.from([9]));
  db.prepare('INSERT INTO transcript_entries VALUES(?,?,?)').run(1, 'u1', '{}'); const source = snapshotStoreState(db); db.close();
  const candidate = path.join(folder, 'candidate.db'); fs.copyFileSync(original, candidate);
  return { source, open: () => new DatabaseSync(candidate) };
}
test('protected SQL state fingerprints retain BLOBs, duplicate occurrences, types and rowids', t => {
  const f = sqlStateFixture(t), db = f.open(), state = snapshotStoreState(db), proof = verifyStoreState(f.source, state); db.close();
  assert.equal(proof.tables.duplicates.source.rows, 2); assert.equal(proof.tables.duplicates.rowidColumn, '_rowid_');
  assert.equal(proof.tables.without_rowid.rowidColumn, null); assert.equal(proof.schemaSHA, f.source.schemaSHA);
  assert.equal(proof.tables.blobs.source.sha256, proof.tables.blobs.candidate.sha256);
  assert.ok(f.source.tables.duplicates.rows.every(row => row.includes('3ff8000000000000')));
});
test('protected SQL state refuses dropped inbox, deleted legacy BLOBs, changed duplicate count and rowid', t => {
  for (const change of [db => db.exec('DROP TABLE automation_completion_inbox'), db => db.exec('DELETE FROM blobs'),
    db => db.exec('DELETE FROM duplicates WHERE rowid=2'), db => db.exec('UPDATE duplicates SET rowid=9 WHERE rowid=2'),
    db => db.exec("UPDATE automation_completion_inbox SET acknowledged=1"), db => db.exec('CREATE INDEX extra ON duplicates(i)')]) {
    const f = sqlStateFixture(t), db = f.open(); change(db); assert.throws(() => verifyStoreState(f.source, snapshotStoreState(db)), /STORE_SCHEMA_CHANGED|STORE_TABLE_CHANGED/); db.close();
  }
});
test('only transcript AUTOINCREMENT may advance and only to its actual union frontier', t => {
  const f = sqlStateFixture(t, { auto: true }), db = f.open(); db.prepare('INSERT INTO transcript_entries(id,entry)VALUES(?,?)').run('u2', '{}');
  assert.equal(verifyStoreState(f.source, snapshotStoreState(db)).sqliteSequence.candidate, '2');
  db.exec("UPDATE sqlite_sequence SET seq=99 WHERE name='transcript_entries'");
  assert.throws(() => verifyStoreState(f.source, snapshotStoreState(db)), /TRANSCRIPT_SEQUENCE_CHANGED/); db.close();
});
test('real native complete bootstrap includes unpublished current-native rows without a fabricated API capture', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, many: 3 });
  const capture = JSON.parse(fs.readFileSync(f.plan.fullPublishedCapture.path)); capture.entries.pop();
  f.plan.fullPublishedCapture = f.json('published.json', capture);
  const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path)); inputs.publishedCapture = f.plan.fullPublishedCapture;
  f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
  const proof = await repairCandidate(f.options, f.plan);
  assert.equal(proof.context.importedMessages, 3); assert.equal(proof.ui.records, 3);
  assert.equal(proof.nativeCurrentRowsUsed.length, 1); assert.equal(proof.nativeCurrentRowsUsed[0].originalId, 't3u');
  assert.equal(proof.storeStatePreserved, true); assert.equal(proof.storeState.tables.blobs.source.sha256, proof.storeState.tables.blobs.after.sha256);
});

test('real native same-ID repair refuses corrupted legacy BLOB or completion inbox state', { skip: !integration }, async t => {
  for (const operation of [db => db.exec('DELETE FROM blobs'), db => db.exec('DELETE FROM automation_completion_inbox'),
    db => db.exec('DROP TABLE automation_completion_inbox')]) {
    const f = nativeFixture(t), { DatabaseSync } = require('node:sqlite'), db = new DatabaseSync(f.store); operation(db); db.close();
    f.plan.expectedStoreSHA = digest(fs.readFileSync(f.store)); const inputs = JSON.parse(fs.readFileSync(f.plan.provenance.path));
    inputs.expectedCandidateStoreSHA = f.plan.expectedStoreSHA; f.plan.provenance = f.json('inputs.json', inputs); fs.writeFileSync(f.planFile, JSON.stringify(f.plan));
    await assert.rejects(repairCandidate(f.options, f.plan), /STORE_SCHEMA_CHANGED|STORE_TABLE_CHANGED/);
    assert.equal(fs.existsSync(path.join(f.directory, 'same-id-sdk-restoration-proof.json')), false);
  }
});

test('actual generated native protected-store proof passes the independent Python validator', { skip: !integration }, async t => {
  const f = nativeFixture(t, { empty: true, typedState: true }); await repairCandidate(f.options, f.plan);
  // Native process-local denials are restored before this TEST-ONLY Python
  // subprocess. It reads only our detached fixtures and the generated proof.
  const script = `import importlib.util,json,pathlib,sys
module_path,folder,plan_path=sys.argv[1:]
spec=importlib.util.spec_from_file_location('actual_same_id_validator',module_path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=pathlib.Path(folder);plan=json.loads(pathlib.Path(plan_path).read_text())
native=json.loads((root/'same-id-sdk-restoration-proof.json').read_text())
proof={'currentStoreSnapshot':plan['currentStoreSnapshot'],'currentRootSha256':plan['expectedCurrentRootSHA']}
current={'baseline':{'store':{'path':str(root/'store.db'),'sha256':native['files']['store.db']['sha256']}}}
state=m._validate_store_transition(m._helper(),root,proof,current,native)
print(json.dumps({'ok':True,'tables':len(state['tables'])}))
`;
  const validator = fileURLToPath(new URL('./same-id-history-restoration.py', import.meta.url));
  const result = spawnSync('python3', ['-c', script, validator, f.directory, f.planFile], { encoding: 'utf8', timeout: 15000 });
  assert.equal(result.status, 0, result.stderr); const proof = JSON.parse(result.stdout);
  assert.equal(proof.ok, true); assert.equal(proof.tables, 5);
});
