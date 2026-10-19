/** Stage paused return routines through the reviewed native file codec; never dispatch. */
import fs from 'node:fs';
import path from 'node:path';
import Module, { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { boundedOwnedFile, digest, WORKER_SHA } from './history-identifier-sdk-check.mjs';

export const ROUTINE_ENGINE_SHA = '74278c1ec791b38a235bf68e386dbbe86428bd2f9966baceb29a1173bc574dc3';
const require = createRequire(import.meta.url), MAX_PLAN = 8 * 1024 ** 2;
const SHA = /^[0-9a-f]{64}$/, UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/;
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
function check(ok, code) { if (!ok) { const error = new Error(code); error.code = code; throw error; } }
function keys(value, allowed, code) { check(object(value) && Object.keys(value).every(k => allowed.includes(k)), code); }
function canonical(v) {
  if (Array.isArray(v)) return v.map(canonical);
  if (object(v)) return Object.fromEntries(Object.keys(v).sort().filter(k => v[k] !== undefined).map(k => [k, canonical(v[k])]));
  return v;
}
const encode = v => JSON.stringify(canonical(v));
const stamp = v => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER;

/** Only native record fields are admitted; derived reader fields are retained in the immutable plan. */
export function validateRecord(record) {
  keys(record, ['id','name','prompt','trigger','schedule','triggerDescription','triggerPresentation','isEnabled',
    'provenance','sessionId','createdAt','lastRunAt','pendingNotices','raisedNotices','nextRunAt','runs','filePath'], 'RECORD_UNKNOWN_FIELD');
  check(typeof record.name === 'string' && record.name.trim().length > 0 && record.name.length <= 1024 &&
    typeof record.prompt === 'string' && record.prompt.trim().length > 0 && record.prompt.length <= 1024 ** 2,
  'RECORD_TEXT');
  check(typeof record.isEnabled === 'boolean' && stamp(record.createdAt) &&
    (record.lastRunAt === null || stamp(record.lastRunAt)), 'RECORD_STATE');
  check(record.provenance === undefined || ['user','template_import','untrusted'].includes(record.provenance), 'RECORD_PROVENANCE');
  check(record.sessionId === undefined || (typeof record.sessionId === 'string' && record.sessionId.length > 0 && record.sessionId.length <= 1024), 'RECORD_SESSION');
  for (const key of ['pendingNotices','raisedNotices']) check(record[key] === undefined ||
    (Array.isArray(record[key]) && record[key].length <= 256 && record[key].every(v => typeof v === 'string')), 'RECORD_NOTICES');
  check((record.pendingNotices ?? []).length === 0,'PENDING_ROUTINE_NOTICES');
  check(object(record.trigger), 'RECORD_TRIGGER');
  if (record.triggerPresentation !== undefined) {
    keys(record.triggerPresentation, ['version','trigger'], 'PRESENTATION_UNKNOWN_FIELD');
    check(record.triggerPresentation.version === 1 && object(record.triggerPresentation.trigger), 'RECORD_PRESENTATION');
  }
  check(record.runs === undefined || (Array.isArray(record.runs) && record.runs.length <= 20000), 'RECORD_RUN_BOUND');
  return record;
}

export function validateRoutinePlan(plan) {
  keys(plan, ['schemaVersion','targetId','userTimeZone','routines'], 'PLAN_UNKNOWN_FIELD');
  check(plan.schemaVersion === 1 && UUID.test(plan.targetId), 'PLAN_IDENTITY');
  check(typeof plan.userTimeZone === 'string' && plan.userTimeZone.length > 0 && plan.userTimeZone.length <= 128, 'PLAN_TIMEZONE');
  try { new Intl.DateTimeFormat('en', { timeZone: plan.userTimeZone }); } catch { check(false, 'PLAN_TIMEZONE'); }
  check(Array.isArray(plan.routines) && plan.routines.length <= 512, 'PLAN_ROUTINE_BOUND');
  return plan.routines.map(row => {
    keys(row, ['id','wrapper','sourceRecordSha256','previousFiles'], 'PLAN_ROUTINE_UNKNOWN_FIELD');
    check(row.id === undefined || SAFE_ID.test(row.id), 'ROUTINE_ID');
    keys(row.wrapper, ['automationId','recordJson'], 'WRAPPER_UNKNOWN_FIELD');
    check(typeof row.wrapper.automationId === 'string' && row.wrapper.automationId.length > 0 && row.wrapper.automationId.length <= 1024 &&
      typeof row.wrapper.recordJson === 'string' && Buffer.byteLength(row.wrapper.recordJson) <= 2 * 1024 ** 2 &&
      SHA.test(row.sourceRecordSha256) && digest(row.wrapper.recordJson) === row.sourceRecordSha256, 'SOURCE_RECORD_HASH');
    let record; try { record = JSON.parse(row.wrapper.recordJson); } catch { check(false, 'SOURCE_RECORD_JSON'); }
    validateRecord(record);
    check(Array.isArray(row.previousFiles) && row.previousFiles.length <= 32, 'PREVIOUS_FILES');
    const seen = new Set();
    for (const file of row.previousFiles) {
      keys(file, ['kind','path','sha256'], 'PREVIOUS_FILE_UNKNOWN_FIELD');
      check(['config','runs'].includes(file.kind) && typeof file.path === 'string' && SHA.test(file.sha256) && !seen.has(file.path), 'PREVIOUS_FILE');
      seen.add(file.path);
    }
    return record;
  });
}

/** Ignore only derived cron firesAt; explicit/effective timezone is compared semantically. */
export function normalizeAuthoredTrigger(trigger, userTimeZone, depth = 0) {
  check(object(trigger) && depth < 12 && typeof trigger.type === 'string', 'TRIGGER_SHAPE');
  const result = {};
  for (const [key,value] of Object.entries(trigger)) {
    if (key === 'firesAt' && trigger.type === 'cron') continue;
    if (key === 'listeners') {
      check(trigger.type === 'group' && Array.isArray(value) && value.length > 0 && value.length <= 128, 'TRIGGER_GROUP');
      result[key] = value.map(t => normalizeAuthoredTrigger(t, userTimeZone, depth + 1));
    } else result[key] = value;
  }
  if (trigger.type === 'cron') {
    check(typeof trigger.schedule === 'string', 'TRIGGER_CRON');
    let schedule = trigger.schedule.trim().split(/\s+/).join(' '), zone = userTimeZone;
    if (schedule.startsWith('CRON_TZ=')) {
      const at = schedule.indexOf(' '); check(at > 8, 'TRIGGER_CRON_TIMEZONE');
      zone = schedule.slice(8,at); schedule = schedule.slice(at + 1);
    }
    result.schedule = schedule; result.effectiveTimeZone = zone;
  }
  return canonical(result);
}

export function authoredRoutineSemantic(record, zone) {
  return canonical({ name: record.name, prompt: record.prompt.trim(), trigger: normalizeAuthoredTrigger(record.trigger, zone),
    triggerPresentation: record.triggerPresentation ? { version: record.triggerPresentation.version,
      trigger: normalizeAuthoredTrigger(record.triggerPresentation.trigger, zone) } : null,
    provenance: record.provenance ?? 'untrusted', ...(record.sessionId === undefined ? {} : { sessionId: record.sessionId }),
    createdAt: record.createdAt, lastRunAt: record.lastRunAt,
    pendingNotices: record.pendingNotices ?? [], raisedNotices: record.raisedNotices ?? [] });
}

/** Keep conflicting historical variants; native UI exposes only its bounded recent window. */
export function mergeHistoricalRuns(...sources) {
  const seen = new Set(), all = [];
  for (const runs of sources) {
    check(Array.isArray(runs), 'RUNS_ARRAY');
    for (const run of runs) {
      keys(run, ['id','requestId','trigger','startedAt','finishedAt','status','detail','errorKind','event','coalescedRunIds'], 'RUN_UNKNOWN_FIELD');
      check(typeof run.id === 'string' && run.id.length > 0 && stamp(run.startedAt) &&
        (run.finishedAt === null || stamp(run.finishedAt)) && ['ok','error','running'].includes(run.status) &&
        ['manual','event','schedule'].includes(run.trigger), 'RUN_SCHEMA');
      check(run.status !== 'running' && run.finishedAt !== null,'NONTERMINAL_ROUTINE_RUN');
      const raw = encode(run); if (!seen.has(raw)) { seen.add(raw); all.push(run); }
      check(all.length <= 20000, 'RUN_BOUND');
    }
  }
  return all.sort((a,b) => b.startedAt - a.startedAt);
}

/** Previous native files are immutable historical evidence, not fresh work.
 * Preserve arbitrary JSON audit rows and annotations; native readers project
 * only known run fields. Source API runs retain the stricter current schema. */
export function mergeRawHistoricalRuns(sourceRuns, ...previousLists) {
  const source = mergeHistoricalRuns(sourceRuns), all = [], seen = new Set();
  let nodes = 0;
  function boundedJson(value, depth = 0) {
    check(++nodes <= 500000 && depth <= 40,'HISTORICAL_JSON_BOUND');
    check(value === null || ['string','boolean'].includes(typeof value) ||
      (typeof value === 'number' && Number.isFinite(value)) || Array.isArray(value) || object(value),'HISTORICAL_JSON_TYPE');
    if (Array.isArray(value)) value.forEach(item => boundedJson(item,depth + 1));
    else if (object(value)) Object.values(value).forEach(item => boundedJson(item,depth + 1));
  }
  for (const rows of [source,...previousLists]) {
    check(Array.isArray(rows),'HISTORICAL_RUNS_ARRAY');
    for (const row of rows) {
      boundedJson(row); const raw = encode(row);
      check(Buffer.byteLength(raw) <= 1024 ** 2,'HISTORICAL_ROW_BOUND');
      if (!seen.has(raw)) { seen.add(raw); all.push(row); } check(all.length <= 20000,'RUN_BOUND');
    }
  }
  const time = value => object(value) && Number.isFinite(value.startedAt) ? value.startedAt : -Infinity;
  return all.sort((a,b) => time(b) - time(a));
}

export function reviewedRoutineSource(raw) {
  check(digest(raw) === ROUTINE_ENGINE_SHA, 'UNREVIEWED_HOST');
  const source = raw.toString('utf8'), boot = 'bootSandHost(runHostMain).catch(';
  const a = source.indexOf(boot), b = source.indexOf('/*! Bundled license information:', a);
  check(a > 0 && b > a && source.indexOf(boot,a + 1) < 0, 'HOST_BOOT_BOUNDARY');
  return source.slice(0,a) + `module.exports={serializeAutomation:serializeStoredAutomationConfig,
    parseAutomation:parseStoredAutomationConfig,serializeRuns:serializeStoredAutomationRuns,parseRuns:parseStoredAutomationRuns,
    serializeTrigger:serializeStoredTrigger,AutomationStore:FileAutomationStore,slug:slugifyAutomationName,historyLimit:AUTOMATION_MAX_RUN_HISTORY};\n` + source.slice(b);
}

function denySideEffects() {
  const restores = [], counts = { networkAttempts: 0, toolAttempts: 0 };
  const patch = (target,key,value) => { const old = Object.getOwnPropertyDescriptor(target,key);
    Object.defineProperty(target,key,{value,configurable:true,writable:true});
    restores.push(() => old ? Object.defineProperty(target,key,old) : delete target[key]); };
  const deny = code => () => { counts[code === 'NETWORK_DENIED' ? 'networkAttempts' : 'toolAttempts']++; check(false,code); };
  const net = deny('NETWORK_DENIED'), tool = deny('TOOL_DENIED');
  for (const key of ['fetch','WebSocket']) patch(globalThis,key,net);
  for (const name of ['node:http','node:https']) for (const key of ['request','get']) patch(require(name),key,net);
  for (const name of ['node:net','node:tls','node:http2']) for (const key of ['connect','createConnection']) if (key in require(name)) patch(require(name),key,net);
  patch(require('node:net').Socket.prototype,'connect',net); patch(require('node:dgram'),'createSocket',net);
  for (const key of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork']) patch(require('node:child_process'),key,tool);
  patch(require('node:worker_threads'),'Worker',tool); patch(require('node:cluster'),'fork',tool);
  return { counts, restore() { for (const fn of restores.reverse()) fn(); } };
}

function candidateDirectory(directory) {
  check(typeof directory === 'string' && path.isAbsolute(directory) && path.normalize(directory) === directory &&
    path.basename(directory).startsWith('openbot-history-tail-'), 'CANDIDATE_PATH');
  const stat = fs.lstatSync(directory);
  check(stat.isDirectory() && !stat.isSymbolicLink() && stat.uid === process.getuid?.() && (stat.mode & 0o077) === 0, 'CANDIDATE_PRIVATE');
  const marker = path.join(directory,'.openbot-detached-history-tail.json'); boundedOwnedFile(marker,1024,{privateFile:true});
  const value = JSON.parse(fs.readFileSync(marker)); check(value.schemaVersion === 1 && value.detached === true, 'CANDIDATE_MARKER');
  check(!fs.existsSync(path.join(directory,'automations')) && !fs.existsSync(path.join(directory,'return-routine-proof.json')), 'CANDIDATE_OUTPUT_EXISTS');
}

export function stageReturnRoutines(options, plan) {
  const records = validateRoutinePlan(plan); candidateDirectory(options.candidateDir);
  boundedOwnedFile(options.planFile,MAX_PLAN,{privateFile:true});
  const planRaw = fs.readFileSync(options.planFile); check(encode(JSON.parse(planRaw)) === encode(plan), 'PLAN_FILE_MISMATCH');
  boundedOwnedFile(options.host,128 * 1024 ** 2,{allowRoot:true});
  const hostRaw = fs.readFileSync(options.host), source = reviewedRoutineSource(hostRaw);
  // The retained archive omits installed parser dependencies. Resolve against the
  // reviewed native worker's installation, as the SDK checkpoint readers do.
  const workerPath = options.worker ?? '/opt/sand/sand-host/agent-isolation/agent-store-worker.cjs';
  boundedOwnedFile(workerPath,128 * 1024 ** 2,{allowRoot:true});
  check(digest(fs.readFileSync(workerPath)) === WORKER_SHA,'UNREVIEWED_WORKER');
  const archived = new Map();
  for (const row of plan.routines) for (const previous of row.previousFiles) {
    boundedOwnedFile(previous.path,8 * 1024 ** 2,{privateFile:true});
    const raw = fs.readFileSync(previous.path); check(digest(raw) === previous.sha256, 'PREVIOUS_FILE_HASH'); archived.set(previous.path,raw);
  }
  const staged = path.join(options.candidateDir,'.return-routines-' + randomUUID());
  const guard = denySideEffects(); let published = false;
  try {
    const mod = new Module(options.host); mod.filename = path.join(path.dirname(options.host),'openbot-return-routine-check.cjs');
    mod.paths = [...new Set([options.host,workerPath,process.execPath].flatMap(file => Module._nodeModulePaths(path.dirname(file))))];
    mod._compile(source,mod.filename); const sdk = mod.exports;
    for (const key of ['serializeAutomation','parseAutomation','serializeRuns','parseRuns','serializeTrigger','AutomationStore','slug']) check(typeof sdk[key] === 'function','NATIVE_EXPORT');
    fs.mkdirSync(staged,{mode:0o700}); const output = [], used = new Set();
    for (let index = 0; index < records.length; index++) {
      const row = plan.routines[index], record = records[index], id = row.id ?? record.id ?? sdk.slug(record.name);
      check(SAFE_ID.test(id) && !used.has(id),'ROUTINE_ID_COLLISION'); used.add(id);
      const paused = { ...record, isEnabled:false, pendingNotices:record.pendingNotices ?? [], raisedNotices:record.raisedNotices ?? [] };
      const stored = JSON.parse(sdk.serializeAutomation(paused));
      // Native cron shorthand omits timing/window. Retain the full reviewed trigger.
      stored.trigger = sdk.serializeTrigger(record.trigger);
      if (record.triggerPresentation) stored.triggerPresentation = {version:1,trigger:sdk.serializeTrigger(record.triggerPresentation.trigger)};
      const configRaw = JSON.stringify(stored,null,2) + '\n', parsed = sdk.parseAutomation(configRaw,record.createdAt);
      check(parsed && parsed.isEnabled === false,'NATIVE_CONFIG_PARSE');
      const semantics = authoredRoutineSemantic(record,plan.userTimeZone);
      assert.deepEqual(authoredRoutineSemantic(parsed,plan.userTimeZone),semantics,'Native authored semantics');
      const previousRuns = [];
      for (const previous of row.previousFiles) if (previous.kind === 'runs') {
        let rawRuns; try { rawRuns = JSON.parse(archived.get(previous.path)); } catch { check(false,'PREVIOUS_RUNS_JSON'); }
        previousRuns.push(rawRuns);
      }
      const sourceRuns = mergeHistoricalRuns(record.runs ?? []);
      for (const run of sourceRuns) assert.deepEqual(sdk.parseRuns(sdk.serializeRuns([run])),[run],'Native source run exactness');
      const runs = mergeRawHistoricalRuns(sourceRuns,...previousRuns);
      const projected = runs.flatMap(run => sdk.parseRuns(sdk.serializeRuns([run]))).sort((a,b) => b.startedAt - a.startedAt);
      check(Number.isSafeInteger(sdk.historyLimit) && sdk.historyLimit > 0,'NATIVE_HISTORY_LIMIT');
      const runsRaw = sdk.serializeRuns(runs), visible = sdk.parseRuns(runsRaw);
      assert.deepEqual(visible,projected.slice(0,sdk.historyLimit),'Independent native visible history projection');
      assert.deepEqual(JSON.parse(runsRaw),runs,'Full historical run variants');
      const folder = path.join(staged,id); fs.mkdirSync(folder,{mode:0o700});
      fs.writeFileSync(path.join(folder,'automation.json'),configRaw,{mode:0o600,flag:'wx'});
      fs.writeFileSync(path.join(folder,'runs.json'),runsRaw,{mode:0o600,flag:'wx'});
      output.push({id,sourceAutomationId:row.wrapper.automationId,sourceRecordSha256:row.sourceRecordSha256,
        previousFiles:row.previousFiles,configPath:'automations/' + id + '/automation.json',runsPath:'automations/' + id + '/runs.json',desiredEnabled:record.isEnabled,allPaused:true,configSha256:digest(configRaw),runsSha256:digest(runsRaw),
        authoredSha256:digest(encode(semantics)),retainedRunCount:runs.length,nativeVisibleRunCount:visible.length,nativeRunHistoryLimit:sdk.historyLimit,
        priorHistoricalNonterminalCount:previousRuns.flat().flatMap(run => sdk.parseRuns(sdk.serializeRuns([run]))).filter(run => run.status === 'running' || run.finishedAt === null).length,
        fullHistoricalRunsRetained:true,nativeSerdeVerified:true,visible});
    }
    const store = new sdk.AutomationStore(staged,() => plan.userTimeZone), read = store.list();
    check(read.length === output.length,'NATIVE_STORE_COUNT');
    for (let index = 0; index < output.length; index++) {
      const row = output[index], actual = read.find(item => item.id === row.id); check(actual && actual.isEnabled === false,'NATIVE_STORE_PAUSED');
      assert.deepEqual(authoredRoutineSemantic(actual,plan.userTimeZone),authoredRoutineSemantic(records[index],plan.userTimeZone),'Native store authored semantics');
      assert.deepEqual(actual.runs,row.visible,'Native visible runs'); delete row.visible;
      check(digest(fs.readFileSync(path.join(staged,row.id,'automation.json'))) === row.configSha256 &&
        digest(fs.readFileSync(path.join(staged,row.id,'runs.json'))) === row.runsSha256,'NATIVE_STORE_REWROTE_FILES'); row.nativeStoreVerified = true;
    }
    check(digest(fs.readFileSync(options.planFile)) === digest(planRaw),'PLAN_CHANGED');
    for (const [filename,raw] of archived) check(digest(fs.readFileSync(filename)) === digest(raw),'PREVIOUS_FILE_CHANGED');
    check(guard.counts.networkAttempts === 0 && guard.counts.toolAttempts === 0,'NATIVE_SIDE_EFFECT');
    fs.renameSync(staged,path.join(options.candidateDir,'automations')); published = true;
    const proof = {schema:'openbot-return-routine-codec/v1',engineSha256:ROUTINE_ENGINE_SHA,
      bridgeSha256:digest(fs.readFileSync(fileURLToPath(import.meta.url))),sourcePlanFileSha256:digest(planRaw),targetId:plan.targetId,
      routines:output,allPaused:true,authoredSemanticsVerified:true,previousArchivesUnchanged:true,
      sourceUpdated:false,liveApplied:false,historicalActionsReplayed:false,providerCalls:0,...guard.counts};
    fs.writeFileSync(path.join(options.candidateDir,'return-routine-proof.json'),JSON.stringify(proof) + '\n',{mode:0o600,flag:'wx'});
    return proof;
  } finally {
    guard.restore(); if (!published && fs.existsSync(staged)) fs.rmSync(staged,{recursive:true});
  }
}

async function main(args) {
  check(args.length % 2 === 0,'CLI_ARGUMENTS'); const options = {}, names = {'--candidate-dir':'candidateDir','--plan-file':'planFile','--host':'host','--worker':'worker'};
  for (let at = 0; at < args.length; at += 2) { const key = names[args[at]]; check(key && !Object.hasOwn(options,key),'CLI_ARGUMENTS'); options[key] = args[at + 1]; }
  check(options.candidateDir && options.planFile && options.host,'CLI_ARGUMENTS'); boundedOwnedFile(options.planFile,MAX_PLAN,{privateFile:true});
  const proof = stageReturnRoutines(options,JSON.parse(fs.readFileSync(options.planFile)));
  process.stdout.write(JSON.stringify({ok:true,routines:proof.routines.length,allPaused:true,liveApplied:false}) + '\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2)).catch(error => {
  process.stderr.write(JSON.stringify({ok:false,error:/^[A-Z][A-Z0-9_]+$/.test(error.code ?? '') ? error.code : 'NATIVE_ROUTINE_PROOF_REFUSED',liveApplied:false}) + '\n'); process.exitCode = 1;
});
