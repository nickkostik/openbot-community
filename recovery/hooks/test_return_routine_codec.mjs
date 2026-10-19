import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { digest } from './history-identifier-sdk-check.mjs';
import { validateRoutinePlan,validateRecord,authoredRoutineSemantic,normalizeAuthoredTrigger,
  mergeHistoricalRuns,mergeRawHistoricalRuns,reviewedRoutineSource } from './return-routine-codec.mjs';
const targetId = '22222222-2222-4222-8222-222222222222';
const record = () => ({id:'daily-recipe',name:'Daily recipe',prompt:' Retain this recipe exactly. ',
  trigger:{type:'cron',schedule:'8 8 * * *',timing:'flexible',window:{earlyMinutes:10,lateMinutes:20}},
  triggerPresentation:{version:1,trigger:{type:'cron',schedule:'8 8 * * *',timing:'flexible',window:{earlyMinutes:10,lateMinutes:20}}},
  isEnabled:true,provenance:'user',createdAt:1000,lastRunAt:null,runs:[]});
function plan() { const recordJson = JSON.stringify(record());return {schemaVersion:1,targetId,userTimeZone:'America/Chicago',
  routines:[{wrapper:{automationId:'source-recipe',recordJson},sourceRecordSha256:digest(recordJson),previousFiles:[]}]}; }
const run = (id = 'saved-run', startedAt = 1) => ({id,trigger:'schedule',startedAt,finishedAt:startedAt + 1,status:'ok'});

