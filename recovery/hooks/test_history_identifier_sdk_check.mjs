import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { WORKER_SHA, validateIdentifiers, boundedOwnedFile, privateOutput, scanIdentifierStrings,
  classifyRawBlob, reviewedWorker, auditDatabase, digest, requireNonWalHeader } from './history-identifier-sdk-check.mjs';

const require = createRequire(import.meta.url);
const { DatabaseSync } = require('node:sqlite');
const alias = 'original-message-identifier-'.repeat(3) + '~openbot-branch-' + 'a'.repeat(64);
function fixture(t) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'openbot-identifier-sdk-')); fs.chmodSync(folder, 0o700);
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const write = (name, data) => { const value = path.join(folder, name); fs.writeFileSync(value, data, { mode: 0o600 }); return value; };
  return { folder, write };
}

test('only long known synthetic aliases are accepted and sorted', () => {
  assert.deepEqual(validateIdentifiers([alias + '~', alias]), [alias, alias + '~']);
  for (const ids of [[], [alias, alias], [128], ['ordinary-id'], ['prefix~openbot-branch-' + 'b'.repeat(64)],
    [alias + '\n'], [alias + 'not-an-alias'], [alias.replace(/a/g, 'G')]]) assert.throws(() => validateIdentifiers(ids));
});

test('literal text, content, summary and reasoning preserve quoted aliases', () => {
  const input = { role: 'assistant', content: [{ type: 'text', text: 'Quoted: ' + alias }],
    summary: 'Past ID ' + alias, reasoning: alias };
  const before = JSON.stringify(input), found = scanIdentifierStrings(input, [alias]);
  assert.deepEqual(found.structuralReferences, []); assert.equal(found.literalReferences.length, 3);
  assert.equal(JSON.stringify(input), before);
});

test('identifier fields, substring references, object keys and nested unknown content refuse', () => {
  for (const input of [{ id: alias }, { userMessageId: 'prefix ' + alias }, { toolCallId: alias },
    { content: [{ arbitraryId: alias }] }, { [alias]: 'value' }, { unknown: alias }]) {
    const found = scanIdentifierStrings(input, [alias]); assert.ok(found.structuralReferences.length > 0);
  }
});

test('unknown active binary blob raw hits refuse even if typed classification is unavailable', () => {
  const input = { id: 'binary', data: Buffer.from('protobuf-prefix-' + alias), activeIds: new Set(['binary']),
    coreJsonIds: new Set(), ids: [alias] };
  assert.throws(() => classifyRawBlob(input), /ACTIVE_UNKNOWN_ALIAS_REFERENCE/);
  const archived = classifyRawBlob({ ...input, activeIds: new Set() });
  assert.equal(archived.active, false); assert.equal(archived.sha256, digest(input.data));
});

test('active core JSON aliases must be literal values rather than structural fields', () => {
  const input = { id: 'core', activeIds: new Set(['core']), coreJsonIds: new Set(['core']), ids: [alias] };
  const literal = classifyRawBlob({ ...input, data: Buffer.from(JSON.stringify({ role: 'user', content: alias })) });
  assert.equal(literal.active, true); assert.equal(literal.matches, 1);
  assert.throws(() => classifyRawBlob({ ...input, data: Buffer.from(JSON.stringify({ id: alias })) }), /ACTIVE_STRUCTURAL_ALIAS_REFERENCE/);
  assert.throws(() => classifyRawBlob({ ...input, data: Buffer.from(alias) }), /ACTIVE_CORE_JSON_REQUIRED/);
});

test('empty room SDK fixture proves byte preservation without a vendor fixture', t => {
  const { write } = fixture(t), file = write('conversation-blobs.db', '');
  const setup = new DatabaseSync(file); setup.exec('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)'); setup.close();
  const before = digest(fs.readFileSync(file)), db = new DatabaseSync(file, { readOnly: true });
  try {
    const proof = auditDatabase(db, null, [alias]);
    assert.equal(proof.rootSha256, null); assert.equal(proof.rootAbsent, true); assert.equal(proof.rootRoundtrip, false);
    assert.equal(proof.graphUnchanged, true); assert.equal(proof.graphBlobs, 0);
    assert.deepEqual(proof.structuralReferences, []); assert.deepEqual(proof.literalBlobHashes, []);
    assert.deepEqual(proof.checkedIds, [alias]);
  } finally { db.close(); }
  assert.equal(digest(fs.readFileSync(file)), before);
});

test('missing SDK root with any archived or orphan blob is never accepted as an empty room', t => {
  const { write } = fixture(t), file = write('conversation-blobs.db', '');
  const db = new DatabaseSync(file); db.exec('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)');
  db.prepare('INSERT INTO blobs VALUES(?,?)').run('unrooted', Buffer.from('state'));
  try { assert.throws(() => auditDatabase(db, null, [alias]), /SDK_ROOT_MISSING_WITH_BLOBS/); } finally { db.close(); }
});

