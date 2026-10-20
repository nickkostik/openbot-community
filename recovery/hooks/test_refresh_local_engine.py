import gzip
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


M = load('refresh-local-engine')
ENGINE = load('local-engine')
STOCK = '  async sendPrompt(prompt, options2) {\n' + '    // retained native runner\n' * 200 + '\n    return this.tm.turnRuntime.runAgentTurn(prompt, options2);\n  }\n  nextTurnEpoch(session) {}\n  runServerScheduledAutomation(args) {}'


def transform(source):
    return source if source.endswith('\n// CHECKED UPGRADE') else source + '\n// CHECKED UPGRADE'


class EngineUpgradeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name); directory = self.root / 'openbot-local-engine'; directory.mkdir()
        self.sources = {'stock': STOCK.encode(), 'wrapped': ('/* openbot-stock-wrap */\n' + STOCK).encode()}
        self.policy = {'schemaVersion': 1, 'enabled': True, 'clientVersion': '0.67.0',
                       **{k + 'Sha256': hashlib.sha256(v).hexdigest() for k, v in self.sources.items()}}
        self.path = self.root / 'openbot-local-engine.json'; self.path.write_text(json.dumps(self.policy))
        for k, v in self.sources.items(): (directory / (k + '.cjs')).write_bytes(v)
        self.checked = []

    def refresh(self, **kwargs):
        return M.refresh(self.root, transform=transform, validate=lambda source: self.checked.append(source), **kwargs)

    def test_plan_leaves_all_artifacts_and_manifest_unchanged(self):
        before = self.path.read_bytes()
        result = self.refresh()
        self.assertFalse(result['activated']); self.assertEqual(len(self.checked), 2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / 'openbot-local-engine/generations').exists())

    def test_atomic_manifest_selects_the_complete_hash_checked_pair_and_is_idempotent(self):
        result = self.refresh(apply=True)
        policy = json.loads(self.path.read_bytes()); self.assertTrue(result['activated'])
        self.assertEqual(policy['clientVersion'], self.policy['clientVersion'])
        directory = self.root / 'openbot-local-engine/generations' / result['generationId']
        self.assertEqual(json.loads((directory / 'previous-policy.json').read_bytes()), self.policy)
        for k in ['stock', 'wrapped']:
            actual = (directory / (k + '.cjs')).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), policy[k + 'Sha256'])
            self.assertEqual((directory / (k + '.cjs')).stat().st_mode & 0o777, 0o400)
        self.assertEqual(ENGINE.select_source(self.root, pathlib.Path('host-main.cjs.pre-openbot'), ''), transform(STOCK))
        self.assertEqual(ENGINE.select_source(self.root, pathlib.Path('host-main.cjs'), ''), transform(self.sources['wrapped'].decode()))
        before = self.path.read_bytes(); second = self.refresh(apply=True)
        self.assertEqual(second['state'], 'already-current'); self.assertFalse(second['activated'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_hash_mismatch_refuses_without_resetting_the_trusted_manifest(self):
        p = self.root / 'openbot-local-engine/wrapped.cjs'; p.write_text('CORRUPT')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'trusted manifest'): self.refresh(apply=True)
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(p.read_text(), 'CORRUPT')

    def test_only_backups_matching_the_existing_trusted_hash_can_repair_mutated_artifacts(self):
        backup = self.root / 'wrapped.cjs.gz'; backup.write_bytes(gzip.compress(self.sources['wrapped']))
        p = self.root / 'openbot-local-engine/wrapped.cjs'; p.write_text('CORRUPT')
        result = self.refresh(wrapped_backup=backup, apply=True)
        self.assertTrue(result['activated']); self.assertEqual(p.read_text(), 'CORRUPT')
        self.assertIn('CHECKED UPGRADE', ENGINE.select_source(self.root, pathlib.Path('host-main.cjs'), ''))

    def test_validation_or_concurrent_edit_cannot_activate_a_partial_pair(self):
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            M.refresh(self.root, apply=True, transform=transform, validate=lambda _: (_ for _ in ()).throw(RuntimeError('syntax')))
        self.assertEqual(self.path.read_bytes(), before)
        real_atomic = M.atomic
        def edit(path, data, expected):
            self.path.write_text(json.dumps({**self.policy, 'ownerEdited': True}))
            return real_atomic(path, data, expected)
        with mock.patch.object(M, 'atomic', side_effect=edit), self.assertRaisesRegex(RuntimeError, 'concurrently'):
            self.refresh(apply=True)
        self.assertTrue(json.loads(self.path.read_bytes())['ownerEdited'])
        self.assertNotIn('generationId', json.loads(self.path.read_bytes()))

    def test_generation_paths_and_symlinks_fail_closed(self):
        self.path.write_text(json.dumps({**self.policy, 'generationId': '../elsewhere'}))
        with self.assertRaisesRegex(RuntimeError, 'generation'): ENGINE.select_source(self.root, pathlib.Path('host-main.cjs'), '')
        self.path.write_text(json.dumps(self.policy))
        directory = self.root / 'openbot-local-engine/generations'; directory.symlink_to(self.root, target_is_directory=True)
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError): self.refresh(apply=True)
        self.assertEqual(self.path.read_bytes(), before)

    def worker_options(self):
        vendor = self.root / 'vendor-worker.cjs'
        vendor.write_text('const nativeWorker = true;\n')
        def bounded(source):
            original = 'const nativeWorker = true;\n'
            patched = original + 'const connectionLimit = 8;\n'
            if source in (original, patched):
                return patched
            raise RuntimeError('Unreviewed worker')
        return {'vendor_worker': vendor, 'worker_transform': bounded,
                'worker_validate': lambda _: None}

    def test_optional_worker_plan_does_not_stage_and_apply_records_current_immutable_asset(self):
        options = self.worker_options()
        before = options['vendor_worker'].read_bytes()
        plan = self.refresh(**options)
        self.assertIn('contentSearchWorker', plan)
        self.assertFalse(pathlib.Path(plan['contentSearchWorker']['path']).exists())
        result = self.refresh(apply=True, **options)
        policy = json.loads(self.path.read_bytes())
        self.assertEqual(policy['contentSearchWorker'], result['contentSearchWorker'])
        asset = pathlib.Path(policy['contentSearchWorker']['path'])
        self.assertEqual(asset.stat().st_mode & 0o777, 0o400)
        self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), policy['contentSearchWorker']['sha256'])
        folder = self.root / 'openbot-local-engine/generations' / result['generationId']
        self.assertEqual(json.loads((folder / 'content-search-worker.json').read_bytes()), policy['contentSearchWorker'])
        self.assertEqual(options['vendor_worker'].read_bytes(), before)
        self.assertEqual(self.refresh(apply=True, **options)['state'], 'already-current')
        asset.unlink()
        self.assertEqual(self.refresh(apply=True, **options)['state'], 'already-current')
        self.assertTrue(asset.exists(), 'an owned missing asset may be reproduced without selecting a new pair')

    def test_unknown_new_worker_clears_old_asset_without_blocking_engine_refresh(self):
        options = self.worker_options()
        self.refresh(apply=True, **options)
        policy = json.loads(self.path.read_bytes()); previous = pathlib.Path(policy['contentSearchWorker']['path'])
        options['vendor_worker'].write_text('new unsupported vendor worker')
        result = self.refresh(apply=True, **options)
        self.assertTrue(result['activated'])
        self.assertNotIn('contentSearchWorker', json.loads(self.path.read_bytes()))
        self.assertTrue(previous.exists(), 'old immutable generations stay recoverable but are not selected')
        self.assertEqual(options['vendor_worker'].read_text(), 'new unsupported vendor worker')

    def test_staged_optional_asset_does_not_publish_policy_when_pair_cas_fails(self):
        options = self.worker_options()
        def concurrent_edit(_):
            self.path.write_text(json.dumps({**self.policy, 'ownerEdited': True}))
        with self.assertRaisesRegex(RuntimeError, 'concurrently'):
            M.activate_pair(self.root, self.sources, self.path.read_bytes(), before_activate=concurrent_edit, **options)
        current = json.loads(self.path.read_bytes())
        self.assertTrue(current['ownerEdited'])
        self.assertNotIn('contentSearchWorker', current)

    def test_caller_provenance_cannot_pin_a_stale_optional_worker(self):
        result = M.activate_pair(self.root, self.sources, self.path.read_bytes(),
                                 metadata={'contentSearchWorker': {'path': 'stale'}})
        self.assertTrue(result['activated'])
        self.assertNotIn('contentSearchWorker', json.loads(self.path.read_bytes()))

    def test_sidecar_metadata_cannot_publish_an_unreadable_oversized_manifest(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'reader bound'):
            M.activate_pair(self.root, self.sources, before, metadata={'audit': 'x' * 16384},
                            **self.worker_options())
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
