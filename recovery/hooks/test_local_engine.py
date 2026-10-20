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
WORKER = 'const syntheticNativeWorker = true;\n'
BOUNDED_WORKER = WORKER + 'const syntheticConnectionLimit = 8;\n'


def worker_transform(source):
    if source in (WORKER, BOUNDED_WORKER):
        return BOUNDED_WORKER
    raise RuntimeError('Unreviewed synthetic worker')


class ContentSearchSidecarTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.vendor = self.root / 'vendor-worker.cjs'
        self.vendor.write_text(WORKER)

    def capture(self, **kwargs):
        return engine.optional_content_search_worker(self.root, self.vendor,
            worker_transform, lambda source: None, **kwargs)

    def test_plan_names_exact_current_source_and_output_without_creating_an_asset(self):
        before = self.vendor.read_bytes()
        asset = self.capture()
        self.assertEqual(asset['sourceSha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(asset['sha256'], hashlib.sha256(BOUNDED_WORKER.encode()).hexdigest())
        self.assertEqual(asset['schemaVersion'], 1)
        self.assertFalse(pathlib.Path(asset['path']).exists())
        self.assertFalse((self.root / 'openbot-local-engine').exists())
        self.assertEqual(self.vendor.read_bytes(), before)

    def test_staging_is_immutable_idempotent_and_leaves_vendor_untouched(self):
        asset = self.capture(stage=True)
        path = pathlib.Path(asset['path'])
        self.assertEqual(path, self.root / 'openbot-local-engine/sidecars' / asset['sha256'] / 'search-index-worker.cjs')
        self.assertEqual(path.read_text(), BOUNDED_WORKER)
        self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        inode = path.stat().st_ino
        self.assertEqual(self.capture(stage=True), asset)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(self.vendor.read_text(), WORKER)

    def test_unknown_missing_oversized_and_symlinked_vendor_are_optional(self):
        self.vendor.write_text('a new unsupported vendor worker')
        self.assertIsNone(self.capture(stage=True))
        self.vendor.write_bytes(b'x' * (engine.MAX_CONTENT_SEARCH_WORKER_BYTES + 1))
        self.assertIsNone(self.capture(stage=True))
        self.assertIsNone(engine.content_search_worker_source_sha256(self.vendor))
        self.vendor.unlink()
        self.assertIsNone(self.capture(stage=True))
        target = self.root / 'actual.cjs'; target.write_text(WORKER)
        self.vendor.symlink_to(target)
        self.assertIsNone(self.capture(stage=True))
        self.assertFalse((self.root / 'openbot-local-engine').exists())

    def test_corrupt_or_symlinked_asset_never_gets_overwritten(self):
        asset = self.capture(stage=True); path = pathlib.Path(asset['path'])
        path.chmod(0o600); path.write_text('changed by owner')
        self.assertIsNone(self.capture(stage=True))
        self.assertEqual(path.read_text(), 'changed by owner')
        path.unlink(); path.symlink_to(self.vendor)
        self.assertIsNone(self.capture(stage=True))
        self.assertTrue(path.is_symlink())
        self.assertEqual(self.vendor.read_text(), WORKER)

    def test_sidecar_parent_symlink_and_failed_validation_leave_vendor_authoritative(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / 'openbot-local-engine').symlink_to(outside, target_is_directory=True)
        self.assertIsNone(self.capture(stage=True))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertIsNone(engine.optional_content_search_worker(self.root, self.vendor, worker_transform,
            lambda _: (_ for _ in ()).throw(RuntimeError('syntax failed')), stage=True))
        self.assertEqual(self.vendor.read_text(), WORKER)

    def test_source_identity_tracks_current_vendor_without_selecting_an_old_asset(self):
        before = engine.content_search_worker_source_sha256(self.vendor)
        self.capture(stage=True)
        self.vendor.write_text('new and unreviewed source')
        self.assertNotEqual(engine.content_search_worker_source_sha256(self.vendor), before)
        self.assertIsNone(self.capture(stage=True))

    def test_watch_fast_path_has_no_process_or_writes_and_requires_exact_current_asset(self):
        asset = self.capture(stage=True)
        path = pathlib.Path(asset['path']); before = path.stat()
        with mock.patch.object(engine.subprocess, 'run', side_effect=AssertionError('watch spawned syntax process')), \
                mock.patch.object(engine, '_stage_content_search_sidecar', side_effect=AssertionError('watch wrote source')):
            self.assertTrue(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                               transform=worker_transform))
        self.assertEqual(path.stat().st_ino, before.st_ino)
        for bad in (None, {}, {**asset, 'path': str(self.vendor)}, {**asset, 'sha256': 'a' * 64}):
            self.assertFalse(engine.content_search_worker_current(self.root, bad, self.vendor,
                                                                transform=worker_transform))
        path.unlink()
        self.assertFalse(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                            transform=worker_transform))
        self.assertEqual(self.capture(stage=True), asset)
        self.assertTrue(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                           transform=worker_transform))
        path.chmod(0o600); path.write_text('corrupt asset')
        self.assertFalse(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                            transform=worker_transform))

    def test_watch_fast_path_refuses_new_vendor_and_symlinked_asset(self):
        asset = self.capture(stage=True)
        self.vendor.write_text('new unknown vendor worker')
        self.assertFalse(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                            transform=worker_transform))
        self.vendor.write_text(WORKER)
        path = pathlib.Path(asset['path']); path.unlink(); path.symlink_to(self.vendor)
        self.assertFalse(engine.content_search_worker_current(self.root, asset, self.vendor,
                                                            transform=worker_transform))


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
