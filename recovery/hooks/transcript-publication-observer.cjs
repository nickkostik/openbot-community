/* openbot-transcript-publication-observer-v1 */
function openbotCreateTranscriptPublicationObserver(options) {
  const fs = options.fs ?? require('node:fs');
  const path = require('node:path');
  const now = options.now ?? Date.now;
  const random = options.random ?? Math.random;
  const schedule = options.schedule ?? ((fn, ms) => setTimeout(fn, ms));
  const cancel = options.cancel ?? clearTimeout;
  const states = new Map();
  const timers = new Map();
  const capturedFailures = new WeakSet();
  const maxRetries = 6;
  const maxAgents = 256;
  let stopped = false;
  let serial = 0;
  let writeWarning = false;
  const classes = new Set(['Error', 'TypeError', 'RangeError', 'ConnectError', 'AbortError',
    'TimeoutError', 'DeadlineExceededError', 'TranscriptLedgerCorruptError',
    'TranscriptEntryCommitRejectedError', 'SystemError', 'DOMException']);
  const codes = new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'ENETUNREACH',
    'EHOSTUNREACH', 'EAI_AGAIN', 'ENOTFOUND', 'EPIPE', 'EBUSY', 'ENOENT', 'EACCES',
    'EPERM', 'ENOSPC', 'SQLITE_BUSY', 'SQLITE_CORRUPT', 'SQLITE_READONLY', 'SQLITE_FULL']);
  const connectCodes = ['other', 'canceled', 'unknown', 'invalid_argument', 'deadline_exceeded',
    'not_found', 'already_exists', 'permission_denied', 'resource_exhausted', 'failed_precondition',
    'aborted', 'out_of_range', 'unimplemented', 'internal', 'unavailable', 'data_loss', 'unauthenticated'];
  function field(value, key) { try { return value?.[key]; } catch { return undefined; } }
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  function permitted(id) {
    if (stopped || typeof id !== 'string' || !uuid.test(id)) return false;
    try { return options.enabled() === true && options.isAllowed(id) === true; }
    catch { return false; }
  }
  function errorTag(value) {
    const match = typeof value === 'string' && /^([A-Za-z]+)(?: \(([A-Z_]+)\))?$/.exec(value);
    return { errorClass: match && classes.has(match[1]) ? match[1] : 'other',
      errorCode: match?.[2] && codes.has(match[2]) ? match[2] : 'other' };
  }
  function failureDetails(error) {
    const value = { ...errorTag(field(error, 'name')), connectCode: 'other', errorCode: 'other' };
    for (let depth = 0, current = error; current && depth < 4; depth++, current = field(current, 'cause')) {
      const code = field(current, 'code');
      if (field(current, 'name') === 'ConnectError' && Number.isInteger(code) && code > 0 && code < connectCodes.length) value.connectCode = connectCodes[code];
      else if (typeof code === 'string' && codes.has(code)) value.errorCode = code;
      const status = field(current, 'statusCode') ?? field(current, 'status') ?? field(field(current, 'response'), 'status');
      if (Number.isInteger(status) && status >= 100 && status <= 599) value.httpStatus = status;
    }
    return value;
  }
  function writePrivate(value) {
    const target = options.statePath;
    const parent = path.dirname(target);
    const directory = fs.lstatSync(parent);
    if (!directory.isDirectory() || directory.uid !== process.getuid()) throw Error('Unsafe observer directory');
    try {
      const info = fs.lstatSync(target);
      if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o077)) throw Error('Unsafe observer file');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const temporary = `${target}.${process.pid}.${++serial}.tmp`;
    let created = false;
    try {
      fs.writeFileSync(temporary, JSON.stringify(value) + '\n', { mode: 0o600, flag: 'wx' });
      created = true;
      fs.renameSync(temporary, target);
      created = false;
    } finally { if (created) fs.unlinkSync(temporary); }
  }
  function persist() {
    const value = { schemaVersion: 1, observedAtMs: now(), maxRetries,
      publicationVerified: false, nativeAppDeliveryVerified: false,
      agents: Object.fromEntries(states) };
    try {
      (options.writeState ?? writePrivate)(value);
      writeWarning = false;
    } catch {
      if (!writeWarning) {
        writeWarning = true;
        try { options.onStatusWriteError?.(); } catch { /* Diagnostics cannot break publication. */ }
      }
    }
  }
  function stateFor(id) {
    let state = states.get(id);
    if (state) return state;
    if (states.size >= maxAgents) return undefined;
    state = { phase: 'observed', retryRequests: 0, failures: 0, inFlight: false };
    states.set(id, state);
    return state;
  }
  function arm(id, state) {
    if (timers.has(id) || state.inFlight || !permitted(id)) return;
    if (state.retryRequests >= maxRetries || state.errorClass === 'TranscriptLedgerCorruptError') {
      state.phase = 'exhausted';
      delete state.retryNotBeforeMs;
      persist();
      return;
    }
    const base = Math.min(30000, 2000 * 2 ** state.retryRequests);
    const delay = Math.min(30000, Math.round(base * (0.8 + Math.max(0, Math.min(1, random())) * 0.4)));
    state.phase = 'retry-wait';
    state.retryNotBeforeMs = now() + delay;
    const timer = schedule(() => { void retry(id, state); }, delay);
    timer?.unref?.();
    timers.set(id, timer);
    persist();
  }
  async function retry(id, state) {
    timers.delete(id);
    delete state.retryNotBeforeMs;
    if (!permitted(id)) { state.phase = 'policy-paused'; return; }
    state.inFlight = true;
    state.retryRequests++;
    state.lastRetryAtMs = now();
    state.phase = 'retry-queued';
    persist();
    let drained = false;
    try {
      // Use the existing publisher's lane, database reads, generations and
      // commit implementation. The observer never manufactures entries.
      drained = await options.getPublisher().flushAgent(id) === true;
    } catch (error) {
      state.failures++;
      delete state.httpStatus;
      Object.assign(state, failureDetails(error));
      state.lastFailureAtMs = now();
    } finally {
      state.inFlight = false;
      if (stopped) return;
      if (!permitted(id)) { state.phase = 'policy-paused'; return; }
      if (drained) { state.phase = 'native-drained'; persist(); }
      else arm(id, state);
    }
  }
  return {
    retryBlocked(id) { return states.get(id)?.inFlight === true && !permitted(id); },
    commitAttempt(args) {
      const id = args?.agentId;
      if (!permitted(id)) return;
      const state = stateFor(id);
      if (!state) return;
      const sequences = [...(args.entries ?? []), ...(args.deletes ?? [])].map(item => item?.seq)
        .filter(value => Number.isSafeInteger(value) && value >= 0);
      state.nativeCommitAttempts = (state.nativeCommitAttempts ?? 0) + 1;
      if (sequences.length) state.lastAttemptedSeq = Math.max(...sequences);
      if (Number.isSafeInteger(args.generation) && args.generation >= 0) state.generation = args.generation;
      persist();
    },
    captureFailure(id, error) {
      if (!permitted(id)) return;
      const state = stateFor(id);
      if (!state) return;
      delete state.httpStatus;
      Object.assign(state, failureDetails(error));
      state.lastFailureAtMs = now();
      capturedFailures.add(state);
      persist();
    },
    observe(_level, metadata) {
      const id = metadata?.agent_id;
      if (!permitted(id) || !['failed', 'ok'].includes(metadata?.outcome)) return;
      const state = stateFor(id);
      if (!state) return;
      if (metadata.outcome === 'failed') {
        state.failures++;
        state.lastFailureAtMs = now();
        if (!capturedFailures.has(state)) {
          delete state.httpStatus;
          Object.assign(state, errorTag(metadata.error_class), { connectCode: 'other' });
        }
        capturedFailures.delete(state);
        persist();
        arm(id, state);
      } else {
        state.lastNativeCommitAtMs = now();
        if (Number.isSafeInteger(metadata.committed_rows) && metadata.committed_rows >= 0) {
          state.lastCommittedRows = metadata.committed_rows;
        }
        // A concurrent normal flush may recover before the retry timer runs.
        if (!state.inFlight) {
          if (timers.has(id)) cancel(timers.get(id));
          timers.delete(id);
          delete state.retryNotBeforeMs;
          state.phase = 'native-commit-ok';
        }
        persist();
      }
    },
    dispose() {
      stopped = true;
      for (const timer of timers.values()) cancel(timer);
      timers.clear();
    }
  };
}
module.exports = { openbotCreateTranscriptPublicationObserver };
