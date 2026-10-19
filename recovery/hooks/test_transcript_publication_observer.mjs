import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { openbotCreateTranscriptPublicationObserver: create } = require('./transcript-publication-observer.cjs');
const A = '00000000-0000-4000-8000-000000000001';
const B = '00000000-0000-4000-8000-000000000002';
function fixture(overrides = {}) {
  let now = 1000, custom = true;
  const timers = new Set(), snapshots = [], calls = [];
  const options = {
    enabled: () => custom, isAllowed: () => true, now: () => now, random: () => 0.5,
    schedule(fn, delay) { const timer = { fn, due: now + delay, delay }; timers.add(timer); return timer; },
    cancel(timer) { timers.delete(timer); },
    writeState(state) { snapshots.push(structuredClone(state)); },
    getPublisher: () => ({ async flushAgent(id) { calls.push(id); return true; } }),
    ...overrides
  };
  const observer = create(options);
  return { observer, timers, snapshots, calls, options, custom(value) { custom = value; },
    async advance(ms) { now += ms; for (const timer of [...timers]) if (timer.due <= now) { timers.delete(timer); timer.fn(); }
      await new Promise(setImmediate); },
    last() { return snapshots.at(-1); } };
}
const failure = (observer, id = A, error_class = 'ConnectError') => observer.observe('warn', { agent_id: id, outcome: 'failed', error_class });

test('retries only the failed owner through the exact canonical flushAgent queue', async () => {
  const payload = Object.freeze({ id: 'historical-message', content: 'literal history' });
  const original = JSON.stringify(payload);
  const f = fixture();
  failure(f.observer);
  f.observer.observe('info', { agent_id: B, outcome: 'ok', committed_rows: 200 });
  assert.equal(f.calls.length, 0);
  await f.advance(1999); assert.equal(f.calls.length, 0);
  await f.advance(1); assert.deepEqual(f.calls, [A]);
  assert.equal(f.last().agents[A].phase, 'native-drained');
  assert.equal(f.last().agents[B].phase, 'native-commit-ok');
  assert.equal(f.last().publicationVerified, false);
  assert.equal(f.last().nativeAppDeliveryVerified, false);
  assert.equal(JSON.stringify(payload), original);
});

test('repeated failures coalesce and back off with a finite per-agent retry budget', async () => {
  let observer;
  const f = fixture({ getPublisher: () => ({ async flushAgent(id) { failure(observer, id); return false; } }) });
  observer = f.observer;
  failure(observer); failure(observer);
  assert.equal(f.timers.size, 1);
  const delays = [];
  for (let i = 0; i < 6; i++) {
    assert.equal(f.timers.size, 1);
    const delay = [...f.timers][0].delay; delays.push(delay);
    await f.advance(delay);
  }
  assert.deepEqual(delays, [2000, 4000, 8000, 16000, 30000, 30000]);
  assert.equal(f.last().agents[A].phase, 'exhausted');
  assert.equal(f.last().agents[A].retryRequests, 6);
  assert.equal(f.timers.size, 0);
  await f.advance(1000000);
  assert.equal(f.last().agents[A].retryRequests, 6);
});

test('a failed agent never suppresses another owner retry or success', async () => {
  let observer;
  const calls = [];
  const f = fixture({ getPublisher: () => ({ async flushAgent(id) {
    calls.push(id); if (id === A) { failure(observer, A, 'Error (ECONNRESET)'); return false; }
    return true;
  } }) });
  observer = f.observer;
  failure(observer, A); failure(observer, B);
  await f.advance(2000);
  assert.deepEqual(calls.sort(), [A, B]);
  assert.equal(f.last().agents[B].phase, 'native-drained');
  assert.equal(f.last().agents[A].errorCode, 'ECONNRESET');
  assert.equal(f.timers.size, 1);
});

test('Official, ownership changes and stop cancel authority without calling the publisher', async () => {
  const f = fixture(); failure(f.observer); f.custom(false);
  await f.advance(2000); assert.deepEqual(f.calls, []);
  const noOwner = fixture({ isAllowed: () => false }); failure(noOwner.observer);
  assert.equal(noOwner.timers.size, 0); assert.equal(noOwner.snapshots.length, 0);
  const stopped = fixture(); failure(stopped.observer); stopped.observer.dispose();
  await stopped.advance(5000); assert.deepEqual(stopped.calls, []);
  assert.equal(stopped.timers.size, 0);
});

test('only allowlisted metadata is persisted, never arbitrary errors or payloads', () => {
  const f = fixture();
  f.observer.observe('warn', { agent_id: A, outcome: 'failed',
    error_class: 'Bearer_secret_private_error', message: 'DO_NOT_WRITE', body: 'PRIVATE_BODY', token: 'PRIVATE_TOKEN' });
  const saved = JSON.stringify(f.last());
  for (const secret of ['Bearer_secret_private_error', 'DO_NOT_WRITE', 'PRIVATE_BODY', 'PRIVATE_TOKEN']) assert.ok(!saved.includes(secret));
  assert.equal(f.last().agents[A].errorClass, 'other');
  f.observer.observe('warn', { agent_id: 'not-an-agent/secret', outcome: 'failed' });
  assert.equal(Object.keys(f.last().agents).length, 1);
});

