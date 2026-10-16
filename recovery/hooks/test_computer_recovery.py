import importlib.util
import ast
import contextlib
import io
import json
import hashlib
import gzip
import pathlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('computer_recovery', HERE / 'computer-recovery.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ComputerRecoveryTests(unittest.TestCase):
    def official_fixture(self, root):
        sd = pathlib.Path(root); host = sd / 'host-main.cjs'
        composed = b'/* openbot-latest-box-compatibility-v1 */\nLOCAL'
        stock = b'VENDOR STOCK'
        host.write_bytes(composed); host.chmod(0o640)
        (sd / 'host-main.cjs.pre-openbot').write_bytes(composed)
        (sd / 'openbot-mode').write_text('official\n')
        digest = hashlib.sha256(stock).hexdigest()
        (sd / 'openbot-local-engine.json').write_text(json.dumps({
            'schemaVersion': 1, 'enabled': True, 'stockSha256': 'a' * 64,
            'wrappedSha256': 'b' * 64, 'upstreamVersion': 'abcdef0', 'upstreamSourceSha256': digest}))
        vendor = sd / 'openbot-upstream/releases/abcdef0'; vendor.mkdir(parents=True)
        (vendor / 'host-main.cjs').write_bytes(stock)
        (vendor / 'vendor.json').write_text(json.dumps({'sourceSha256': digest}))
        return sd, host, vendor, composed, stock

    def test_official_restores_verified_vendor_instead_of_local_stock(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, vendor, composed, stock = self.official_fixture(root)
            armed = []; checked = []
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                changed = m.restore_official_stock(checked.append, lambda: armed.append(True))
                self.assertEqual(m.restore_official_stock(lambda _: self.fail('fixed point')), [])
            self.assertEqual(set(changed), {'host-main.cjs', 'host-main.cjs.pre-openbot'})
            self.assertEqual(host.read_bytes(), stock)
            self.assertEqual((sd / 'host-main.cjs.pre-openbot').read_bytes(), stock)
            self.assertEqual(host.stat().st_mode & 0o777, 0o640)
            self.assertEqual((host.parent / 'version').read_text(), 'abcdef0\n')
            self.assertEqual(checked, [stock]); self.assertEqual(armed, [True])
            backups = list((sd / 'recovery-snapshots/computer-update-bundles').glob('*.gz'))
            self.assertEqual(len(backups), 2)
            self.assertTrue(all(gzip.decompress(p.read_bytes()) == composed for p in backups))

    def test_official_preserves_a_newer_native_update_without_local_markers(self):
        with tempfile.TemporaryDirectory() as root:
            sd = pathlib.Path(root); host = sd / 'host-main.cjs'; host.write_bytes(b'NEWER NATIVE')
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                self.assertEqual(m.restore_official_stock(lambda _: self.fail('native untouched')), [])
            self.assertEqual(host.read_bytes(), b'NEWER NATIVE')

    def test_official_local_rollback_does_not_downgrade_captured_vendor(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, _, _, _ = self.official_fixture(root)
            stock = b'LATEST VERIFIED VENDOR'; digest = hashlib.sha256(stock).hexdigest()
            folder = sd / 'openbot-upstream/releases/1234567'; folder.mkdir()
            (folder / 'host-main.cjs').write_bytes(stock)
            (folder / 'vendor.json').write_text(json.dumps({'sourceSha256': digest}))
            (sd / 'openbot-upstream-sync-state.json').write_text(json.dumps({
                'phase': 'verification-failed', 'upstreamVersion': '1234567',
                'upstreamSourceSha256': digest}))
            version = host.parent / 'version'; version.write_text('abcdef0\n'); version.chmod(0o640)
            pin = (sd / 'openbot-local-engine.json').read_bytes()
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                m.restore_official_stock(lambda _: None)
            self.assertEqual(host.read_bytes(), stock)
            self.assertEqual(version.read_text(), '1234567\n')
            self.assertEqual(version.stat().st_mode & 0o777, 0o640)
            self.assertEqual((sd / 'openbot-local-engine.json').read_bytes(), pin)

    def test_official_invalid_capture_cannot_fall_back_to_old_pin(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, _, composed, _ = self.official_fixture(root)
            (sd / 'openbot-upstream-sync-state.json').write_text(json.dumps({
                'upstreamVersion': '../bad', 'upstreamSourceSha256': 'a' * 64}))
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                with self.assertRaisesRegex(RuntimeError, 'vendor authority'):
                    m.restore_official_stock(lambda _: self.fail('invalid capture'))
            self.assertEqual(host.read_bytes(), composed)

    def test_official_preopenbot_restore_cannot_change_newer_native_version(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, _, _, stock = self.official_fixture(root)
            host.write_bytes(b'NEWER NATIVE'); version = host.parent / 'version'; version.write_text('7654321\n')
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                self.assertEqual(m.restore_official_stock(lambda _: None), ['host-main.cjs.pre-openbot'])
            self.assertEqual(host.read_bytes(), b'NEWER NATIVE')
            self.assertEqual(version.read_text(), '7654321\n')
            self.assertEqual((sd / 'host-main.cjs.pre-openbot').read_bytes(), stock)

    def test_official_refuses_changed_vendor_bytes_without_touching_local_engine(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, vendor, composed, stock = self.official_fixture(root)
            (vendor / 'host-main.cjs').write_bytes(stock + b'CHANGED')
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                with self.assertRaisesRegex(RuntimeError, 'hash differs'):
                    m.restore_official_stock(lambda _: self.fail('bad vendor not checked'))
            self.assertEqual(host.read_bytes(), composed)

    def test_official_restoration_rechecks_mode_before_each_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, vendor, composed, stock = self.official_fixture(root)
            def change_mode(_): (sd / 'openbot-mode').write_text('custom\n')
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                with self.assertRaisesRegex(RuntimeError, 'Mode changed'):
                    m.restore_official_stock(change_mode)
            self.assertEqual(host.read_bytes(), composed)

    def test_official_restoration_does_not_follow_symlinked_vendor(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, vendor, composed, stock = self.official_fixture(root)
            path = vendor / 'host-main.cjs'; path.unlink(); path.symlink_to(host)
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host):
                with self.assertRaises(OSError): m.restore_official_stock(lambda _: None)
            self.assertEqual(host.read_bytes(), composed)

    def test_full_fleet_repair_preserves_ids_settings_rooms_and_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);agents=sd/'agents';agents.mkdir()
            originals={}
            for n,harness in enumerate(('temporal','box',None),1):
                aid=f'00000000-0000-4000-8000-{n:012d}'
                directory=agents/aid;directory.mkdir()
                profile={'id':aid,'name':'Keep this name','serverId':str(n),'role':'keep role','modelId':'keep model','reasoningEffort':'max'}
                if harness is not None:profile['harness']=harness
                path=directory/'profile.json';path.write_text(json.dumps(profile));path.chmod(0o640)
                (directory/'store.db').write_bytes(b'UNCHANGED TRANSCRIPT')
                (directory/'group.json').write_text('{"memberIds":["keep"]}')
                (directory/'settings.json').write_text('{"hiddenFromSidebar":true}')
                originals[aid]=profile
            deleted=agents/'00000000-0000-4000-8000-000000000004';deleted.mkdir()
            armed=[]
            with mock.patch.object(m,'SD',sd):
                result=m.repair_local_profiles(lambda:armed.append(True))
                second=m.repair_local_profiles(lambda:self.fail('fixed point reload'))
            self.assertEqual(result['profileCount'],3);self.assertEqual(result['boxProfiles'],3)
            self.assertEqual(len(result['changedProfileIds']),2);self.assertEqual(result['errors'],[])
            self.assertEqual(second['changedProfileIds'],[]);self.assertEqual(len(armed),2)
            self.assertFalse((deleted/'profile.json').exists())
            for aid,original in originals.items():
                path=agents/aid/'profile.json'
                self.assertEqual(json.loads(path.read_bytes()),{**original,'harness':'box'})
                self.assertEqual(path.stat().st_mode&0o777,0o640)
                self.assertEqual((path.parent/'store.db').read_bytes(),b'UNCHANGED TRANSCRIPT')
                self.assertEqual((path.parent/'group.json').read_text(),'{"memberIds":["keep"]}')
                self.assertEqual((path.parent/'settings.json').read_text(),'{"hiddenFromSidebar":true}')
            self.assertEqual(len(list((sd/'recovery-snapshots/local-fleet-profiles').glob('*.json'))),2)

    def test_invalid_profiles_and_symlinks_are_reported_without_writes(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);agents=sd/'agents';agents.mkdir()
            cases=('bad-json','wrong-id','symlink','unknown-harness')
            for n,kind in enumerate(cases,1):
                directory=agents/f'00000000-0000-4000-8000-{n:012d}';directory.mkdir();path=directory/'profile.json'
                if kind=='bad-json':path.write_text('{')
                if kind=='wrong-id':path.write_text('{"id":"somebody else","harness":"temporal"}')
                if kind=='symlink':path.symlink_to(agents/'00000000-0000-4000-8000-000000000001'/'profile.json')
                if kind=='unknown-harness':path.write_text('{"harness":"future-kind"}')
            with mock.patch.object(m,'SD',sd):result=m.repair_local_profiles()
            self.assertEqual(len(result['errors']),4);self.assertEqual(result['changedProfileIds'],[])
            self.assertEqual((agents/'00000000-0000-4000-8000-000000000001'/'profile.json').read_text(),'{')
            self.assertTrue((agents/'00000000-0000-4000-8000-000000000003'/'profile.json').is_symlink())

    def test_profile_edited_during_repair_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);directory=sd/'agents/00000000-0000-4000-8000-000000000001';directory.mkdir(parents=True)
            path=directory/'profile.json';path.write_text('{"harness":"temporal","name":"Before"}')
            def race():path.write_text('{"harness":"temporal","name":"Concurrent edit"}')
            with mock.patch.object(m,'SD',sd):result=m.repair_local_profiles(race)
            self.assertEqual(json.loads(path.read_text())['name'],'Concurrent edit')
            self.assertEqual(result['changedProfileIds'],[]);self.assertEqual(len(result['errors']),1)

    def test_inaccessible_profile_does_not_prevent_other_bots_from_becoming_local(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);paths=[]
            for n in (1,2):
                directory=sd/f'agents/00000000-0000-4000-8000-{n:012d}';directory.mkdir(parents=True)
                path=directory/'profile.json';path.write_text('{"harness":"temporal"}');paths.append(path)
            real_lstat=pathlib.Path.lstat
            def inspect(path):
                if path==paths[0]:raise PermissionError('inaccessible profile')
                return real_lstat(path)
            with mock.patch.object(m,'SD',sd),mock.patch.object(pathlib.Path,'lstat',autospec=True,side_effect=inspect):
                result=m.repair_local_profiles()
            self.assertEqual(json.loads(paths[0].read_text())['harness'],'temporal')
            self.assertEqual(json.loads(paths[1].read_text())['harness'],'box')
            self.assertEqual(result['boxProfiles'],1);self.assertEqual(result['errors'][0]['errorType'],'PermissionError')
    def test_backup_repair_refuses_symlink_without_running_sudo(self):
        with tempfile.TemporaryDirectory() as root:
            pack=pathlib.Path(root)/'pack'; pack.symlink_to(pathlib.Path(root),target_is_directory=True)
            with mock.patch.object(m,'PACK_DIR',pack), mock.patch.object(m.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'symlinked'):m.ensure_backup_permissions()
                run.assert_not_called()

    def test_backup_repair_preserves_existing_native_root_hook(self):
        with tempfile.TemporaryDirectory() as root:
            pack=pathlib.Path(root)/'pack';pack.mkdir();hook=pathlib.Path(root)/'hook';hook.write_text('native')
            real_lstat=pathlib.Path.lstat
            def inspect(p):
                value=real_lstat(p)
                return SimpleNamespace(st_mode=value.st_mode,st_uid=0) if p==hook else value
            with mock.patch.object(m,'PACK_DIR',pack),mock.patch.object(m,'HIBERNATE_HOOK',hook),mock.patch.object(pathlib.Path,'lstat',autospec=True,side_effect=inspect),mock.patch.object(m.subprocess,'run') as run:
                self.assertEqual(m.ensure_backup_permissions()['hibernateHook'],'existing')
                run.assert_not_called()
            self.assertEqual(hook.read_text(),'native')

    def test_shutdown_hook_validates_flush_without_embedding_a_credential(self):
        ast.parse(m.HIBERNATE_SCRIPT)
        ast.parse(m.HOOK_REPLACE)
        self.assertIn('timeout=180',m.HIBERNATE_SCRIPT)
        self.assertIn('timeout=55',m.PREVIOUS_HIBERNATE_SCRIPT)
        self.assertIn("result.get('ok') is True",m.HIBERNATE_SCRIPT)
        self.assertIn('snapshotBoxStoreNow',m.HIBERNATE_SCRIPT)
        self.assertNotIn('Bearer sk-',m.HIBERNATE_SCRIPT)

    def test_previous_owned_hook_is_upgraded_and_backed_up_once(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);pack=sd/'pack';pack.mkdir();hook=sd/'hook'
            hook.write_text(m.PREVIOUS_HIBERNATE_SCRIPT)
            real_lstat=pathlib.Path.lstat
            def inspect(path):
                value=real_lstat(path)
                return SimpleNamespace(st_mode=value.st_mode,st_uid=0) if path==hook or path.name.startswith('.openbot-backup-hook-') else value
            def privileged(args,**options):
                if args[2]=='install':
                    pathlib.Path(args[-1]).write_bytes(pathlib.Path(args[-2]).read_bytes())
                elif args[2]=='/usr/bin/python3':
                    with mock.patch('sys.argv',['hook-replace',*args[-3:]]):exec(args[4],{})
                elif args[2]=='rm':pathlib.Path(args[-1]).unlink(missing_ok=True)
                else:self.fail('Unexpected privileged command')
                return SimpleNamespace(returncode=0)
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'PACK_DIR',pack),mock.patch.object(m,'HIBERNATE_HOOK',hook),mock.patch.object(pathlib.Path,'lstat',autospec=True,side_effect=inspect),mock.patch.object(m.subprocess,'run',side_effect=privileged) as run:
                self.assertEqual(m.ensure_backup_permissions()['hibernateHook'],'updated')
                calls=run.call_count
                self.assertEqual(m.ensure_backup_permissions()['hibernateHook'],'existing')
                self.assertEqual(run.call_count,calls)
            self.assertEqual(hook.read_text(),m.HIBERNATE_SCRIPT)
            saved=list((sd/'recovery-snapshots/backup-hooks').glob('*.py'))
            self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_text(),m.PREVIOUS_HIBERNATE_SCRIPT)

    def test_owned_hook_upgrade_refuses_a_competing_native_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            stage=pathlib.Path(root)/'stage';stage.write_text(m.HIBERNATE_SCRIPT)
            hook=pathlib.Path(root)/'hook';hook.write_text('CONCURRENT NATIVE HOOK')
            real_lstat=pathlib.Path.lstat
            def inspect(path):
                value=real_lstat(path);return SimpleNamespace(st_mode=value.st_mode,st_uid=0)
            with mock.patch.object(pathlib.Path,'lstat',autospec=True,side_effect=inspect),mock.patch('sys.argv',['hook-replace',str(stage),str(hook),hashlib.sha256(m.PREVIOUS_HIBERNATE_SCRIPT.encode()).hexdigest()]):
                with self.assertRaisesRegex(RuntimeError,'changed before replacement'):exec(m.HOOK_REPLACE,{})
            self.assertEqual(hook.read_text(),'CONCURRENT NATIVE HOOK')
            self.assertTrue(stage.exists())

    def test_syntax_failure_leaves_original_and_no_backup(self):
        with tempfile.TemporaryDirectory() as root:
            p = pathlib.Path(root) / 'host.cjs';p.write_text('ORIGINAL');backup=p.parent/'backup'
            with mock.patch.object(m,'SD',pathlib.Path(root)),mock.patch.object(m, 'patched', return_value='NEW'):
                def fail(_):raise RuntimeError('syntax')
                with self.assertRaises(RuntimeError):m.repair_bundle(p,backup,fail)
            self.assertEqual(p.read_text(),'ORIGINAL');self.assertFalse(backup.exists())

    def test_competing_update_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            p=pathlib.Path(root)/'host.cjs';p.write_text('ORIGINAL')
            with mock.patch.object(m,'SD',pathlib.Path(root)),mock.patch.object(m,'patched',return_value='NEW'):
                def race(_):p.write_text('PLATFORM UPDATE')
                with self.assertRaisesRegex(RuntimeError,'Concurrent'):m.repair_bundle(p,p.parent/'backup',race)
            self.assertEqual(p.read_text(),'PLATFORM UPDATE')

    def test_checked_replace_preserves_permissions_and_skips_fixed_point(self):
        with tempfile.TemporaryDirectory() as root:
            p=pathlib.Path(root)/'host.cjs';p.write_text('ORIGINAL');p.chmod(0o640)
            armed=[]
            with mock.patch.object(m,'SD',pathlib.Path(root)),mock.patch.object(m,'patched',return_value='NEW'):
                self.assertTrue(m.repair_bundle(p,p.parent/'backup',lambda _:None,lambda:armed.append(True)))
                self.assertFalse(m.repair_bundle(p,p.parent/'backup',lambda _:self.fail('unneeded validation')))
            self.assertEqual(p.stat().st_mode&0o777,0o640);self.assertEqual(armed,[True])
            self.assertEqual(len(list((p.parent/'backup').glob('*.gz'))),1)

    def test_tunnel_recovery_does_not_need_a_working_patch_anchor(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch();(sd/'openbot-mode').write_text('custom')
            host=sd/'host.cjs';host.write_text('UNSUPPORTED UPDATE')
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HOST',host),mock.patch.object(m,'ensure_named_tunnel',return_value={'state':'started'}) as tunnel,mock.patch.object(m,'ensure_backup_permissions',return_value={'state':'mocked'}):
                with self.assertRaises(RuntimeError):m.tick()
                tunnel.assert_called_once()
            self.assertEqual(host.read_text(),'UNSUPPORTED UPDATE')

    def test_official_mode_without_local_opt_in_starts_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-mode').write_text('official')
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'ensure_named_tunnel') as tunnel:
                self.assertEqual(m.tick(),{'state':'disabled'});tunnel.assert_not_called()

    def test_missing_mode_with_local_opt_in_starts_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'ensure_named_tunnel') as tunnel:
                self.assertEqual(m.tick(),{'state':'disabled'});tunnel.assert_not_called()

    def test_official_tick_recovers_panel_without_replacing_stock_or_rewriting_profiles(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch();(sd/'openbot-mode').write_text('official')
            host=sd/'host.cjs';host.write_text('PATCHED');(sd/'agents').mkdir()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HOST',host),mock.patch.object(m,'ensure_named_tunnel',return_value={'state':'running'}) as tunnel,mock.patch.object(m,'ensure_openbot_service',return_value={'state':'running'}) as service,mock.patch.object(m,'ensure_backup_permissions',return_value={'state':'mocked'}),mock.patch.object(m,'repair_bundle') as repair,mock.patch.object(m,'repair_local_profiles') as profiles,mock.patch.object(m.os,'kill') as kill:
                state=m.tick()
                self.assertEqual(state['state'],'official-stock')
                repair.assert_not_called();profiles.assert_not_called()
                self.assertEqual(state['openbotService']['state'],'running')
                tunnel.assert_called_once();service.assert_called_once();kill.assert_not_called()
            self.assertEqual((sd/'openbot-mode').read_text(),'official')

    def test_official_vendor_reload_waits_for_idle_and_survives_a_tick(self):
        with tempfile.TemporaryDirectory() as root:
            sd, host, vendor, composed, stock = self.official_fixture(root)
            (sd / 'openbot-local-only.enable').touch()
            with mock.patch.object(m, 'SD', sd), mock.patch.object(m, 'HOST', host), \
                    mock.patch.object(m, 'ensure_named_tunnel', return_value={'state': 'running'}), \
                    mock.patch.object(m, 'ensure_backup_permissions', return_value={'state': 'mocked'}), \
                    mock.patch.object(m, 'ensure_upstream_sync', return_value={'state': 'waiting'}), \
                    mock.patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                    mock.patch.object(m, 'host_pids', return_value=[123]), \
                    mock.patch.object(m, 'gateway_status', return_value={'isBusy': True}) as status, \
                    mock.patch.object(m.os, 'kill') as kill:
                state = m.tick()
                self.assertTrue(state['pendingOfficialStockReload']); kill.assert_not_called()
                self.assertEqual(host.read_bytes(), stock)
                status.return_value = {'isBusy': False}
                state = m.tick()
                kill.assert_called_once_with(123, m.signal.SIGTERM)
                self.assertFalse(state['pendingOfficialStockReload'])
            self.assertEqual((sd / 'openbot-mode').read_text(), 'official\n')

    def test_busy_host_keeps_durable_pending_reload(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch();(sd/'openbot-mode').write_text('custom')
            host=sd/'host.cjs';host.write_text('ORIGINAL');(sd/'agents').mkdir()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HOST',host),mock.patch.object(m,'ensure_named_tunnel',return_value={'state':'running'}),mock.patch.object(m,'ensure_backup_permissions',return_value={'state':'mocked'}),mock.patch.object(m,'repair_bundle',return_value=True),mock.patch.object(m,'host_pids',return_value=[123]),mock.patch.object(m,'gateway_status',return_value={'isBusy':True}),mock.patch.object(m.os,'kill') as kill:
                state=m.tick();self.assertTrue(state['pendingHostReload']);kill.assert_not_called()
            self.assertTrue(json.loads((sd/'computer-recovery-state.json').read_text())['pendingHostReload'])

    def test_browser_route_failure_does_not_block_host_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch();(sd/'openbot-mode').write_text('custom')
            host=sd/'host.cjs';host.write_text('PATCHED');(sd/'agents').mkdir()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HOST',host),mock.patch.object(m,'ensure_named_tunnel',return_value={'state':'running'}),mock.patch.object(m,'ensure_backup_permissions',return_value={'state':'mocked'}),mock.patch.object(m,'ensure_browser_egress',side_effect=RuntimeError('unknown launcher')),mock.patch.object(m,'repair_bundle',return_value=False),mock.patch.object(m.os,'kill') as kill:
                state=m.tick()
                self.assertEqual(state['browserEgress'],{'state':'failed','errorType':'RuntimeError'})
                self.assertEqual(state['state'],'local-patched');kill.assert_not_called()

    def test_profile_drift_does_not_restart_an_already_patched_host(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);(sd/'openbot-local-only.enable').touch();(sd/'openbot-mode').write_text('custom')
            host=sd/'host.cjs';host.write_text('PATCHED')
            directory=sd/'agents/00000000-0000-4000-8000-000000000001';directory.mkdir(parents=True)
            (directory/'profile.json').write_text('{"harness":"temporal"}')
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HOST',host),mock.patch.object(m,'ensure_named_tunnel',return_value={'state':'running'}),mock.patch.object(m,'ensure_backup_permissions',return_value={'state':'mocked'}),mock.patch.object(m,'ensure_browser_egress',return_value={'state':'direct'}),mock.patch.object(m,'repair_bundle',return_value=False),mock.patch.object(m.os,'kill') as kill:
                state=m.tick()
                self.assertEqual(len(state['profiles']['changedProfileIds']),1)
                self.assertFalse(state.get('pendingHostReload',False));kill.assert_not_called()

    def test_missing_service_uses_deferred_reconcile_without_spawning_a_hop(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root)
            for name in ('openbot/src/cli.ts','openbot/src/ui/server.ts','node22/bin/node'):
                p=sd/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HERE',sd/'openbot-hooks'),mock.patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'{"ok":true,"hostBounce":"deferred"}')) as run:
                state=m.ensure_openbot_service()
            self.assertEqual(state,{'state':'reconciled','hostReloadDeferred':True})
            argv=run.call_args.args[0]
            self.assertIn('--defer-host-bounce',argv);self.assertIn('install',argv)
            self.assertIn(str(sd/'openbot/src/cli.ts'),argv)
            self.assertNotIn('hop-server.cjs',' '.join(argv))

    def test_service_reconcile_refusal_is_not_reported_as_running(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root)
            for name in ('openbot/src/cli.ts','openbot/src/ui/server.ts','node22/bin/node'):
                p=sd/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HERE',sd/'openbot-hooks'),mock.patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=b'{"ok":false,"error":"foreign-hop"}')):
                with self.assertRaisesRegex(RuntimeError,'refused'):m.ensure_openbot_service()

    def test_packaged_helper_recovers_its_own_repo_instead_of_a_stale_factory_copy(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root)/'sand-data';repo=pathlib.Path(root)/'installed-openbot'
            for base in (sd/'openbot',repo):
                for name in ('src/cli.ts','src/ui/server.ts'):
                    path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            node=sd/'node22/bin/node';node.parent.mkdir(parents=True);node.touch()
            selected=pathlib.Path(root)/'native/host-main.cjs'
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HERE',repo/'recovery/hooks'),mock.patch.object(m,'HOST',selected),mock.patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'{"ok":true}')) as run:
                self.assertEqual(m.ensure_openbot_service()['state'],'reconciled')
            self.assertEqual(run.call_args.kwargs['cwd'],repo)
            argv=run.call_args.args[0]
            self.assertEqual(argv[2],str(repo/'src/cli.ts'))
            self.assertEqual(argv[argv.index('--host-main')+1],str(selected))
            self.assertEqual(argv[argv.index('--sand-data')+1],str(sd))

    def test_copied_hook_does_not_adopt_an_unrelated_parent_project(self):
        with tempfile.TemporaryDirectory() as root:
            parent=pathlib.Path(root);sd=parent/'sand-data'
            for base in (parent,sd/'openbot'):
                for name in ('src/cli.ts','src/ui/server.ts'):
                    path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            node=sd/'node22/bin/node';node.parent.mkdir(parents=True);node.touch()
            with mock.patch.object(m,'SD',sd),mock.patch.object(m,'HERE',sd/'openbot-hooks'),mock.patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'{"ok":true}')) as run:
                m.ensure_openbot_service()
            self.assertEqual(run.call_args.kwargs['cwd'],sd/'openbot')

    def test_cli_selected_host_is_used_for_the_tick_and_process_ownership(self):
        selected=pathlib.Path('/home/box/sand-host/host-main.cjs')
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(m,'SD',pathlib.Path(root)),mock.patch.object(m,'HOST',m.HOST),mock.patch.object(m,'owned_lock'),mock.patch.object(m.fcntl,'flock'),mock.patch.object(m,'tick',return_value={'state':'disabled'}) as tick,contextlib.redirect_stdout(io.StringIO()):
                m.main(['--host',str(selected)])
                self.assertEqual(m.HOST,selected)
                tick.assert_called_once()
                with mock.patch.object(m,'process_args',return_value=[
                    (10,[b'/exec-daemon/node',bytes(selected),b'']),
                    (20,[b'/exec-daemon/node',b'/opt/sand/sand-host/host-main.cjs',b'']),
                    (30,[b'/exec-daemon/node',bytes(selected),b'guard',b''])]):
                    self.assertEqual(m.host_pids(),[10])

    def test_bad_host_arguments_fail_before_any_directory_or_lock_write(self):
        for host in ('relative.cjs','/tmp/../host.cjs','/tmp/host\n.cjs','/tmp/host\0.cjs'):
            with self.subTest(host=repr(host)),mock.patch.object(m,'SD') as sd,mock.patch.object(m,'owned_lock') as lock,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:m.main(['--host',host])
                self.assertEqual(error.exception.code,2)
                sd.mkdir.assert_not_called();lock.assert_not_called()


if __name__=='__main__':unittest.main()
