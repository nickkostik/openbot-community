import hashlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent
def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name.replace('_', '-') + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

engine = load('local_engine')
inject = load('local_engine_inject')
RUNNER = '  async sendPrompt(prompt, options2) {\n' + '    // retained native work\n' * 200 + '\n    return this.tm.turnRuntime.runAgentTurn(prompt, options2);\n  }\n  nextTurnEpoch(session) {}\n  runServerScheduledAutomation(args) {}'
UPDATE = inject.STAGE + '\n    return this.deps.stageUpgrade(source);\n  }\n' + inject.IMAGE + '\n  return environment.boxStore.syncEnabled;\n}'


class LocalEngineTests(unittest.TestCase):
    def configure(self, sd):
        directory = sd / 'openbot-local-engine'; directory.mkdir()
        stock = RUNNER.encode(); wrapped = ('/* openbot-stock-wrap */\n' + RUNNER).encode()
        (directory / 'stock.cjs').write_bytes(stock); (directory / 'wrapped.cjs').write_bytes(wrapped)
        policy = {'schemaVersion':1, 'enabled':True,
                  'stockSha256':hashlib.sha256(stock).hexdigest(), 'wrappedSha256':hashlib.sha256(wrapped).hexdigest(),
                  'clientVersion':'0.67.0'}
        (sd / 'openbot-local-engine.json').write_text(json.dumps(policy))
        return stock, wrapped

    def test_no_pin_does_not_downgrade_a_host(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(engine.select_source(pathlib.Path(root), pathlib.Path('host-main.cjs'), 'CURRENT'), 'CURRENT')

    def test_pinned_wrapped_and_stock_pair_remains_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); stock, wrapped = self.configure(sd)
            self.assertEqual(engine.select_source(sd, sd/'host-main.cjs', 'RETIREMENT'), wrapped.decode())
            self.assertEqual(engine.select_source(sd, sd/'host-main.cjs.pre-openbot', 'RETIREMENT'), stock.decode())
            self.assertFalse(engine.summary(sd)['nativeSendVerified'])

    def test_tampered_engine_is_refused_even_if_it_has_a_runner_marker(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); self.configure(sd)
            (sd/'openbot-local-engine/wrapped.cjs').write_text('/* openbot-stock-wrap */\n' + RUNNER + '\nCHANGED')
            with self.assertRaisesRegex(RuntimeError, 'hash differs'):
                engine.select_source(sd, sd/'host-main.cjs', 'CURRENT')

    def test_removed_runner_is_refused_even_with_a_valid_manifest_hash(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); self.configure(sd)
            replacement = b'/* openbot-stock-wrap */\n  async sendPrompt(prompt, options2) { throw new SandBoxHarnessRetiredError(); }\n  nextTurnEpoch(session) {}'
            (sd/'openbot-local-engine/wrapped.cjs').write_bytes(replacement)
            path = sd/'openbot-local-engine.json'; config = json.loads(path.read_text())
            config['wrappedSha256'] = hashlib.sha256(replacement).hexdigest(); path.write_text(json.dumps(config))
            with self.assertRaisesRegex(RuntimeError, 'execution was removed'):
                engine.select_source(sd, sd/'host-main.cjs', 'CURRENT')

    def test_symlinked_manifest_or_engine_never_selects_external_source(self):
        for manifest in (True, False):
            with tempfile.TemporaryDirectory() as root:
                sd = pathlib.Path(root); self.configure(sd)
                path = sd/'openbot-local-engine.json' if manifest else sd/'openbot-local-engine/wrapped.cjs'
                outside = sd/'elsewhere'; path.rename(outside); path.symlink_to(outside)
                with self.assertRaises(OSError):engine.select_source(sd, sd/'host-main.cjs', 'CURRENT')

    def test_symlinked_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);self.configure(sd);directory=sd/'openbot-local-engine'
            directory.rename(sd/'elsewhere');directory.symlink_to(sd/'elsewhere',target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError,'not symlinked'):engine.select_source(sd,sd/'host-main.cjs','CURRENT')

    def test_foreign_files_and_oversized_artifacts_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root)/'foreign'; path.write_bytes(b'SOURCE')
            with mock.patch.object(engine.os,'getuid',return_value=path.stat().st_uid+1):
                with self.assertRaises(RuntimeError):engine.owned_bytes(path,100)
            with self.assertRaises(RuntimeError):engine.owned_bytes(path,3)

    def test_disabled_pin_accepts_an_explicit_owner_upgrade(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); (sd/'openbot-local-engine.json').write_text('{"schemaVersion":1,"enabled":false}')
            self.assertEqual(engine.select_source(sd,sd/'host-main.cjs','NEW'), 'NEW')

    def test_partial_or_duplicate_upgrade_guards_are_refused(self):
        for source in (UPDATE + inject.MARK, UPDATE + UPDATE):
            with self.assertRaises(RuntimeError):inject.patch(source)

    def test_previous_both_mode_guard_upgrades_to_custom_only(self):
        patched = inject.patch(UPDATE)
        previous = patched.replace(inject.HELPER, inject.BOTH_MODE_HELPER)
        self.assertEqual(inject.patch(previous), patched)

    def test_guard_blocks_bundle_and_image_updates_and_is_idempotent(self):
        result=inject.patch(UPDATE)
        self.assertEqual(inject.patch(result),result)
        self.assertIn(inject.STAGE_GUARD,result);self.assertIn(inject.IMAGE_GUARD,result)
        self.assertIn('openbot-local-only.enable',result);self.assertIn('"custom"',result)

    def test_update_guard_runs_and_only_explicit_local_pin_blocks_upgrades(self):
        source=inject.patch('class Upgrade {\n'+inject.STAGE+'\n    return this.deps.stageUpgrade(source);\n  }\n}\n'+inject.IMAGE+'\n  return environment.boxStore.syncEnabled;\n}')
        runner=r'''import vm from "node:vm"; import assert from "node:assert/strict";
const source = SOURCE;
for (const test of [
  { mode:"custom", flag:true, config:{enabled:true}, blocked:true },
  { mode:"official", flag:true, config:{enabled:true}, blocked:false },
  { mode:"custom", flag:false, config:{enabled:true}, blocked:false },
  { mode:"custom", flag:true, config:{enabled:false}, blocked:false },
  { mode:"custom", flag:true, config:null, blocked:false },
  { mode:"custom", flag:true, config:"corrupt", blocked:true }
]) {
  let stages=0;
  const context={require:()=>({
    existsSync:()=>test.flag,
    readFileSync:(p)=>p.endsWith("openbot-mode") ? test.mode : test.config==="corrupt" ? "{" : JSON.stringify(test.config),
    lstatSync:()=>{if(test.config===null)throw Object.assign(new Error(),{code:"ENOENT"});return {isFile:()=>true,uid:123};}
  }),process:{getuid:()=>123}};
  vm.createContext(context);
  vm.runInContext(source+"\nglobalThis.upgrade=new Upgrade();globalThis.imageAllowed=isImageAutoUpdateEnabled({boxStore:{syncEnabled:true}});",context);
  context.upgrade.deps={stageUpgrade:()=>{stages++;return {ok:true};}};
  const result=await context.upgrade.runHostBundleFetchStage({source:{},localVersion:"old",trigger:"idle-auto-update",forceNow:false});
  assert.equal(stages,test.blocked ? 0 : 1);
  assert.equal(context.imageAllowed,!test.blocked);
  assert.equal(result.ok,!test.blocked);
}
'''.replace('SOURCE',json.dumps(source))
        result=subprocess.run(['node','--input-type=module','-e',runner],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':unittest.main()