test('path traversal and symlink components are refused, private output cannot clobber', t => {
  const { folder, write } = fixture(t), file = write('data', 'private');
  assert.equal(boundedOwnedFile(file, 1024, { privateFile: true }), file);
  assert.throws(() => boundedOwnedFile(folder + '/nested/../data', 1024), /PATH_ABSOLUTE/);
  fs.symlinkSync(folder, path.join(folder, 'linked'), 'dir');
  assert.throws(() => boundedOwnedFile(path.join(folder, 'linked', 'data'), 1024), /PATH_SYMLINK/);
  fs.chmodSync(file, 0o644); assert.throws(() => boundedOwnedFile(file, 1024, { privateFile: true }), /PATH_PERMISSIONS/);
  assert.equal(privateOutput(path.join(folder, 'proof.json')), path.join(folder, 'proof.json'));
  write('proof.json', '{}'); assert.throws(() => privateOutput(path.join(folder, 'proof.json')), /OUTPUT_EXISTS/);
  fs.chmodSync(folder, 0o755); assert.throws(() => privateOutput(path.join(folder, 'other.json')), /OUTPUT_PRIVATE/);
});

test('unknown worker hash refuses before any boot boundary or module compilation', () => {
  assert.match(WORKER_SHA, /^[a-f0-9]{64}$/);
  assert.throws(() => reviewedWorker(Buffer.from('\nmain();\n')), /UNREVIEWED_WORKER/);
});

test('SQLite iterators retain their statements while callbacks force garbage collection', t => {
  const { folder } = fixture(t);
  const script = `import{DatabaseSync}from'node:sqlite';
    import{forEachSqliteRow}from${JSON.stringify(new URL('./history-identifier-sdk-check.mjs', import.meta.url).href)};
    const db=new DatabaseSync(${JSON.stringify(path.join(folder, 'iterator.db'))});
    db.exec('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB NOT NULL)');
    const put=db.prepare('INSERT INTO blobs VALUES(?,?)');
    for(let i=0;i<300;i++)put.run(String(i),Buffer.alloc(4096));
    let rows=0;forEachSqliteRow(db,'SELECT id,data FROM blobs',row=>{
      if(rows++%5===0)global.gc();if(row.data.length!==4096)throw Error('Bad row');
    });if(rows!==300)throw Error('Lost rows');db.close();`;
  const result = spawnSync(process.execPath, ['--expose-gc', '--no-warnings', '--input-type=module', '-e', script],
    { encoding: 'utf8', timeout: 15000 });
  assert.equal(result.status, 0, result.stderr);
});

test('WAL snapshots refuse from their header before read-only SQLite can create sidecars', t => {
  const { folder, write } = fixture(t), file = write('wal.db', '');
  const db = new DatabaseSync(file); db.exec('PRAGMA journal_mode=WAL;CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)'); db.close();
  const before = fs.readFileSync(file), files = fs.readdirSync(folder);
  assert.throws(() => requireNonWalHeader(before.subarray(0, 100)), /SDK_NON_WAL_SNAPSHOT_REQUIRED/);
  assert.deepEqual(fs.readdirSync(folder), files); assert.deepEqual(fs.readFileSync(file), before);
  const rollbackHeader = Buffer.from(before.subarray(0, 100)); rollbackHeader[18] = rollbackHeader[19] = 1;
  assert.doesNotThrow(() => requireNonWalHeader(rollbackHeader));
  assert.throws(() => requireNonWalHeader(Buffer.from('not sqlite')), /SDK_NON_WAL_SNAPSHOT_REQUIRED/);
});

test('CLI does not execute an unreviewed worker or alter its source database', t => {
  const { folder, write } = fixture(t), payloadMarker = path.join(folder, 'executed');
  const worker = write('unknown.cjs', `require('node:fs').writeFileSync(${JSON.stringify(payloadMarker)},'bad');\nmain();\n`);
  const db = write('blobs.db', 'not opened before pin check'), ids = write('ids.json', JSON.stringify([alias]));
  const before = digest(fs.readFileSync(db));
  const result = spawnSync(process.execPath, [fileURLToPath(new URL('./history-identifier-sdk-check.mjs', import.meta.url)),
    '--worker', worker, '--blobs-db', db, '--ids-file', ids, '--output', path.join(folder, 'proof.json')], { encoding: 'utf8', timeout: 5000 });
  assert.equal(result.status, 1); assert.equal(JSON.parse(result.stderr).error, 'UNREVIEWED_WORKER');
  assert.equal(fs.existsSync(payloadMarker), false); assert.equal(fs.existsSync(path.join(folder, 'proof.json')), false);
  assert.equal(digest(fs.readFileSync(db)), before);
});