test('routine plan retains exact recordJson bytes and explicit desired enabled state', () => {
  const value = plan(); assert.equal(validateRoutinePlan(value)[0].isEnabled,true);
  value.routines[0].wrapper.recordJson += ' '; assert.throws(() => validateRoutinePlan(value),/SOURCE_RECORD_HASH/);
});
test('unknown future recipe/wrapper/plan fields refuse instead of disappearing in native serde', () => {
  for (const edit of [p => {p.extra = true;},p => {p.routines[0].wrapper.extra = true;},p => {
    const r = JSON.parse(p.routines[0].wrapper.recordJson);r.creatorAuthId = 'unsupported-owner';
    p.routines[0].wrapper.recordJson = JSON.stringify(r);p.routines[0].sourceRecordSha256 = digest(p.routines[0].wrapper.recordJson);
  }]) { const p = plan();edit(p);assert.throws(() => validateRoutinePlan(p),/UNKNOWN_FIELD/); }
});
test('unsafe destination identifiers, malformed zones and traversal IDs refuse', () => {
  for (const edit of [p => {p.targetId = '../target';},p => {p.userTimeZone = 'Unknown/Nowhere';},p => {p.routines[0].id = '../recipe';}]) {
    const p = plan();edit(p);assert.throws(() => validateRoutinePlan(p));
  }
});
test('bad flags, nonfinite timestamps and null/missing trigger refuse', () => {
  for (const patch of [{isEnabled:1},{createdAt:Infinity},{lastRunAt:NaN},{trigger:null}]) assert.throws(() => validateRecord({...record(),...patch}));
});
test('authored comparisons retain flexible windows and ignore only derived cron firesAt', () => {
  const a = record(), b = structuredClone(a);b.trigger.firesAt = '9 8 * * *';b.prompt = b.prompt.trim();
  assert.deepEqual(authoredRoutineSemantic(a,'America/Chicago'),authoredRoutineSemantic(b,'America/Chicago'));
  b.trigger.window = {earlyMinutes:0,lateMinutes:30};assert.notDeepEqual(authoredRoutineSemantic(a,'America/Chicago'),authoredRoutineSemantic(b,'America/Chicago'));
});
test('explicit CRON_TZ and effective user timezone compare identically', () => {
  assert.deepEqual(normalizeAuthoredTrigger({type:'cron',schedule:'CRON_TZ=America/Chicago 8 8 * * *'},'UTC'),
    normalizeAuthoredTrigger({type:'cron',schedule:'8   8 * * *'},'America/Chicago'));
});
test('nested grouped triggers retain unknown metadata for semantic mismatch detection', () => {
  const a = {type:'group',listeners:[{type:'cron',schedule:'8 8 * * *',future:true},{type:'webhook'}]};
  const b = structuredClone(a);delete b.listeners[0].future;
  assert.notDeepEqual(normalizeAuthoredTrigger(a,'UTC'),normalizeAuthoredTrigger(b,'UTC'));
});
test('historical runs merge exact duplicates and retain conflicting ID variants', () => {
  const first = run(), variant = {...first,status:'error'};
  assert.deepEqual(mergeHistoricalRuns([first],[first,variant,run('later',5)]),[run('later',5),first,variant]);
});
test('unknown run fields and malformed saved run envelopes refuse', () => {
  for (const patch of [{future:1},{status:'pending'},{startedAt:NaN},{trigger:'unknown'}]) assert.throws(() => mergeHistoricalRuns([{...run(),...patch}]));
});
test('more than native visible history is retained in the full historical union', () => {
  const rows = Array.from({length:30},(_,i) => run('saved-' + i,i));
  assert.equal(mergeHistoricalRuns(rows).length,30);
});
test('pending notices and nonterminal saved runs refuse without acknowledgement', () => {
  assert.throws(() => validateRecord({...record(),pendingNotices:['sand-five-minute-automation-floor']}),/PENDING_ROUTINE_NOTICES/);
  assert.throws(() => mergeHistoricalRuns([{...run(),status:'running'}]),/NONTERMINAL_ROUTINE_RUN/);
  assert.throws(() => mergeHistoricalRuns([{...run(),finishedAt:null}]),/NONTERMINAL_ROUTINE_RUN/);
  assert.doesNotThrow(() => validateRecord({...record(),raisedNotices:['sand-five-minute-automation-floor']}));
});
test('raw prior audit objects, annotations and nonterminal observations stay inert and unchanged', () => {
  const annotated = {...run(),notified:true,reason:'retained annotation',target_day:'old'};
  const orphan = {...run('orphan'),status:'running',finishedAt:null};
  const audit = {audit:'historical note',fields:{kept:true}};
  const before = JSON.stringify([annotated,orphan,audit]);
  const merged = mergeRawHistoricalRuns([run('source')],[annotated,orphan,audit]);
  assert.equal(merged.length,4);assert.ok(merged.includes(annotated));assert.ok(merged.includes(orphan));assert.ok(merged.includes(audit));
  assert.equal(JSON.stringify([annotated,orphan,audit]),before);
  assert.throws(() => mergeRawHistoricalRuns([orphan],[]),/NONTERMINAL_ROUTINE_RUN/);
});
test('unreviewed executable bytes refuse before finding or compiling boot boundaries', () => {
  assert.throws(() => reviewedRoutineSource(Buffer.from('throw Error("must not execute")')),/UNREVIEWED_HOST/);
});
test('CLI rejects an unreviewed host without materializing any routines', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),'openbot-history-tail-'));fs.chmodSync(dir,0o700);
  try {
    for (const [name,value] of [['.openbot-detached-history-tail.json',{schemaVersion:1,detached:true}],['plan.json',plan()]]) fs.writeFileSync(path.join(dir,name),JSON.stringify(value),{mode:0o600});
    const host = path.join(dir,'unreviewed.cjs');fs.writeFileSync(host,'throw Error("must not execute")',{mode:0o600});
    const p = spawnSync(process.execPath,[new URL('./return-routine-codec.mjs',import.meta.url).pathname,'--candidate-dir',dir,'--plan-file',path.join(dir,'plan.json'),'--host',host],{encoding:'utf8'});
    assert.equal(p.status,1);assert.match(p.stderr,/UNREVIEWED_HOST/);assert.equal(fs.existsSync(path.join(dir,'automations')),false);
    assert.equal(fs.existsSync(path.join(dir,'return-routine-proof.json')),false);
  } finally {fs.rmSync(dir,{recursive:true});}
});
