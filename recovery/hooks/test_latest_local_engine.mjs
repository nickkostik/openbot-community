import { test } from "node:test";
import { strict as assert } from "node:assert";
import { indexBundle, planCompatibility, applyEdits, counterpart, restoreSharedClassMembers,
  retireUnusedSharedFeatureReader, restoreLocalExperimentEntries,
  restoreSharedApiMembers, restoreLocalGatewayReaders, restoreLocalGatewayActions, adaptOptionalEmailApi, adaptBuiltinCatalogOptions } from "./latest-local-engine.mjs";
import { createRequire } from "node:module";
import * as fs from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const { installPeerDeliveryCoalescing, installLocalEngineStartupTrace,
  localSendOptions, installLocalEngineSendTrace, composeLocalRunnerGates,
  installLocalEngineTurnTrace, localTaskArguments, localRequestContextOptions } = createRequire(import.meta.url)("./latest-box-ports.cjs");

test("lexical linking preserves shadowed locals, property names and shorthand keys", () => {
  const old = indexBundle(`// src/value.ts\nvar value = 1;\n// src/runner.ts\nfunction run() { const a = {value}; const b = {value: value}; function local(value) { return {value}; } return [a, b, local]; }`);
  const latest = indexBundle(`// src/value.ts\nvar value7 = 2;\n`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.match(p.legacy, /const a = \{value: value7\}/);
  assert.match(p.legacy, /const b = \{value: value7\}/);
  assert.match(p.legacy, /function local\(value\) \{ return \{value\}; \}/);
  assert.equal(p.modules.size, 1);
});

test("removed execution dependencies are restored transitively and isolated from compiler names", () => {
  const old = indexBundle(`// src/dependency.ts\nvar Removed = class {};\n// src/runner.ts\nfunction run() { return new Removed(); }`);
  const latest = indexBundle(`// src/unrelated.ts\nvar Removed = "different binding";`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.equal(p.modules.size, 2);
  assert.match(p.legacy, /new __openbot_box_Removed/);
  assert.match(p.legacy, /var __openbot_box_Removed = class/);
});

test("a changed argument contract is retained for explicit porting instead of linked by name", () => {
  const old = indexBundle(`// src/policy.ts\nfunction policy(host, request) { return request; }\n// src/runner.ts\nfunction run() { return policy(1, 2); }`);
  const latest = indexBundle(`// src/policy.ts\nfunction policy(host, decisions, request) { return decisions; }`);
  assert.equal(counterpart(old.globals.get("policy"), latest), undefined);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.equal(p.modules.size, 2);
  assert.match(p.legacy, /__openbot_box_policy\(1, 2\)/);
});

test("redirected classes do not overwrite upstream declarations or unrelated bindings", () => {
  const old = indexBundle(`// src/runner.ts\nvar Runner = class { run() {} };`);
  const latest = indexBundle(`// src/runner.ts\nvar Runner2 = class { refusal() {} };\n// src/host.ts\nfunction start() { return new Runner2(); }`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  const result = applyEdits(latest.source, p.newEdits);
  assert.match(result, /var Runner2 = class/);
  assert.match(result, /return new __openbot_box_Runner\(\)/);
});

test("ambiguous module bindings and compatibility namespace collisions are rejected", () => {
  const old = indexBundle(`// src/policy.ts\nfunction policy() {}\n// src/runner.ts\nfunction run() { policy(); }`);
  const latest = indexBundle(`// src/policy.ts\nfunction policy2() {}\nfunction policy3() {}\n// src/elsewhere.ts\nvar __openbot_box_run = 1;`);
  assert.equal(counterpart(old.globals.get("policy"), latest), undefined);
  assert.throws(() => planCompatibility(old, latest, ["src/runner.ts"]), /namespace collision/);
});

test("conflicting edits and overlapping replacements fail before a candidate is produced", () => {
  assert.throws(() => applyEdits("abc", [{start:0,end:1,text:"a"},{start:0,end:1,text:"b"}]), /Conflicting/);
  assert.throws(() => applyEdits("abc", [{start:0,end:2,text:"a"},{start:1,end:3,text:"b"}]), /Overlapping/);
});

test("esbuild initializer writes are isolated while reads inherit the latest shared implementation", () => {
  const old = indexBundle(`// src/shared.ts\nvar f;\nvar init = () => { f = (value) => value; };\n// src/runner.ts\nfunction run() { init(); return f(1); }`);
  const latest = indexBundle(`// src/shared.ts\nvar f3;\n`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.match(p.legacy, /__openbot_box_f = \(value\)/);
  assert.match(p.legacy, /return f3\(1\)/);
});

test("references to globals declared in later statements are correctly linked", () => {
  const old = indexBundle(`// src/runner.ts\nfunction run() { return value; }\n// src/value.ts\nvar value = 1;`);
  const latest = indexBundle(`// src/value.ts\nvar value9 = 2;`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.match(p.legacy, /return value9/);
  assert.equal(p.modules.size, 1);
});

test("defaulted destructuring preserves public keys and indexes every global", () => {
  const old = indexBundle(`// src/runner.ts\nvar {read = 0, write: mode = 1} = source;\nfunction run() { return {read, mode}; }`);
  const p = planCompatibility(old, indexBundle(""), ["src/runner.ts"]);
  assert.equal(old.globals.size, 3);
  assert.match(p.legacy, /read: __openbot_box_read = 0/);
  assert.match(p.legacy, /write: __openbot_box_mode = 1/);
});

test("internal state writers share the restored state while unrelated upstream writers fail closed", () => {
  const old = indexBundle(`// src/runner.ts\nvar state;\nfunction set() { state = 1; }`);
  const latest = indexBundle(`// src/runner.ts\nvar state;\nfunction set() { state = 2; }`);
  const p = planCompatibility(old, latest, ["src/runner.ts"]);
  assert.match(applyEdits(latest.source, p.newEdits), /__openbot_box_state = 2/);
  const other = indexBundle(latest.source + `\n// src/other.ts\nfunction overwrite() { state = 3; }`);
  assert.throws(() => planCompatibility(old, other, ["src/runner.ts"]), /external upstream writer/);
});

test("reviewed shared database methods use new helpers and preserve upstream migrations", () => {
  const old = indexBundle(`// src/db.ts\nvar KEY = 'scope'; var Store = class { scoped() { return this.read(KEY); } };\n// src/runner.ts\nfunction run(db) { return db.scoped(); }`);
  const latest = indexBundle(`// src/db.ts\nvar KEY7 = 'scope'; var Store = class { migrate() { return 'new migration'; } };`);
  const plan = planCompatibility(old, latest, ["src/runner.ts"], ["src/db.ts"]);
  assert.equal(plan.forced.has("Store"), false);
  assert.equal(plan.read.get("Store"), "Store");
  const port = restoreSharedClassMembers(old, latest, plan, {Store:["scoped"]});
  const source = applyEdits(latest.source, port.edits);
  assert.match(source, /migrate\(\).*new migration/);
  assert.match(source, /scoped\(\).*this.read\(KEY7\)/);
  assert.deepEqual(port.restored, [{class:"Store",members:["scoped"]}]);
});

test("unexpected shared interface changes cannot activate without an explicit recipe", () => {
  const old = indexBundle(`// src/db.ts\nvar Store = class { first() {} second() {} };`);
  const latest = indexBundle(`// src/db.ts\nvar Store = class {};`);
  const plan = planCompatibility(old, latest, [], ["src/db.ts"]);
  assert.throws(() => restoreSharedClassMembers(old, latest, plan, {Store:["first"]}), /interface changed/);
  const forced = planCompatibility(old, latest, ["src/db.ts"]);
  assert.throws(() => restoreSharedClassMembers(old, latest, forced, {Store:["first","second"]}), /Unreviewed shared/);
});

test("restored adapter exports forward through the latest live reporter and retain new exports", () => {
  const old = indexBundle(`// src/api.ts\nfunction adapter(telemetry, analytics) { return {
    current: () => telemetry.current(), revive: report => telemetry.revive(report),
    run: report => { analytics.track(report); return telemetry.run(report); }
  }; }\n// src/runner.ts\nfunction execute(log, analytics) { return adapter(log, analytics).revive('result'); }`);
  const latest = indexBundle(`// src/api.ts\nfunction adapter(telemetry, analytics) { return {
    current: () => telemetry.current(), added: () => telemetry.added()
  }; }`);
  const plan = planCompatibility(old, latest, ["src/runner.ts"], ["src/api.ts"]);
  const ports = restoreSharedApiMembers(old, latest, plan, {adapter:["revive", "run"]});
  const factory = new Function(applyEdits(latest.source, ports.edits) + "\nreturn adapter;")();
  const calls = [], reports = [];
  const api = factory({current:()=>"latest", added:()=>"new", revive:r=>calls.push(r),
    run:r=>r}, {track:r=>reports.push(r)});
  assert.equal(api.current(), "latest"); assert.equal(api.added(), "new");
  api.revive("completion"); assert.deepEqual(calls, ["completion"]);
  assert.equal(api.run("routine"), "routine"); assert.deepEqual(reports, ["routine"]);
  assert.deepEqual(ports.restored, [{function:"adapter", exports:["revive", "run"]}]);
});

test("unreviewed or dynamic adapter export changes cannot be ported", () => {
  const old = indexBundle(`// src/api.ts\nfunction adapter() { return {one:()=>1, two:()=>2}; }`);
  const latest = indexBundle(`// src/api.ts\nfunction adapter() { return {}; }`);
  const plan = planCompatibility(old, latest, [], ["src/api.ts"]);
  assert.throws(() => restoreSharedApiMembers(old, latest, plan, {adapter:["one"]}), /exports changed/);
  assert.throws(() => restoreSharedApiMembers(old, latest, plan, {adapter:["one", "one"]}), /Unreviewed/);
  const dynamic = indexBundle(`// src/api.ts\nfunction adapter() { return {...other}; }`);
  assert.throws(() => restoreSharedApiMembers(old, dynamic, plan, {adapter:["one", "two"]}), /exports changed/);
});

test("removed attachment factories retain their dependencies and latest native exports", () => {
  const old = indexBundle(`// src/image.ts\nfunction imageFactory(env, auth, options) { return () => [env, auth, options]; }
// src/attachments.ts\nfunction attachments(deps) { return {upload:()=> 'old',
  createGenerateImageService:options=>imageFactory(deps.environment,deps.auth,options)}; }
// src/runner.ts\nfunction run(deps) { return attachments(deps).createGenerateImageService({persist:true}); }`);
  const latest = indexBundle(`// src/attachments.ts\nfunction attachments(deps) { return {upload:()=> 'latest',newExport:()=>42}; }`);
  const plan = planCompatibility(old, latest, ['src/runner.ts'], ['src/attachments.ts']);
  const restored = restoreSharedApiMembers(old, latest, plan,
    {attachments:['createGenerateImageService']});
  const source = applyEdits(latest.source, [...plan.newEdits,...restored.edits]) + '\n' + plan.legacy;
  const {api,run} = new Function(source + '\nreturn {api:attachments,run:__openbot_box_run};')();
  const deps={environment:'live',auth:'saved'};
  assert.equal(api(deps).upload(),'latest');assert.equal(api(deps).newExport(),42);
  assert.deepEqual(run(deps)(),['live','saved',{persist:true}]);
});

test("missing optional email does not break local turns or bypass a live disabled policy", () => {
  const source = `function configure(extensions) {return {
      isAgentEmailAllowedByTeamAdmin: () => extensions.api("email").isEnabled(),
      ...extensions.api("email").isEnabled() ? { email: extensions.api("email").email } : {},
  };}`;
  const adapted=adaptOptionalEmailApi(source,'optional-native-capability');
  const configure=new Function(adapted+'\nreturn configure;')();
  for(const service of [undefined,null,{isEnabled:()=>false,email:'disabled'}]){
    const result=configure({api:()=>service});assert.equal(result.isAgentEmailAllowedByTeamAdmin(),false);
    assert.equal(Object.hasOwn(result,'email'),false);
  }
  const live={email:'native',allowed:true,isEnabled(){return this.allowed;}};
  const result=configure({api:()=>live});assert.equal(result.email,'native');
  assert.equal(result.isAgentEmailAllowedByTeamAdmin(),true);live.allowed=false;
  assert.equal(result.isAgentEmailAllowedByTeamAdmin(),false);
  assert.throws(()=>configure({api:()=>({isEnabled:42})}),/Invalid native/);
  assert.throws(()=>configure({api:()=>({isEnabled(){throw new Error('policy failed');}})}),/policy failed/);
  assert.throws(()=>adaptOptionalEmailApi(source,'force-enabled'),/Unreviewed/);
  assert.throws(()=>adaptOptionalEmailApi(source.replace('isAgentEmailAllowedByTeamAdmin','changed'),'optional-native-capability'),/anchor changed/);
});

test("legacy catalogs omit external options while latest catalogs retain live values and failures", () => {
  const source='function options(registry3) { return registry3?.getExternalCatalogOptions(); }';
  const adapted=adaptBuiltinCatalogOptions(source,'optional-native-reader');
  const options=new Function(adapted+'\nreturn options;')();
  for(const value of [undefined,null,{}]) assert.equal(options(value),undefined);
  const live={value:{namespaces:['latest']},getExternalCatalogOptions(){return this.value;}};
  assert.equal(options(live),live.value);live.value={namespaces:['updated']};assert.equal(options(live),live.value);
  assert.throws(()=>options({getExternalCatalogOptions:42}),/Invalid native/);
  assert.throws(()=>options({getExternalCatalogOptions(){throw new Error('reader failed');}}),/reader failed/);
  assert.throws(()=>adaptBuiltinCatalogOptions(source,'invented'),/Unreviewed/);
  assert.throws(()=>adaptBuiltinCatalogOptions(source+source,'optional-native-reader'),/anchor changed/);
});

test("relay drain wakes modern polling without the removed notify gate closure", () => {
  const old = indexBundle(`// src/relay.ts\nfunction createBackendRelaySources() {
    const notifyGate = {recordNotify(){}}; let timer = null;
    async function tick() {} return {requestDrain:()=>{notifyGate.recordNotify(); if(timer != null) void tick();}};
  }`);
  const latest = indexBundle(`// src/relay.ts\nfunction createBackendRelaySources() {
    let timer = null; let ticks = 0; async function tick() { ticks++; }
    return {start:()=>{timer = {};}, count:()=>ticks};
  }`);
  const plan = planCompatibility(old, latest, [], ["src/relay.ts"]);
  const ports = restoreSharedApiMembers(old, latest, plan, {createBackendRelaySources:["requestDrain"]});
  const factory = new Function(applyEdits(latest.source, ports.edits) + "\nreturn createBackendRelaySources;")();
  const source = factory(); source.requestDrain(); assert.equal(source.count(), 0);
  source.start(); source.requestDrain(); assert.equal(source.count(), 1);
});

test("gateway readers expose local child and shell activity without replacing latest methods", async () => {
  const old = indexBundle(`// src/gateway.ts\nfunction createHostGatewayApi(manager) {
    const api = {getSubagents:args=>manager.getSubagents(args.id),
      getAsyncTasks:args=>manager.getAsyncTasks(args.id)}; return api;
  }`);
  const latest = indexBundle(`// src/gateway.ts\nfunction createHostGatewayApi(manager) {
    const api = {getSubagents:async()=>[],getAsyncTasks:async()=>[],latest:()=>42}; return api;
  }`);
  const plan = planCompatibility(old, latest, [], ["src/gateway.ts"]);
  const ports = restoreLocalGatewayReaders(old, latest, plan, ["getSubagents", "getAsyncTasks"]);
  const factory = new Function(applyEdits(latest.source, ports.edits) + "\nreturn createHostGatewayApi;")();
  const calls = []; const api = factory({getSubagents:id=>{calls.push(id);return [{id:"child"}];},
    getAsyncTasks:id=>{calls.push(id);return [{id:"shell"}];}});
  assert.deepEqual(await api.getSubagents({id:"parent"}), [{id:"child"}]);
  assert.deepEqual(await api.getAsyncTasks({id:"parent"}), [{id:"shell"}]);
  assert.deepEqual(calls, ["parent", "parent"]); assert.equal(api.latest(), 42);
  assert.deepEqual(ports.restored, ["getSubagents", "getAsyncTasks"]);
  assert.throws(() => restoreLocalGatewayReaders(old, latest, plan, ["sendPrompt"]), /Unreviewed/);
  const changed = indexBundle(latest.source.replace("getSubagents:async()=>[]", "getSubagents:async()=>[{id:'remote'}]"));
  assert.throws(() => restoreLocalGatewayReaders(old, changed, plan, ["getSubagents"]), /no longer an empty stub/);
  const oldChanged = indexBundle(old.source.replace("manager.getSubagents(args.id)", "manager.getSubagents(args.name)"));
  assert.throws(() => restoreLocalGatewayReaders(oldChanged, latest, plan, ["getSubagents"]), /contract changed/);
  const wrapped = indexBundle(latest.source.replace("return api;",
    "return wrapGatewayApiWithServerAgentProxy({api,proxy:manager});"));
  const wrappedPort = restoreLocalGatewayReaders(old, wrapped, plan, ["getSubagents", "getAsyncTasks"]);
  assert.match(applyEdits(wrapped.source, wrappedPort.edits), /return wrapGatewayApiWithServerAgentProxy\(\{api,proxy:manager\}\)/);
});

test("manual local routines receive their ID while non-default sessions keep upstream routing", async () => {
  const latest = indexBundle(`// src/gateway.ts\nfunction createHostGatewayApi(manager, upstream) {
    const api = {runAgentAutomationNow: async (args) => {
      const agentId = args.id.toUpperCase();
      if (args.sessionId) return upstream({agentId,localId:args.automationId,sessionId:args.sessionId});
      return manager.runAgentAutomationNow(agentId);
    }, latest:()=>42}; return api;
  }`);
  const ports = restoreLocalGatewayActions(latest, ["runAgentAutomationNow"]);
  const factory = new Function(applyEdits(latest.source, ports.edits) + "\nreturn createHostGatewayApi;")();
  const local = [], remote = [];
  const api = factory({runAgentAutomationNow:async(...args)=>{local.push(args);return "local-run";}},
    async args=>{remote.push(args);return "session-run";});
  assert.equal(await api.runAgentAutomationNow({id:"q",automationId:"routine"}), "local-run");
  assert.deepEqual(local, [["Q", "routine"]]);
  assert.equal(await api.runAgentAutomationNow({id:"q",automationId:"other",sessionId:"thread"}), "session-run");
  assert.deepEqual(remote, [{agentId:"Q",localId:"other",sessionId:"thread"}]);
  assert.equal(api.latest(),42); assert.equal(local.length,1);
  assert.deepEqual(restoreLocalGatewayActions(latest).edits, []);
  assert.throws(() => restoreLocalGatewayActions(latest, ["sendPrompt"]), /Unreviewed/);
  const changed = indexBundle(latest.source.replace("runAgentAutomationNow(agentId)",
    "runAgentAutomationNow(agentId, args.automationId)"));
  assert.throws(() => restoreLocalGatewayActions(changed, ["runAgentAutomationNow"]), /contract changed/);
  const changedId = indexBundle(latest.source.replace("runAgentAutomationNow(agentId)",
    "runAgentAutomationNow(args.id)"));
  assert.throws(() => restoreLocalGatewayActions(changedId, ["runAgentAutomationNow"]), /contract changed/);
});

test("live BOX voice relays reach the local runner before hangup and preserve server relays", async () => {
  const latest = indexBundle(`// src/gateway.ts\nfunction createHostGatewayApi(manager, proxy) {
    const voiceRelaysInFlight = new Set();
    const api = {nudgeVoiceCall: async (args) => {
      const proxy = manager.proxy;
      if (!proxy.isProxiedAgent(args.id)) {
        return { kind: "refused", refusal: "agent-unavailable" };
      }
      const key = args.callId;
      if (voiceRelaysInFlight.has(key)) return {kind:"refused"};
      voiceRelaysInFlight.add(key);
      try { return await manager.nudgeVoiceCall({agentId:args.id,callId:args.callId,
        request:args.request,sink:{kind:"server-loop",relay:proxy.relay}}); }
      finally { voiceRelaysInFlight.delete(key); }
    }, latest:()=>42}; return api;
  }`);
  const ports = restoreLocalGatewayActions(latest, ["nudgeVoiceCall"]);
  const factory = new Function(applyEdits(latest.source, ports.edits) + "\nreturn createHostGatewayApi;")();
  let onTheLine = true;
  const relays = [];
  const manager = {proxy:{isProxiedAgent:id=>id==="remote",relay:()=>{}},
    nudgeVoiceCall:async args=>{assert.equal(onTheLine,true);relays.push(args);return {kind:"accepted"};}};
  const api = factory(manager);
  const request = {id:"box",callId:"live-call",request:"Read the fixture and speak its result"};
  assert.deepEqual(await api.nudgeVoiceCall(request), {kind:"accepted"});
  assert.deepEqual(relays[0], {agentId:"box",callId:"live-call",request:request.request,sink:{kind:"box-loop"}});
  assert.deepEqual(await api.nudgeVoiceCall({...request,id:"remote"}), {kind:"accepted"});
  assert.equal(relays[1].sink.kind,"server-loop");
  assert.equal(relays[1].sink.relay,manager.proxy.relay);
  assert.equal(api.latest(),42);
  onTheLine = false;
  const changed = indexBundle(latest.source.replace('return { kind: "refused", refusal: "agent-unavailable" };','return manager.sendPrompt(args.request);'));
  assert.throws(()=>restoreLocalGatewayActions(changed,["nudgeVoiceCall"]),/contract changed/);
  const alreadyLocal = indexBundle(applyEdits(latest.source,ports.edits));
  assert.throws(()=>restoreLocalGatewayActions(alreadyLocal,["nudgeVoiceCall"]),/contract changed/);
});

test("the unused copied experiment reader cannot confuse the canonical live gate patch", () => {
  const legacy = `var __openbot_box_SandExperimentServiceCore = class {
    checkFeatureGate(name, options) { return true; }
  };`;
  const result = retireUnusedSharedFeatureReader(legacy);
  assert.match(result, /retainedFeatureGate\(name, options\)/);
  assert.doesNotMatch(result, /checkFeatureGate\(/);
  assert.throws(() => retireUnusedSharedFeatureReader(legacy +
    "\nnew __openbot_box_SandExperimentServiceCore();"), /still live/);
  assert.throws(() => retireUnusedSharedFeatureReader(legacy.replace("checkFeatureGate", "otherGate")),
    /interface changed/);
  assert.equal(retireUnusedSharedFeatureReader("var latest = 1;"), "var latest = 1;");
  const marked = legacy.replace("return true;", "return false; /* openbot-force-local-infer */");
  assert.doesNotMatch(retireUnusedSharedFeatureReader(marked), /openbot-force-local-infer/);
  assert.match(retireUnusedSharedFeatureReader(marked), /openbot-retained-feature-reader/);
});

test("restored experiment readers retain registered parsers and control fallbacks", () => {
  const old = indexBundle(`// src/catalog.ts\nfunction parseBoolean(value) {return value === true;}\nvar EXPERIMENTS = {removed:{client:true, fallbackValues:{enabled:false},parseValue:{enabled:parseBoolean}}};`);
  const latest = indexBundle(`// src/catalog.ts\nfunction parseBoolean7(value) {return value === true;}\nvar EXPERIMENTS = {current:{}};`);
  const plan = planCompatibility(old, latest, [], ["src/catalog.ts"]);
  const source = applyEdits(latest.source, restoreLocalExperimentEntries(old, latest, plan, ["removed"]));
  const catalog = Function(source + "; return EXPERIMENTS;")();
  assert.deepEqual(catalog.removed.fallbackValues, {enabled:false});
  assert.equal(catalog.removed.parseValue.enabled(true), true);
  assert.equal(catalog.removed.parseValue.enabled("true"), false);
  assert.deepEqual(catalog.current, {});
  assert.throws(() => restoreLocalExperimentEntries(old, indexBundle(source), plan, ["removed"]), /catalog changed/);
  assert.throws(() => restoreLocalExperimentEntries(old, latest, plan, ["unknown"]), /catalog changed/);
});

test("the modern subagent tool receives the local turn and its active execution resources", () => {
  const args = {config:{id:"active"},toolSession:{id:"session"},summarizationHandler:{},props:{}};
  const turn = {executorProfileSelectionEnabled:true,subagentLaunchPrefetch:{},other:7};
  const result = localTaskArguments(args, turn);
  assert.equal(result.turn.config,args.config);
  assert.equal(result.turn.toolSession,args.toolSession);
  assert.equal(result.turn.summarizationHandler,args.summarizationHandler);
  assert.equal(result.turn.executorProfileSelectionEnabled,true);
  assert.equal(result.turn.subagentLaunchPrefetch,turn.subagentLaunchPrefetch);
  assert.equal(result.props,args.props);
  assert.equal(turn.config,undefined);
});

test("removed lazy state gets initialized even when upstream removed the corresponding call", () => {
  const old = indexBundle(`// src/locale.ts\nvar PATTERN; var init_locale = __esm({locale(){PATTERN = /en/;}});\nfunction valid(value) { return PATTERN.test(value); }\n// src/runner.ts\nfunction run(value) { return valid(value); }`);
  const plan = planCompatibility(old, indexBundle(""), ["src/runner.ts"]);
  assert.deepEqual(plan.initializers, ["__openbot_box_init_locale"]);
  const latest = indexBundle(`// src/locale.ts\nvar PATTERN; var init_locale = __esm({locale(){PATTERN = /en/;}}); function valid(value) {return true;}`);
  assert.deepEqual(planCompatibility(old, latest, ["src/runner.ts"], ["src/locale.ts"]).initializers, []);
});

test("concurrent duplicate peer messages deliver and wake the local receiver once", async () => {
  let calls = 0, finish;
  class Peer { async receiveFromPeer() { calls++; await new Promise((r) => { finish = r; }); return "delivered"; } }
  installPeerDeliveryCoalescing(Peer);
  const peer = new Peer(), args = {toAgentId:"q", messageId:"one"};
  const a = peer.receiveFromPeer(args), b = peer.receiveFromPeer(args);
  await Promise.resolve(); assert.equal(calls, 1); finish();
  assert.deepEqual(await Promise.all([a, b]), ["delivered", "delivered"]);
});

test("new and retained runner gates coexist without overriding live policy readers", () => {
  let policy=false;
  const live=()=>policy;
  const compose=(wirings)=>Object.fromEntries(['newTool','shared'].map((name)=>[name,
    typeof wirings[name]==='function'?wirings[name]:()=>wirings[name].value]));
  const gates=composeLocalRunnerGates({shared:live,oldTool:live},{newTool:false,shared:false},compose,{newTool:false});
  assert.equal(gates.newTool(),false);assert.equal(gates.shared(),false);policy=true;
  assert.equal(gates.shared(),true);assert.equal(gates.oldTool(),true);
  assert.throws(()=>composeLocalRunnerGates({}, {unexpected:false},compose,{newTool:false}),/Unreviewed/);
});

test("removed native context readers leave the retained context's defaults available", () => {
  const timezone=()=>"America/Chicago",base={shell:"/bin/bash",resolveUserTimeZone:timezone};
  const options=localRequestContextOptions(base,{},{});
  assert.deepEqual(options,base);
  assert.equal(Object.hasOwn(options,"resolveRules"),false);
  assert.equal(Object.hasOwn(options,"resolveUserFullName"),false);
  assert.equal(options.resolveUserTimeZone,timezone);
  assert.throws(()=>localRequestContextOptions(base,null,{}),/Missing native context/);
});

test("compatible native context readers keep live data and the actual service receiver", async () => {
  const auth={name:"First",getUserFullName(){return this.name;}};
  const setup={rules:["first"],async resolveTeamRules(){return this.rules;}};
  const options=localRequestContextOptions({},auth,setup);
  auth.name="Second";setup.rules=["second"];
  assert.equal(options.resolveUserFullName(),"Second");
  assert.deepEqual(await options.resolveRules(),["second"]);
  assert.throws(()=>localRequestContextOptions({}, {getUserFullName:"invalid"}, setup),/Invalid native context reader/);
  assert.throws(()=>localRequestContextOptions({},auth,{resolveTeamRules:false}),/Invalid native context reader/);
});

test("peer failures release the claim and unrelated IDs and receivers stay independent", async () => {
  let calls = 0;
  class Peer { async receiveFromPeer() { if (++calls === 1) throw new Error("temporary"); return "delivered"; } }
  installPeerDeliveryCoalescing(Peer);
  const peer = new Peer(), args = {toAgentId:"q", messageId:"one"};
  await assert.rejects(peer.receiveFromPeer(args), /temporary/);
  assert.equal(await peer.receiveFromPeer(args), "delivered");
  await Promise.all([peer.receiveFromPeer({...args,messageId:"two"}),new Peer().receiveFromPeer(args)]);
  assert.equal(calls, 4);
  assert.throws(() => installPeerDeliveryCoalescing(Peer), /Unexpected/);
});

test("startup readiness waits for the entire native start and preserves its result", async () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "openbot-startup-")), file = join(dir, "state.json");
  try {
    let finish;
    class Host { async start(value) { await new Promise((r) => { finish = r; }); return value; } }
    installLocalEngineStartupTrace(Host, fs, file);
    const pending = new Host().start("native result");
    assert.equal(JSON.parse(fs.readFileSync(file)).phase, "starting");
    finish(); assert.equal(await pending, "native result");
    const state = JSON.parse(fs.readFileSync(file));
    assert.equal(state.phase, "ready"); assert.equal(state.pid, process.pid);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("failed startup preserves the exception and records locations without sensitive text", async () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "openbot-startup-")), file = join(dir, "state.json");
  try {
    const error = new TypeError("private credential must not be recorded");
    error.stack += "\n    at boot (/opt/sand/sand-host/host-main.cjs:123:45)";
    class Host { async start() { throw error; } }
    installLocalEngineStartupTrace(Host, fs, file);
    await assert.rejects(new Host().start(), (e) => e === error);
    const raw = fs.readFileSync(file, "utf8"), state = JSON.parse(raw);
    assert.equal(state.phase, "failed"); assert.equal(state.errorClass, "TypeError");
    assert.deepEqual(state.frames, [{line:123,column:45}]);
    assert.equal(raw.includes("private credential"), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("an unwritable or symlinked startup trace cannot change native startup behavior", async () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "openbot-startup-")), file = join(dir, "state.json");
  try {
    const target = join(dir, "target"); fs.writeFileSync(target, "do not overwrite"); fs.symlinkSync(target, file);
    class Host { async start() { return 1; } }
    installLocalEngineStartupTrace(Host, fs, file);
    assert.equal(await new Host().start(), 1);
    assert.equal(fs.readFileSync(target, "utf8"), "do not overwrite");
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("local sends preserve nonce, connector scope, threading and provenance without waiting for a full turn", () => {
  const args = { agentId: "q", clientNonce: "stable-nonce", richText: {text:"text"}, replyToId: "reply",
    isFork: true, automationWriteProvenance: {source:"owner"}, traceparent: "trace",
    enterEpochMs: 1, composedAtMs: 2, mcpConfigJson: "{\"servers\":[\"one\"]}", machineId: "desktop",
    attachmentPaths: ["/tmp/file"], attachmentNames: ["file"], directAddressedAcceptance: true };
  assert.deepEqual(localSendOptions(args, {}), {...args, awaitTurn:false});
  assert.equal(localSendOptions(args, {sendAcceptReturnDisabled:true}).awaitTurn, true);
  assert.deepEqual(localSendOptions({}, {}).attachmentPaths, []);
});

test("successful local sends do not write diagnostic files or change the native result", async () => {
  class Pipeline { async sendPrompt(prompt, options) { return {prompt, options}; } }
  const fs = new Proxy({}, {get() { throw new Error("No successful-send I/O"); }});
  installLocalEngineSendTrace(Pipeline, fs, "unused");
  assert.deepEqual(await new Pipeline().sendPrompt("message", {nonce:"one"}),
    {prompt:"message",options:{nonce:"one"}});
});

test("a failed local send retains its original exception and private trace contains no request text", async () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "openbot-send-")), file = join(dir, "state.json");
  try {
    const error = new TypeError("private request must not be recorded");
    error.stack += "\n    at send (/opt/sand/sand-host/host-main.cjs:222:33)";
    class Pipeline { async sendPrompt() { throw error; } }
    installLocalEngineSendTrace(Pipeline, fs, file);
    await assert.rejects(new Pipeline().sendPrompt("private prompt"), (e) => e === error);
    const raw = fs.readFileSync(file, "utf8"), state = JSON.parse(raw);
    assert.equal(state.errorClass, "TypeError"); assert.deepEqual(state.frames, [{line:222,column:33}]);
    assert.equal(raw.includes("private"), false); assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  } finally { fs.rmSync(dir, { recursive:true, force:true }); }
});

test("post-acceptance turn failures are observed without changing settlement or retaining sensitive text", async () => {
  const dir = fs.mkdtempSync(join(tmpdir(), "openbot-turn-")), prefix = join(dir,"turn-");
  const session = {id:"00000000-0000-4000-8000-2cc295c470b1"};
  try {
    const error = new ReferenceError("private prompt and token");
    error.stack += "\n    at run (/opt/sand/sand-host/host-main.cjs:333:44)";
    class Runtime { async runAgentTurn(s, fail) { if (fail) throw error; return {aborted:false,pausedForUpgrade:true}; } }
    installLocalEngineTurnTrace(Runtime,fs,prefix);
    await assert.rejects(new Runtime().runAgentTurn(session,true),(e)=>e===error);
    const file = prefix+session.id+".json", raw = fs.readFileSync(file,"utf8");
    assert.equal(raw.includes("private"),false);
    assert.deepEqual(JSON.parse(raw).frames,[{line:333,column:44}]);
    assert.equal(JSON.parse(raw).phase,"failed");
    assert.deepEqual(await new Runtime().runAgentTurn(session,false),{aborted:false,pausedForUpgrade:true});
    assert.equal(JSON.parse(fs.readFileSync(file)).pausedForUpgrade,true);
    assert.equal(fs.statSync(file).mode & 0o777,0o600);
    await new Runtime().runAgentTurn({id:"../../invalid"},false);
    assert.equal(fs.readdirSync(dir).length,1);
  } finally { fs.rmSync(dir,{recursive:true,force:true}); }
});

test("turn diagnostics retain only a native missing-member name, never the receiver or free-form error", async () => {
  const dir=fs.mkdtempSync(join(tmpdir(),"openbot-turn-interface-")), prefix=join(dir,"turn-");
  const session={id:"00000000-0000-4000-8000-2cc295c470b1"};
  try {
    class Runtime { async runAgentTurn(_session,message) {throw new TypeError(message);} }
    installLocalEngineTurnTrace(Runtime,fs,prefix);
    await assert.rejects(new Runtime().runAgentTurn(session,"privateReceiver.nativeReader is not a function"));
    let raw=fs.readFileSync(prefix+session.id+".json","utf8");
    assert.equal(JSON.parse(raw).missingMember,"nativeReader");assert.equal(raw.includes("privateReceiver"),false);
    for(const message of ["private prompt nativeReader is not a function","token=https://private.example",
      "privateReceiver.secret-key is not a function","nativeReader is not a function"]) {
      await assert.rejects(new Runtime().runAgentTurn(session,message));
      raw=fs.readFileSync(prefix+session.id+".json","utf8");
      assert.equal(JSON.parse(raw).missingMember,undefined);assert.equal(raw.includes("private"),false);
    }
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
