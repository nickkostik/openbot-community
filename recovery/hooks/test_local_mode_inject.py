import importlib.util
import pathlib
import json
import subprocess
import unittest
spec=importlib.util.spec_from_file_location('local_mode',pathlib.Path(__file__).with_name('local-mode-inject.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SOURCE='''const applyReads = (isOn) => {
const getCreationPolicy = async (signal = lifetime.signal) => {
name: "sand-harness-migration-window",
}).run(async (deadlineSignal) => {
function profileHarnessFromJson(parsed2) {
function serializeSandProfileFile(profile, binding = {}) {
    const readHosting = (agentId) => {
...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {}
...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {}
if (!isActive && !includeBlank) {
''' + m.PROXY_STOCK
class LocalModeTests(unittest.TestCase):
    def test_experiment_refresh_cannot_reenable_server_profile_reads(self):
        patched=m.patch(SOURCE);self.assertIn('isOn = false; /* openbot-local-mode: reads',patched)
        self.assertEqual(m.patch(patched),patched)
    def test_ambiguous_read_anchor_is_rejected(self):
        with self.assertRaises(RuntimeError):m.patch(SOURCE+'const applyReads = (isOn) => {')
    def test_creation_is_local_and_upgrade_cutover_is_disabled(self):
        s=m.patch(SOURCE);self.assertIn('temporalCreationEnabled: false',s);self.assertIn('return "disabled"',s)
    def test_legacy_fleet_helpers_upgrade_once_without_repatching_other_guards(self):
        current=m.patch(SOURCE)
        modern_helpers=m.FLEET_HELPERS
        old=current.replace(modern_helpers,m.LEGACY_FLEET_HELPERS)
        self.assertEqual(m.patch(old),current)
        self.assertEqual(m.patch(current),current)
        with self.assertRaisesRegex(RuntimeError,'legacy fleet helper'):
            m.patch(old.replace('const journalPath = root + "openbot-local-migration.json";', 'const journalPath = "unexpected";'))
        with self.assertRaisesRegex(RuntimeError,'Ambiguous'):
            m.patch(current+'/* openbot-local-mode: remint-retirement */')
        self.assertEqual(m.patch(current.replace(modern_helpers,m.LEGACY_REMINT_HELPERS)),current)
    def test_both_mode_and_unconditional_identity_guards_upgrade_atomically(self):
        current = m.patch(SOURCE)
        previous = current.replace(m.MODE_POLICY_STOCK, m.MODE_POLICY_LOCAL)
        previous = previous.replace('if (openbotLocalFleetEnabled()) {', f'if (require("node:fs").existsSync("{m.FLAG}")) {{')
        previous = previous.replace('if (openbotLocalFleetEnabled()) isOn = false;', f'if (require("node:fs").existsSync("{m.FLAG}")) isOn = false;')
        previous = previous.replace('if (openbotLocalFleetEnabled()) return "disabled";', f'if (require("node:fs").existsSync("{m.FLAG}")) return "disabled";')
        self.assertEqual(m.patch(previous), current)

    def test_missing_profile_write_anchor_rejects_the_entire_transform(self):
        with self.assertRaisesRegex(RuntimeError,'profile-write'):
            m.patch(SOURCE.replace('function serializeSandProfileFile(profile, binding = {}) {',''))
    def test_duplicate_fleet_marker_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Ambiguous'):
            m.patch(m.patch(SOURCE)+'/* openbot-local-mode: profile-read */')
    def test_incomplete_roster_policy_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'summary harness anchors'):
            m.patch(SOURCE.replace('...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {}','',1))
        patched=m.patch(SOURCE)
        with self.assertRaisesRegex(RuntimeError,'Incomplete explicit BOX'):
            m.patch(patched.replace('openbot-local-mode: roster-box','partial-roster',1))
    def test_missing_or_ambiguous_blank_roster_filter_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'blank agent summary filter'):
            m.patch(SOURCE.replace('if (!isActive && !includeBlank) {',''))
        with self.assertRaisesRegex(RuntimeError,'blank agent summary filter'):
            m.patch(SOURCE+'if (!isActive && !includeBlank) {')
        with self.assertRaisesRegex(RuntimeError,'Ambiguous bound blank'):
            m.patch(m.patch(SOURCE)+'/* openbot-local-mode: bound-blank-roster */')
    def test_dispatch_requires_the_known_native_predicate(self):
        with self.assertRaisesRegex(RuntimeError,'proxy dispatch predicate'):
            m.patch(SOURCE.replace(m.PROXY_STOCK,''))
        with self.assertRaisesRegex(RuntimeError,'proxy dispatch predicate'):
            m.patch(SOURCE + m.PROXY_STOCK)
        updated=m.patch(SOURCE)
        with self.assertRaisesRegex(RuntimeError,'Incomplete local dispatch'):
            m.patch(updated.replace('if (openbotLocalFleetEnabled()) return false;',
                                    'if (openbotLocalFleetEnabled()) return true;'))
        with self.assertRaisesRegex(RuntimeError,'Incomplete local dispatch'):
            m.patch(updated+'/* openbot-local-mode: local-dispatch */')
    def test_held_and_retired_owners_upgrade_out_of_native_legacy_box_fallback(self):
        current=m.patch(SOURCE)
        old=current.replace(m.HELD_HOSTING,m.LEGACY_HELD_HOSTING)
        self.assertNotEqual(current,old)
        self.assertEqual(m.patch(old),current)
        self.assertEqual(m.patch(current),current)
        with self.assertRaisesRegex(RuntimeError,'Incomplete native routine hold'):
            m.patch(current.replace(m.HELD_HOSTING,m.HELD_HOSTING.replace('harness: null','harness: "box"')))
        with self.assertRaisesRegex(RuntimeError,'Incomplete native routine hold'):
            m.patch(current+m.LEGACY_HELD_HOSTING)
    def test_native_profile_policy_and_retired_routine_ownership(self):
        # Exercise the injected JS with a filesystem stub; no real flags/data are read.
        aid='00000000-0000-4000-8000-43677a3769d2'
        new='00000000-0000-4000-8000-b4965e0b081b'
        # The guards are part of valid native-sized fixtures, not an open-brace test stub.
        source='''const applyReads = (isOn) => {};
const lifetime={signal:null};
const getCreationPolicy = async (signal = lifetime.signal) => {};
({name: "sand-harness-migration-window", run:()=>{}}).run(async (deadlineSignal) => {});
function profileHarnessFromJson(parsed2) {
  return parsed2.harness === "temporal" || parsed2.harness === "box" ? parsed2.harness : null;
}
function serializeSandProfileFile(profile, binding = {}) {
  return JSON.stringify({...profile, ...binding});
}
const sessionBox=false;
    const readHosting = (agentId) => { return {harness:"box",isServerBacked:true,onSessionBox:sessionBox}; };
function isBoxHostedForAutomationSync(hosting) {
  if (hosting.onSessionBox) return false;
  if (hosting.harness !== null) return hosting.harness === "box";
  return !hosting.isServerBacked;
}
function readSandProfileHarness(profilePath) { return profileHarnessFromJson({harness:"temporal"}); }
function minimalAgentSummary() { const profilePath="fixture";return {...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {}}; }
let fixtureEmpty=false, fixtureBound=true, fixtureHidden=false;
function readSandProfileServerId() { return fixtureBound ? "123" : null; }
function buildSummary() {
  const profilePath="fixture",isActive=false,includeBlank=false;
  if (!isActive && !includeBlank) { if (fixtureEmpty) return null; }
  return {...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {},isHiddenFromSidebar:fixtureHidden};
}
''' + '\nconst requiredAgents=new Set(["temporal-agent"]); let refreshes=0;\n' + \
            'function refreshRoster() { refreshes += 1; }\nconst proxy={\n        ' + m.PROXY_STOCK + '\n};'
        injected=m.patch(source)
        test='''const assert=require('node:assert/strict');const vm=require('node:vm');
const files=new Map();
const fs={existsSync:p=>files.has(p),readFileSync:p=>{if(!files.has(p))throw Error('missing');return files.get(p);},
  lstatSync:p=>{if(!files.has(p))throw Object.assign(new Error('missing'),{code:'ENOENT'});return {isFile:()=>true,uid:1000,size:files.get(p).length};}};
const ctx={require:name=>name==='node:fs'?fs:require(name),Object,process:{getuid:()=>1000}};
vm.createContext(ctx);vm.runInContext(SOURCE,ctx);
const root='/home/box/sand-data/';
const evaluate=expression=>vm.runInContext(expression,ctx);
files.set(root+'openbot-local-only.enable','');files.set(root+'openbot-mode','custom');
// Both prompt and automation paths use this predicate. A server Temporal
// roster entry cannot escape to the cloud while explicit local mode is on.
assert.equal(evaluate('proxy.isProxiedAgent("temporal-agent")'),false);
assert.equal(evaluate('refreshes'),0);
assert.equal(evaluate('profileHarnessFromJson({harness:"temporal"})'),'box');
assert.equal(evaluate('profileHarnessFromJson({})'),'box');
assert.equal(evaluate('minimalAgentSummary().harness'),'box');
assert.equal(evaluate('buildSummary().harness'),'box');
assert.deepEqual(JSON.parse(evaluate('serializeSandProfileFile({name:"A"},{harness:"temporal",serverId:"123"})')),{name:'A',harness:'box',serverId:'123'});
files.set(root+'openbot-mode','official');
assert.equal(evaluate('proxy.isProxiedAgent("temporal-agent")'),true);
assert.equal(evaluate('proxy.isProxiedAgent("unlisted-agent")'),false);
assert.equal(evaluate('profileHarnessFromJson({harness:"temporal"})'),'temporal');
assert.equal(evaluate('minimalAgentSummary().harness'),'temporal');
assert.equal(evaluate('buildSummary().harness'),'temporal');
assert.equal(JSON.parse(evaluate('serializeSandProfileFile({name:"A"},{harness:"temporal"})')).harness,'temporal');
files.set(root+'openbot-mode','custom');
evaluate('fixtureEmpty = true');
assert.equal(evaluate('buildSummary().harness'),'box');
evaluate('fixtureHidden = true');
assert.equal(evaluate('buildSummary().isHiddenFromSidebar'),true);
evaluate('fixtureBound = false');
assert.equal(evaluate('buildSummary()'),null);
evaluate('fixtureBound = true');
files.set(root+'openbot-mode','official');
assert.equal(evaluate('buildSummary()'),null);
files.set(root+'openbot-mode','custom');
files.delete(root+'openbot-local-only.enable');
assert.equal(evaluate('proxy.isProxiedAgent("temporal-agent")'),true);
assert.equal(evaluate('buildSummary()'),null);
files.set(root+'openbot-local-only.enable','');
files.set(root+'openbot-local-migration.json',JSON.stringify({finalized:true,entries:[{oldId:OLD,newId:NEW}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),false);
files.set(root+'agents/'+NEW+'/profile.json',JSON.stringify({harness:'box'}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(NEW)+')'),false);
assert.equal(evaluate('readHosting('+JSON.stringify(OLD)+').harness'),null);
assert.equal(evaluate('isBoxHostedForAutomationSync(readHosting('+JSON.stringify(OLD)+'))'),false);
assert.equal(evaluate('readHosting('+JSON.stringify(NEW)+').harness'),'box');
assert.equal(evaluate('isBoxHostedForAutomationSync(readHosting('+JSON.stringify(NEW)+'))'),true);
files.set(root+'openbot-local-migration.json',JSON.stringify({entries:[{oldId:OLD,newId:NEW}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),false);
files.set(root+'openbot-local-migration.json','{');
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
files.set(root+'openbot-local-migration.json',JSON.stringify({entries:[]}));
files.set(root+'openbot-box-remint.json',JSON.stringify({entries:[{oldId:OLD,newId:NEW,phase:'registered-box'}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),false);
files.set(root+'openbot-box-remint.json',JSON.stringify({entries:[{oldId:OLD,newId:NEW,phase:'active'}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(NEW)+')'),false);
files.set(root+'openbot-box-remint.json',JSON.stringify({entries:[],previousGenerations:[{entries:[{oldId:OLD,newId:NEW,phase:'active'}]}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
files.set(root+'openbot-box-remint.json',JSON.stringify({entries:[],previousGenerations:[{}]}));
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
files.set(root+'openbot-box-remint.json','{');
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),true);
files.set(root+'openbot-box-remint.json',JSON.stringify({entries:[]}));
files.set(root+'openbot-routine-holds.json',JSON.stringify({heldAgentIds:[OLD]}));
assert.equal(evaluate('readHosting('+JSON.stringify(OLD)+').harness'),null);
assert.equal(evaluate('isBoxHostedForAutomationSync(readHosting('+JSON.stringify(OLD)+'))'),false);
assert.equal(evaluate('readHosting('+JSON.stringify(NEW)+').harness'),'box');
files.delete(root+'openbot-routine-holds.json');
files.set(root+'openbot-server-roster.alert.json',JSON.stringify({driftAgentIds:[NEW]}));
assert.equal(evaluate('readHosting('+JSON.stringify(NEW)+').harness'),null);
assert.equal(evaluate('isBoxHostedForAutomationSync(readHosting('+JSON.stringify(NEW)+'))'),false);
assert.equal(evaluate('readHosting('+JSON.stringify(OLD)+').harness'),'box');
files.set(root+'openbot-server-roster.alert.json','{');
assert.equal(evaluate('readHosting('+JSON.stringify(NEW)+').harness'),null);
assert.equal(evaluate('isBoxHostedForAutomationSync(readHosting('+JSON.stringify(NEW)+'))'),false);
files.delete(root+'openbot-server-roster.alert.json');
assert.equal(evaluate('readHosting('+JSON.stringify(NEW)+').harness'),'box');
files.delete(root+'openbot-local-only.enable');
assert.equal(evaluate('openbotRetiredRoutineOwner('+JSON.stringify(OLD)+')'),false);
assert.equal(evaluate('profileHarnessFromJson({harness:"temporal"})'),'temporal');
'''
        script='const SOURCE='+json.dumps(injected)+';const OLD='+json.dumps(aid)+';const NEW='+json.dumps(new)+';\n'+test
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main()