test('a corrupt canonical ledger is observed without an automatic rewrite or retry', () => {
  const f = fixture(); failure(f.observer, A, 'TranscriptLedgerCorruptError');
  assert.equal(f.last().agents[A].phase, 'exhausted');
  assert.equal(f.timers.size, 0); assert.deepEqual(f.calls, []);
});

test('normal canonical recovery cancels an obsolete pending retry', async () => {
  const f = fixture(); failure(f.observer);
  f.observer.observe('info', { agent_id: A, outcome: 'ok', committed_rows: 123 });
  assert.equal(f.timers.size, 0);
  await f.advance(30000); assert.deepEqual(f.calls, []);
  assert.equal(f.last().agents[A].phase, 'native-commit-ok');
});

test('private metadata uses owner-only atomic files and refuses symlink destinations', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'openbot-publication-observer-'));
  const file = path.join(directory, 'status.json');
  try {
    const f = fixture({ writeState: undefined, statePath: file }); failure(f.observer);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.equal(JSON.parse(fs.readFileSync(file)).publicationVerified, false);
    f.observer.dispose(); fs.unlinkSync(file);
    const foreign = path.join(directory, 'foreign'); fs.writeFileSync(foreign, 'untouched');
    fs.symlinkSync(foreign, file);
    let warnings = 0;
    const unsafe = fixture({ writeState: undefined, statePath: file, onStatusWriteError: () => warnings++ });
    failure(unsafe.observer); await unsafe.advance(2000);
    assert.equal(fs.readFileSync(foreign, 'utf8'), 'untouched');
    assert.equal(warnings, 1);
    assert.deepEqual(unsafe.calls, [A]);
    unsafe.observer.dispose();
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('captures only actual Connect status and HTTP status with canonical attempt position', () => {
  const f = fixture();
  f.observer.commitAttempt({ agentId: A, generation: 3, entries: [{ seq: 101, body: 'PRIVATE' }, { seq: 200 }], deletes: [] });
  const error = Object.assign(new Error('PRIVATE_RESPONSE'), { name: 'ConnectError', code: 14, statusCode: 503 });
  f.observer.captureFailure(A, error);
  failure(f.observer, A, 'ConnectError');
  const record = f.last().agents[A];
  assert.equal(record.connectCode, 'unavailable');
  assert.equal(record.httpStatus, 503);
  assert.equal(record.nativeCommitAttempts, 1);
  assert.equal(record.lastAttemptedSeq, 200);
  assert.equal(record.generation, 3);
  assert.ok(!JSON.stringify(f.last()).includes('PRIVATE'));
  assert.equal(f.timers.size, 1);
});

test('missing or unrecognized status stays unknown instead of inferring a transport failure', () => {
  const f = fixture();
  f.observer.captureFailure(A, Object.assign(new Error('secret'), { code: 'PRIVATE_TOKEN', status: '401' }));
  failure(f.observer);
  assert.equal(f.last().agents[A].connectCode, 'other');
  assert.equal(f.last().agents[A].errorCode, 'other');
  assert.equal(f.last().agents[A].httpStatus, undefined);
});

test('a retry already queued in the canonical lane rechecks authority at drain time', async () => {
  let release, observer, commits = 0;
  const barrier = new Promise(resolve => { release = resolve; });
  const f = fixture({ getPublisher: () => ({ async flushAgent(id) {
    await barrier;
    if (observer.retryBlocked(id)) return false;
    commits++; return true;
  } }) });
  observer = f.observer;
  failure(observer); await f.advance(2000);
  assert.equal(f.last().agents[A].inFlight, true);
  f.custom(false); release(); await new Promise(setImmediate);
  assert.equal(commits, 0);
  assert.equal(f.timers.size, 0);
});

test('the fixed tracking cap never evicts and resets an exhausted agent budget', () => {
  const f = fixture();
  for (let i = 1; i <= 257; i++) failure(f.observer, '00000000-0000-4000-8000-' + i.toString(16).padStart(12, '0'), 'TranscriptLedgerCorruptError');
  assert.equal(Object.keys(f.last().agents).length, 256);
  failure(f.observer, A, 'TranscriptLedgerCorruptError');
  assert.equal(f.last().agents[A].failures, 2);
  assert.equal(f.timers.size, 0);
});

test('a generic numeric application code is not invented as a Connect status', () => {
  const f = fixture();
  f.observer.captureFailure(A, Object.assign(new Error('secret'), { code: 14 })); failure(f.observer);
  assert.equal(f.last().agents[A].connectCode, 'other');
});
