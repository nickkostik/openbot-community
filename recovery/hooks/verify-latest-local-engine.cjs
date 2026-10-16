"use strict";
const fs=require('node:fs'), Module=require('node:module'), path=require('node:path'), crypto=require('node:crypto');
const candidate=process.argv[2], expected=process.argv[3], host=process.argv[4];
if (!candidate || !/^[0-9a-f]{64}$/.test(expected || '') || !path.isAbsolute(host || '')) throw new Error('Expected candidate, digest and native host path');
const fd=fs.openSync(candidate,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
let raw;
try {const info=fs.fstatSync(fd);if(!info.isFile()||info.uid!==process.getuid()||info.size>48*1024*1024)throw new Error('Untrusted candidate');raw=fs.readFileSync(fd);}finally{fs.closeSync(fd);}
if(crypto.createHash('sha256').update(raw).digest('hex')!==expected)throw new Error('Candidate digest changed');
let s=raw.toString('utf8');
const start=s.indexOf('bootSandHost(runHostMain).catch('), end=s.indexOf('/*! Bundled license information:',start);
if(start<0||end<0)throw new Error('No unique boot boundary');
s=s.slice(0,start)+'module.exports = { promptMcpOptions: getUserInfoMcpMetaToolOptions, attachments: createAttachmentsService, imageWrite: writeExecutorResource, imageRead: readExecutorResource, SandHost, gatewayFactory: createHostGatewayApi, runner: __openbot_box_SandAgentRunner, transcript: __openbot_box_transcriptExtension, execution: __openbot_box_TurnExecutionRegistry, runnerFactory: __openbot_box_createHostRunnerComposition, upgrade: __openbot_box_UpgradeRecreateResume, lifecycle: __openbot_box_AgentLifecycle, manager: __openbot_box_TranscriptManager, db: SandAgentDb, memory: MemoryService, memoryStore: FileMemoryStore, mcp: McpHostService, authCompletion: HostMcpAuthCompletion, voicePrompt: MainLoopVoicePrompt, voicePolicy: VoiceSpeakPolicy, telemetry: SandStructuredLogTelemetry, telemetryApi: withAutomationRunAnalytics, noopTelemetry: createNoopSandTelemetry, relaySource: createBackendRelaySources, browserArmDeps: sandBrowserArmDeps, browserRunCodeDeps: playwrightRunCodeToolDeps, composeGates: __openbot_box_composeBoxRunnerGates, subagentGates: __openbot_box_pickSubagentGates, taskTool: createSandTaskTool, experiments: SandExperimentServiceCore, experimentRegistry: SAND_EXPERIMENT_REGISTRY, inference: typeof __openbot_box_inferenceExtension === "undefined" ? inferenceExtension : __openbot_box_inferenceExtension };\n'+s.slice(end);
const permissionModule='// src/shared/mcp/mcp-tool-permissions.ts';
const permissionBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,permissionBoundary)+'module.exports.mcpPermissions = typeof effectiveMcpToolPermission === "undefined" ? null : { effective: effectiveMcpToolPermission, scope: mcpToolPermissionScope, withPolicy: withMcpToolPermissionPolicy, readAccess: isReadForToolPermissions, management: SandMcpInstructionsAndToggles, settings: SettingsService };\n'+s.slice(permissionBoundary);
const contextBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,contextBoundary)+'module.exports.requestContext = __openbot_box_createHostRequestContext;\n'+s.slice(contextBoundary);
const credentialBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,credentialBoundary)+'module.exports.credentials = { coordinator: CredentialCoordinator, factory: startCredentialProvider };\n'+s.slice(credentialBoundary);
const reviewBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,reviewBoundary)+'module.exports.autoReview = typeof __openbot_box_autoReviewExtension === "undefined" ? autoReviewExtension : __openbot_box_autoReviewExtension;\n'+s.slice(reviewBoundary);
const policyBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,policyBoundary)+'module.exports.teamPolicy = typeof __openbot_box_createTeamAdminPolicyService === "undefined" ? createTeamAdminPolicyService : __openbot_box_createTeamAdminPolicyService; module.exports.nativeClock = realClock;\n'+s.slice(policyBoundary);
const formBoundary=s.indexOf('/*! Bundled license information:');
s=s.slice(0,formBoundary)+'module.exports.localForms = {extension: typeof __openbot_box_sessionExtension === "undefined" ? sessionExtension : __openbot_box_sessionExtension, service: typeof __openbot_box_UserFormService === "undefined" ? UserFormService : __openbot_box_UserFormService};\n'+s.slice(formBoundary);
const mod=new Module(process.argv[2]);mod.filename=path.join(path.dirname(host),'openbot-offline-check.cjs');mod.paths=Module._nodeModulePaths(path.dirname(host));
async function check() {
  const assert=require('node:assert/strict');
  mod._compile(s,mod.filename); const x=mod.exports;const reg=new x.execution();
  assert.equal(reg.canExecute,false);assert.equal(await reg.isRunReady(),false);
  const calls=[];const executor={isInferenceReady:async()=>true,createRunner:(...a)=>{calls.push(a);return 'runner';},createGroupMemberRunner:(...a)=>{calls.push(a);return 'group';}};
  reg.bindExecutor(executor);assert.equal(await reg.isRunReady(),true);
  reg.setLocalWorkAllowed(false);assert.equal(await reg.isRunReady(),false);reg.setLocalWorkAllowed(true);
  assert.equal(reg.createRunner('session','hooks'),'runner');assert.equal(reg.createGroupMemberRunner('session','hooks','options'),'group');
  assert.deepEqual(calls,[['session','hooks'],['session','hooks','options']]);assert.throws(()=>reg.bindExecutor(executor));
  const deleted=new Set(['restored']);x.lifecycle.prototype.forgetAgentDeletion.call({tm:{sessions:{deletedAgentIds:deleted}}},'restored');assert.equal(deleted.size,0);
  let forwarded=false;x.manager.prototype.forgetAgentDeletion.call({agentLifecycle:{forgetAgentDeletion:id=>{assert.equal(id,'restored');forwarded=true;}}},'restored');assert.equal(forwarded,true);
  assert.equal(typeof x.upgrade.prototype.runningTurns,'function');
  const environment={backend:{backendUrl:'https://api2.cursor.sh'},agentMockResponse:'offline'};
  const auth={peekAccessToken:()=>null,getAccessToken:async()=>'',getGrokBotToken:async()=>'',getTeamId:()=>undefined,getMachineId:()=> 'offline'};
  const inference=await x.inference.start({host:{environment},deps:{auth,experiments:{},settings:{}}});
  assert.equal(await inference.isReady(),true);environment.agentMockResponse=null;assert.equal(await inference.isReady(),false);
  for(const name of ['createWebSearch','createWebFetch','transcribeAudio','previewVoice'])assert.equal(typeof inference[name],'function');
  assert.ok(inference.createWebSearch({modelId:'offline'}));assert.ok(inference.createWebFetch({}));
  const experiments={registry:x.experimentRegistry,hasFreshAuthenticatedExperimentBootstrap:true,
    experimentOverrideStore:{read:()=>undefined},flushExposureLog:async()=>{},
    client:{getExperiment:(_key,parse)=>({groupName:'enabled',value:parse({enabled:true})})}};
  experiments.readExperiment=x.experiments.prototype.readExperiment.bind(experiments);
  experiments.offerEnabledArm=x.experiments.prototype.offerEnabledArm.bind(experiments);
  for(const name of ['offerUpdateStateSplit','offerVoiceSkill']){
    const key=x.experiments.prototype[name].call({offerEnabledArm:key=>key});
    assert.equal(x.experimentRegistry.EXPERIMENTS[key].fallbackValues.enabled,false);
    assert.equal(x.experiments.prototype[name].call(experiments),true);
  }
  const tool=x.taskTool({props:{resourceAccessor:{get:()=>undefined},stateHandler:{}},
    turn:{config:{},toolSession:{},executorProfileSelectionEnabled:false},
    remoteBoxResourceAccessor:{get:()=>undefined},subagentConfigs:[],subagentModels:[],
    gates:{executorForkContext:()=>false,taskSubagentRoster:()=>false},toolName:'Subagent'});
  assert.ok(tool);assert.equal(typeof tool.execute,'function');
  const legacyRegistry={getMcpDescriptor:()=>({serverIdentifier:'fixture-builtin',instructions:'fixture'}),listsFirstInCatalog:()=>false};
  const toolSet={getStaticTools:()=>[{dynamicToolMetaRole:'discovery',mcpSnapshotDescriptors:[]}],
    getStaticTool:name=>({name}),getDynamicToolRegistry:()=>undefined,getBuiltinDynamicToolRegistry:()=>legacyRegistry};
  assert.ok(x.promptMcpOptions(undefined,[],toolSet));
  legacyRegistry.getExternalCatalogOptions=()=>undefined;
  assert.ok(x.promptMcpOptions(undefined,[],toolSet));
  for(const method of ['kickstartIfPending','requestDiskSaverAudit','requireRunnerComposition','prepareForUpgrade'])assert.equal(typeof x.SandHost.prototype[method],'function');
  const credentialLoads=[];
  const credentialCoordinator=new x.credentials.coordinator({
    loadDirectory:async mode=>{credentialLoads.push(mode);return {items:[]};}});
  const credentialAccess=credentialCoordinator.createAccess(async()=>0);
  assert.equal(await credentialAccess.isConnected(),true);
  assert.deepEqual(await credentialAccess.list(),[]);
  const missingCredential=await credentialAccess.resolveBrowserTarget({
    credentialId:'missing',connectionId:'missing',catalogRevision:'missing'},'https://example.invalid');
  assert.equal(missingCredential.ok,false);
  assert.ok(credentialLoads.length>0);
  for(const exportName of ['accessForAgentWindow','status','agentToolLease'])
    assert.ok(new RegExp('\\b'+exportName+'\\s*:').test(x.credentials.factory.toString()));
  const policyClock=x.nativeClock;
  const teamPolicy=x.teamPolicy({clock:policyClock,getTeamId:()=>null,log:()=>{},
    getDashboardClient:()=>({getTeamAdminSettingsOrEmptyIfNotInTeam:async()=>({})})});
  assert.equal(await teamPolicy.waitForPolicy(),true);
  assert.equal(teamPolicy.isAutoReviewEnforced(),false);
  const enforcedTeamPolicy=x.teamPolicy({clock:policyClock,getTeamId:()=>1,log:()=>{},
    getDashboardClient:()=>({getTeamAdminSettingsOrEmptyIfNotInTeam:async()=>({
      sandAutoReviewControls:{enforceEnabled:true},backgroundAgentSettings:{disableCloudAgentsInSand:true}})})});
  assert.equal(await enforcedTeamPolicy.waitForPolicy(),true);
  assert.equal(enforcedTeamPolicy.isAutoReviewEnforced(),true);
  assert.equal(enforcedTeamPolicy.isCloudAgentsDisabled(),true);
  const approvalStops=[];let reviewEnabled=false;
  const reviewService=await x.autoReview.start({host:{environment},onStop:fn=>approvalStops.push(fn),
    deps:{auth,experiments:{checkFeatureGate:()=>true},
      settings:{getAutoReviewInstructions:()=>({isEnabled:reviewEnabled})},
      'team-admin-policy':teamPolicy,
      telemetry:{logs:{}},transcript:{createAwaitingStateSink:()=>({clearForTab:()=>{}}),
        listAgentIds:async()=>[],expireAllPendingAutoReviewApprovalCards:async()=>{}}}});
  const reviewBinding=reviewService.bindRunner({agentId:'offline',approvalsResolvable:true,onUpdate:()=>{}});
  assert.equal(reviewBinding.autoReviewModes.hostShell,'off');
  reviewEnabled=true;assert.equal(reviewBinding.getAutoReviewModes().hostShell,'enforce');
  assert.deepEqual(reviewService.agentIdsWithPendingApprovals(),[]);
  assert.equal(reviewService.pendingAwaitingState('offline'),null);
  assert.ok(reviewBinding.autoReviewController);
  for(const stop of approvalStops)stop();
  teamPolicy.dispose();enforcedTeamPolicy.dispose();
  for(const name of ['startUserForm','endUserForm','hasUserFormRemapHold','remapUserFormTargets',
    'startVirtualCard','pendingVirtualCard','forgetVirtualCard'])
    assert.ok(new RegExp('\\b'+name+'\\s*:').test(x.localForms.extension.start.toString()));
  const forms=new x.localForms.service({clock:policyClock});
  for(const name of ['start','end','fill','holdForRemap','hasRemapHold','remap','settleRemap','forget'])
    assert.equal(typeof forms[name],'function');
  assert.equal(forms.hasRemapHold('offline'),false);
  assert.deepEqual(await forms.remap({agentId:'offline',targets:[]}),{kind:'no_hold'});
  assert.equal(forms.holdForRemap('offline',{requestId:'fixture',values:[],consentedHost:'fixture.invalid'}),true);
  assert.equal(forms.hasRemapHold('offline'),true);
  assert.equal(forms.holdForRemap('offline',{requestId:'duplicate'}),false);
  assert.deepEqual(await forms.remap({agentId:'offline',targets:[{fieldId:'unknown'}]}),
    {kind:'unknown_fields',heldFieldIds:[],unknownFieldIds:['unknown']});
  assert.deepEqual(forms.settleRemap({agentId:'offline',requestId:'wrong'}),{kind:'not_remapped'});
  assert.equal(forms.hasRemapHold('offline'),true);
  assert.deepEqual(forms.settleRemap({agentId:'offline',requestId:'fixture'}),{kind:'not_remapped'});
  assert.equal(forms.hasRemapHold('offline'),false);
  assert.equal(forms.holdForRemap('offline',{requestId:'second',values:[]}),true);
  forms.forget('offline');assert.equal(forms.hasRemapHold('offline'),false);
  const transcriptHealth=new Proxy({liveRunningAgentIds:()=>new Set(['running'])},
    {get:(target,name)=>target[name]??(()=>false)});
  const approvalHealth={hostExtensions:{api:()=>({})},transcript:transcriptHealth};
  if(s.includes('typeof autoReview.agentIdsWithPendingApprovals !== "function"')) {
    assert.equal(x.SandHost.prototype.allRunningAgentsAwaitApproval.call(approvalHealth),false);
    const health={...approvalHealth,rosterBookkeeping:{isBusy:true,latestActiveAgentId:'running'},
      allRunningAgentsAwaitApproval:()=>x.SandHost.prototype.allRunningAgentsAwaitApproval.call(approvalHealth)};
    const result=x.SandHost.prototype.getHealth.call(health);
    assert.equal(result.isBusy,true);assert.equal(result.busyOnlyAwaitingApproval,false);
    approvalHealth.hostExtensions.api=()=>({agentIdsWithPendingApprovals:()=>['running']});
    assert.equal(x.SandHost.prototype.allRunningAgentsAwaitApproval.call(approvalHealth),true);
  }
  const dir=fs.mkdtempSync(require('node:path').join(require('node:os').tmpdir(),'openbot-db-compat-'));
  const dbPath=require('node:path').join(dir,'00000000-0000-4000-8000-000000000000','store.db');
  try {
    const attachments=x.attachments({environment,auth});
    for(const name of ['ingestBytes','persistImageBytes','readVideoBytes','resolveOwnerDir',
      'createGenerateImageResourceAccessor','createGenerateImageService']) assert.equal(typeof attachments[name],'function');
    assert.equal(typeof attachments.createGenerateImageService({persistImage:async()=>null}),'function');
    const imageAccessor=attachments.createGenerateImageResourceAccessor(dir);
    const imageWrite=imageAccessor.get(x.imageWrite), imageRead=imageAccessor.get(x.imageRead);
    const imagePath=path.join(dir,'assets','offline.png'), outside=path.join(dir,'outside.png');
    assert.equal((await imageWrite.execute({}, {path:outside,fileBytes:Buffer.from('refused')})).result.case,'error');
    assert.equal(fs.existsSync(outside),false);
    assert.equal((await imageWrite.execute({}, {path:imagePath,fileBytes:Buffer.from('fixture image')})).result.case,'success');
    assert.equal(fs.readFileSync(imagePath,'utf8'),'fixture image');
    assert.equal((await imageRead.execute({}, {path:outside})).result.case,'error');
    const latestContext=x.requestContext({transcriptsFolder:dir,shell:'/bin/bash',resolveUserTimeZone:()=> 'America/Chicago'});
    assert.equal(latestContext.resolve().timeZone,'America/Chicago');
    assert.equal(latestContext.resolve().transcriptsFolder,dir);
    assert.equal(latestContext.resolve().userFullName,undefined);
    assert.deepEqual(await latestContext.resolveRules(),[]);
    const legacyContext=x.requestContext({transcriptsFolder:dir,shell:'/bin/bash',
      resolveUserFullName:()=> 'Fixture Owner',resolveRules:async()=> ['fixture rule']});
    assert.equal(legacyContext.resolve().userFullName,'Fixture Owner');
    assert.deepEqual(await legacyContext.resolveRules(),['fixture rule']);
    let db=new x.db(dbPath);
    assert.equal(db.hasConnectorScopedSend(),false);db.setConnectorScopedSend(true);
    db.recordRequestId('request-one',42,'prompt','chat');
    db.setIntroductionPending(true,'en');db.setServerIntroductionState('pending');
    db.setOriginBuiltinListed(true);assert.equal(db.addWatchedSlackChannel('One'),true);
    assert.equal(db.addWatchedSlackChannel('one'),false);assert.equal(db.addWatchedSlackChannel('bad\nvalue'),false);
    db.close();db=new x.db(dbPath);
    assert.equal(db.hasConnectorScopedSend(),true);assert.equal(db.getRequestIds().at(-1).id,'request-one');
    assert.equal(db.getIntroductionPending(),true);assert.equal(db.getIntroductionLanguage(),'en');
    assert.equal(db.getOriginBuiltinListed(),true);assert.equal(db.hasWatchedSlackChannel('ONE'),true);
    db.setIntroductionPending(false);assert.equal(db.getIntroductionLanguage(),undefined);
    db.setConnectorScopedSend(false);assert.equal(db.hasConnectorScopedSend(),false);db.close();
    const memory=new x.memory({agentsRootDir:dir});const store=memory.createAgentStore(require('node:path').dirname(dbPath));
    for(const field of ['explicitDir','generatedDir','refreshFile','tombstoneDir','evidenceDir'])assert.equal(typeof store[field],'string');
    assert.equal(store.dreaming.isEnabled(),false);assert.equal(typeof store.applySynthesis,'function');memory.dispose();
    assert.equal(typeof x.voicePrompt.replyNudge({sendTool:'send',address:'call'}),'string');
    assert.equal(typeof x.voicePolicy.theyJustSpoke(),'string');
    const waits=new x.authCompletion({getMcp:()=>({noteAuthCompletedElsewhere:()=> 'q'}),getTranscript:()=>({resumeAfterMcpAuth:()=>{}})});
    assert.equal(typeof waits.waits.take,'function');waits.resolve({serverId:'one',serverName:'one',outcome:'cancelled'});
    for(const method of ['reportAutomationRun','reportAutomationFireDropped','reportMemorySynthesis','reportAutomationAgentGoneRecovered','reportSubagentRevival','reportShellRevival'])assert.equal(typeof x.telemetry.prototype[method],'function');
    const apiNames=['reportSubagentRevival','reportShellRevival','reportAutomationRun','reportAutomationFireDropped','reportAutomationAgentGoneRecovered'];
    const forwarded=[], analyticsEvents=[];
    const brain=x.telemetryApi(new Proxy({}, {get:(_target,name)=>(report)=>forwarded.push([name,report])}),
      {trackEvent:(...args)=>analyticsEvents.push(args)});
    const noop=x.noopTelemetry();
    for(const name of apiNames){
      assert.equal(typeof brain[name],'function');assert.equal(typeof noop[name],'function');
      assert.equal(typeof x.telemetry.prototype[name],'function');
      const report={conversationId:'q',automationId:'offline',trigger:'schedule',outcome:'ok',isGroup:false};
      brain[name](report);noop[name](report);assert.equal(forwarded.at(-1)[0],name);assert.equal(forwarded.at(-1)[1],report);
    }
    assert.equal(analyticsEvents.length,1);assert.equal(analyticsEvents[0][0],'sand.automation.run');
    const relay=x.relaySource({polling:{start:()=>{throw new Error('Unexpected polling start');}},fetchImpl:()=>{throw new Error('Unexpected relay fetch');}});
    assert.equal(typeof relay.requestDrain,'function');relay.requestDrain();
    const browser=x.browserArmDeps(new Proxy({}, {get:()=>()=>false}));
    for(const name of ['playwrightSettleArm','playwrightSnapshotDiffArm','browserCoordinateActionsArm','playwrightPostActionWaitArm']){
      assert.equal(typeof browser[name],'function');browser[name]({logExposure:false});
    }
    assert.equal(Object.hasOwn(x.browserRunCodeDeps({playwright:{}}),'playwrightRuntime'),true);

    let opts,reconciled=false,scope;
    let originListed=false;const catalogReads=[];
    const nativeMcp={mcp:{getTools:async(ctx,config)=>{scope=[ctx,config];return []; }},management:{},plugins:{},
      peekCursorScmBuiltinListed:provider=>{catalogReads.push(provider);return originListed;},
      reconcileBoxServers:async()=>{reconciled=true;}};
    const mcp=new x.mcp({backend:{backendUrl:'https://api2.cursor.sh'},auth:{getTeamId:()=>undefined,getAccessToken:async()=>'',getMachineId:()=> 'test'},foreverBox:{box:{}},settings:{},log:()=>{},createHostMcp:(options)=>{opts=options;return nativeMcp;}});
    assert.deepEqual(await opts.boxServers(),{});
    assert.deepEqual(await mcp.api.mcpForAgent('q').getTools('ctx','scope-json'),[]);
    assert.equal(reconciled,true);assert.deepEqual(scope,['ctx','scope-json']);assert.deepEqual(mcp.boxServersTurn,{ctx:'ctx',agentId:'q'});
    assert.equal(mcp.api.peekCursorScmBuiltinListed('origin'),false);
    originListed=true;assert.equal(mcp.api.peekCursorScmBuiltinListed('origin'),true);
    assert.deepEqual(catalogReads,['origin','origin']);
    if(s.includes(permissionModule)) {
      assert.ok(x.mcpPermissions,'Latest MCP permission API must remain available');
      const p=x.mcpPermissions;
      const agentId='00000000-0000-4000-8000-000000000001';
      const serverId='1',otherId='2';
      const settingsPath=path.join(dir,'settings.json');
      let settings=new p.settings(settingsPath,()=>{});
      const restrictedReview={isEnabled:true,allowInstructions:[],blockInstructions:['fixture restriction']};
      const policy={reads:'always_allow',writes:'needs_approval',tools:{explicit:'blocked',writeAllowed:'always_allow',disabled:'always_allow'}};
      const permissions={scopes:{[p.scope(serverId)]:policy,[p.scope(otherId)]:{writes:'blocked'}}};
      settings.setHostSettings({pinnedAgentIds:[agentId],autoReviewInstructions:restrictedReview,
        mcpDisabledToolsByServerId:{[serverId]:['disabled']}});
      settings.setHostSettings({toolPermissions:permissions});
      settings=new p.settings(settingsPath,()=>{});
      assert.deepEqual(settings.getToolPermissions(),permissions);
      assert.deepEqual(settings.getHostSettings().toolPermissions,permissions);
      assert.deepEqual(settings.getHostSettings().pinnedAgentIds,[agentId]);
      assert.deepEqual(settings.getHostSettings().autoReviewInstructions,restrictedReview);
      assert.equal(p.effective({name:'disabled',isReadOnly:true,isDisabled:true,policy}),'blocked');
      assert.equal(p.effective({name:'explicit',isReadOnly:true,isDisabled:false,policy}),'blocked');
      assert.equal(p.effective({name:'read',isReadOnly:true,isDisabled:false,policy}),'always_allow');
      assert.equal(p.effective({name:'write',isReadOnly:false,isDisabled:false,policy}),'needs_approval');
      assert.equal(p.effective({name:'writeAllowed',isReadOnly:false,isDisabled:false,policy}),'always_allow');
      assert.equal(p.effective({name:'unknown',isReadOnly:false,isDisabled:false}),'auto_review');
      assert.equal(p.readAccess({serverHost:'API.CURSOR.COM/path',toolName:'read',declaresReadOnly:true}),true);
      assert.equal(p.readAccess({serverHost:'untrusted.example',toolName:'read',declaresReadOnly:true}),false);
      assert.equal(p.readAccess({serverHost:'untrusted.example',toolName:'read',declaresReadOnly:false,lookupVerb:()=> 'read'}),true);
      const row={id:serverId,config:{url:'https://api.cursor.com/mcp'},accounts:[]};
      const management=new p.management({settingsStore:()=>settings.store,
        resolveListedDisplayServer:async(id)=>id===serverId?row:undefined,
        getToolsRaw:async()=>['read','write','explicit','disabled'].map(name=>({toolName:name,
          providerIdentifier:'fixture',annotations:{readOnlyHint:name==='read'}}))});
      management.displayRowForIdentifier=()=>row;
      const tools=await management.listServerTools(serverId);
      assert.equal(tools.find(t=>t.name==='read').permission,'always_allow');
      assert.equal(tools.find(t=>t.name==='explicit').permission,'blocked');
      assert.equal(tools.find(t=>t.name==='disabled').permission,'blocked');
      assert.equal(tools.find(t=>t.name==='write').permission,'needs_approval');
      const remaining=p.withPolicy(settings.getToolPermissions(),serverId,{});
      settings.setHostSettings({toolPermissions:remaining});
      settings=new p.settings(settingsPath,()=>{});
      assert.deepEqual(settings.getToolPermissions(),{scopes:{[p.scope(otherId)]:{writes:'blocked'}}});
      assert.deepEqual(settings.getHostSettings().mcpDisabledToolsByServerId,{[serverId]:['disabled']});
    }
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
  const gates=x.composeGates({experiments:new Proxy({}, {get:()=>()=>false}),isOriginBuiltinListed:()=>false,isCloudAgentsDisabledByTeamAdmin:()=>false,isDraftingDisabled:()=>false,isAgentEmailAllowedByTeamAdmin:()=>true});
  for(const name of ['playwrightPostActionWaitQuiet','secureValues','stdioSignInCard','automationTiming','sendMessageDeliveryOwed','turnEndThroughAgent','updateStateSplit'])assert.equal(typeof gates[name],'function');
  assert.equal(gates.playwrightPostActionWaitQuiet(),false);assert.equal(gates.secureValues(),false);
  const child=x.subagentGates(gates);for(const name of Object.keys(gates))assert.equal(typeof child[name],'function');assert.equal(child.sendMessageDeliveryOwed(),true);
  let sent,automation,voice;
  const manager={getActiveAgentId:()=> 'q',listAgentsSync:()=>[{id:'q'}],sendPrompt:async(...a)=>{sent=a;},
    getSubagents:async(id)=>{assert.equal(id,'q');return [{id:'child'}];},
    runAgentAutomationNow:async(...args)=>{automation=args;return 'native-routine';},
    nudgeVoiceCall:async(args)=>{voice=args;return {kind:'accepted'};},
    getAsyncTasks:async(id)=>{assert.equal(id,'q');return [{id:'shell'}];}};
  const telemetry={reportMessageSent:()=>{},analytics:{markActive:()=>{}}};
  const proxy={isProxiedAgent:()=>false};
  const extensions={api:(id)=>id==='transcript'?manager:id==='telemetry'?telemetry:id==='server-agent-proxy'?proxy:{}};
  const api=x.gatewayFactory({extensions,environment:{sendAcceptReturnDisabled:false}});
  assert.deepEqual(await api.getSubagents({id:'q'}),[{id:'child'}]);
  assert.deepEqual(await api.getAsyncTasks({id:'q'}),[{id:'shell'}]);
  assert.equal(await api.runAgentAutomationNow({id:'q',automationId:'routine'}),'native-routine');
  assert.deepEqual(automation,['q','routine']);
  assert.deepEqual(await api.nudgeVoiceCall({id:'q',callId:'live-call',request:'read a file',spokenTurns:['read a file']}),{kind:'accepted'});
  assert.deepEqual(voice,{agentId:'q',callId:'live-call',request:'read a file',sink:{kind:'box-loop'}});
  const input={agentId:'q',prompt:'request',clientNonce:'nonce',replyToId:'root',mcpConfigJson:'{}',machineId:'desktop',attachmentNames:['name']};
  assert.deepEqual(await api.sendPrompt(input),{accepted:true});
  assert.equal(sent[0],'request');assert.equal(sent[1].clientNonce,'nonce');assert.equal(sent[1].replyToId,'root');assert.equal(sent[1].mcpConfigJson,'{}');assert.equal(sent[1].machineId,'desktop');assert.equal(sent[1].awaitTurn,false);assert.deepEqual(sent[1].attachmentNames,['name']);
  console.log(JSON.stringify({loaded:true,executorAndLocalGate:true,gatewayAcceptanceFields:true,manualAutomationDispatch:true,latestDatabasePersistence:true,localMcpScope:true,mcpToolPermissions:s.includes(permissionModule),runnerGateCatalog:true,liveTelemetryAdapterPorts:true,localRelayAndBrowserAdapterPorts:true,memoryAndVoicePorts:true,duplicateExecutorRejected:true,deletedAgentRestore:true,upgradePorts:true,runner:typeof x.runner,transcriptId:x.transcript.id,factory:typeof x.runnerFactory,hostMethods:Object.getOwnPropertyNames(x.SandHost.prototype).length}));
}
check().then(()=>process.exit(0),e=>{console.error(JSON.stringify({loaded:false,errorClass:e.name}));process.exit(1);});
