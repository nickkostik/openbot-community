import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent


def patcher():
    spec = importlib.util.spec_from_file_location('server_sidebar', HERE / 'patch-temporal-sidebar-preferences.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def archive(assets):
    files = {}; offset = 0
    for name, content in assets.items():
        digest = hashlib.sha256(content).hexdigest()
        files[name] = {'size': len(content), 'offset': str(offset), 'integrity': {
            'algorithm': 'SHA256', 'hash': digest, 'blockSize': 4194304, 'blocks': [digest]}}
        offset += len(content)
    header = json.dumps({'files': files}, separators=(',', ':')).encode()
    padding = b'\0' * (-(4 + len(header)) % 4)
    payload = struct.pack('<II', 4 + len(header) + len(padding), len(header)) + header + padding
    return struct.pack('<II', 4, len(payload)) + payload + b''.join(assets.values())


class ServerSidebarTests(unittest.TestCase):
    def test_main_app_cache_is_backed_up_verified_and_invalidated(self):
        m = patcher()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); cache = root / 'cache'; cache.mkdir()
            originals = {'main-app-reviewed-build.v8': b'old-code',
                         'main-app-reviewed-build.v8.12345': b'old-code-temp'}
            for name, content in originals.items(): (cache / name).write_bytes(content)
            (cache / 'main-core-build.v8').write_bytes(b'independent')
            (cache / 'main-app-malformed.v8.extra').write_bytes(b'unrelated')
            proof = m.invalidate_code_cache(cache, root / 'backups')
            self.assertEqual(proof['moved'], 2)
            for name, content in originals.items():
                self.assertFalse((cache / name).exists())
                backup = Path(proof['backup']) / name
                self.assertEqual(backup.read_bytes(), content)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual((cache / 'main-core-build.v8').read_bytes(), b'independent')
            self.assertEqual((cache / 'main-app-malformed.v8.extra').read_bytes(), b'unrelated')
            self.assertEqual(m.invalidate_code_cache(cache, root / 'backups'), {'moved': 0})
            self.assertEqual(m.invalidate_code_cache(root / 'absent', root / 'backups'), {'moved': 0})

    def test_unexpected_cache_paths_refuse_before_any_file_is_removed(self):
        m = patcher()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); cache = root / 'cache'; cache.mkdir()
            good = cache / 'main-app-good.v8'; good.write_bytes(b'preserved')
            target = root / 'outside'; target.write_bytes(b'untouched')
            (cache / 'main-app-linked.v8').symlink_to(target)
            with self.assertRaises(ValueError): m.invalidate_code_cache(cache, root / 'backups')
            self.assertEqual(good.read_bytes(), b'preserved')
            self.assertEqual(target.read_bytes(), b'untouched')
            link = root / 'cache-link'; link.symlink_to(cache)
            with self.assertRaises(ValueError): m.invalidate_code_cache(link, root / 'backups')

    def test_check_is_read_only_and_running_desktop_refuses_installation(self):
        m = patcher(); authority = m.load_authority()
        renderer = b'\n'.join([authority.ROSTER_STOCK, authority.TIME_STOCK, authority.ROW_TIME_STOCK,
                              *[pair[0] for pair in authority.LEGACY_PATCHES], m.SLEEP_STOCK])
        raw = archive({'main.cjs': m.RUNTIME_STOCK+b'\n'+m.HANDLERS_STOCK, 'renderer.js': renderer})
        with tempfile.TemporaryDirectory() as folder:
            authority.ASAR=Path(folder)/'app.asar'; authority.BACKUP_ROOT=Path(folder)/'backups'
            authority.ASAR.write_bytes(raw)
            with patch.object(m, 'load_authority', return_value=authority), patch.object(m, 'desktop_running', return_value=True), patch.object(m, 'invalidate_code_cache') as invalidate, patch('sys.argv', ['patcher', '--check']):
                self.assertEqual(m.main(), 0)
                invalidate.assert_not_called()
                self.assertEqual(authority.ASAR.read_bytes(), raw)
                self.assertFalse(authority.BACKUP_ROOT.exists())
            authority=m.load_authority(); authority.ASAR=Path(folder)/'app.asar'
            with patch.object(m, 'load_authority', return_value=authority), patch.object(m, 'desktop_running', return_value=True), patch('sys.argv', ['patcher']):
                with self.assertRaisesRegex(ValueError, 'Quit the desktop'): m.main()
                self.assertEqual(authority.ASAR.read_bytes(), raw)

    def test_reviewed_anchors_are_atomic_and_idempotent(self):
        m = patcher(); raw = b'\n'.join([m.RUNTIME_STOCK, m.HANDLERS_STOCK, m.SLEEP_STOCK])
        fixed = m.transform_preferences(raw)
        self.assertEqual(len(raw), len(fixed))
        self.assertEqual(m.state(fixed), 'fixed')
        self.assertEqual(m.transform_preferences(fixed), fixed)
        for bad in (b'unknown', raw * 2, raw.replace(m.RUNTIME_STOCK, m.RUNTIME_FIXED)):
            with self.assertRaises(ValueError): m.transform_preferences(bad)

    def run_js(self, scenario):
        m = patcher()
        script = """
const assert=(condition,message)=>{if(!condition)throw Error(message)};
let stored={sidebarSections:{sections:[{id:'me',name:'Me',agentIds:['recovered']}]}},readFail=false,writeFail=false,omitEcho=false,boxCalls=0;
const wire={getGrokBotUserRuntimeSettings:async()=>{if(readFail)throw Error('offline');return {settings:stored}},
 updateGrokBotUserRuntimeSettings:async body=>{if(writeFail)throw Error('write refused');stored=structuredClone(body);return omitEcho?{}:{settings:stored}}};
const n=()=>wire,o={},Qat={invariant:assert};
const runtime={RUNTIME};
const e={grokBot:{runtimeSettings:runtime},readHostSettingsFromBox:()=>{boxCalls++;throw Error('Computer asleep')}};
const ipc={HANDLERS};
SCENARIO
process.stdout.write(JSON.stringify({boxCalls}));
""".replace('RUNTIME', m.RUNTIME_FIXED.decode()).replace('HANDLERS', m.HANDLERS_FIXED.decode()).replace('SCENARIO', scenario)
        return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True))

    def test_awake_or_sleeping_computer_is_not_a_read_dependency(self):
        result = self.run_js("const rows=await ipc.getHostSidebarSections();assert(rows[0].agentIds[0]==='recovered','stale read');")
        self.assertEqual(result['boxCalls'], 0)

    def test_writes_return_the_actual_server_echo_and_keep_exact_members(self):
        result = self.run_js("const rows=[{id:'me',name:'Me',agentIds:['one','two']},{id:'coding',name:'Coding',agentIds:[]}];const echo=await ipc.setHostSidebarSections({sections:rows});assert(JSON.stringify(echo)===JSON.stringify(rows),'write changed memberships');assert((await ipc.getHostSidebarSections())[0].agentIds[1]==='two','readback differs');")
        self.assertEqual(result['boxCalls'], 0)

    def test_rejected_writes_and_missing_echo_never_report_success(self):
        self.run_js("writeFail=true;let failed=false;try{await ipc.setHostSidebarSections({sections:[]})}catch(error){failed=true}assert(failed,'write refusal hidden');writeFail=false;omitEcho=true;failed=false;try{await ipc.setHostSidebarSections({sections:[]})}catch(error){failed=true}assert(failed,'missing echo accepted');")

    def test_failed_read_is_not_an_empty_or_stale_success(self):
        self.run_js("readFail=true;let failed=false;try{await ipc.getHostSidebarSections()}catch(error){failed=true}assert(failed,'offline read hid failure');")

    def test_unset_preferences_and_explicit_empty_preferences_remain_distinct(self):
        self.run_js("stored={};assert(await ipc.getHostSidebarSections()===null,'unset is not null');stored={sidebarSections:{sections:[]}};assert((await ipc.getHostSidebarSections()).length===0,'explicit empty lost');")

    def test_renderer_loads_server_preferences_even_when_computer_is_asleep(self):
        m = patcher()
        script = 'let asleep=true,subscribed=false;const s={noteBoxAsleep:v=>{asleep=v}},r={snapshots:{get:()=>`asleep`,subscribe:fn=>{subscribed=true;fn();return()=>{}}}};let '
        script += m.SLEEP_FIXED.decode()
        script += 'if(asleep||!subscribed)throw Error("server read still gated");'
        subprocess.check_call(['node', '--input-type=module', '-e', script])

    def test_offsets_integrity_and_asset_placement(self):
        m = patcher(); authority = m.load_authority()
        raw = archive({'main.cjs': m.RUNTIME_STOCK + b'\n' + m.HANDLERS_STOCK, 'renderer.js': m.SLEEP_STOCK})
        fixed = authority.refresh_integrity(raw, m.transform_archive(raw, authority))
        self.assertEqual(len(raw), len(fixed))
        self.assertEqual(m.transform_archive(fixed, authority), fixed)
        header_size = struct.unpack_from('<I', fixed, 4)[0]; json_size = struct.unpack_from('<I', fixed, 12)[0]
        header = json.loads(fixed[16:16+json_size]); start = 8 + header_size
        for entry in authority.packed_files(header):
            content = fixed[start+int(entry['offset']):start+int(entry['offset'])+entry['size']]
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry['integrity']['hash'])
        split = archive({'runtime.cjs': m.RUNTIME_STOCK, 'handlers.cjs': m.HANDLERS_STOCK, 'renderer.js': m.SLEEP_STOCK})
        with self.assertRaisesRegex(ValueError, 'placement'): m.transform_archive(split, authority)

    def test_composes_with_roster_date_patch_and_installs_atomically(self):
        m = patcher(); authority = m.load_authority()
        renderer = b'\n'.join([authority.ROSTER_STOCK, authority.TIME_STOCK, authority.ROW_TIME_STOCK,
            *[pair[0] for pair in authority.LEGACY_PATCHES], m.SLEEP_STOCK])
        raw = archive({'main.cjs': m.RUNTIME_STOCK+b'\n'+m.HANDLERS_STOCK, 'renderer.js': renderer})
        with tempfile.TemporaryDirectory() as folder:
            authority.ASAR=Path(folder)/'app.asar'; authority.BACKUP_ROOT=Path(folder)/'backup'
            authority.ASAR.write_bytes(raw); base_transform=authority.transform
            authority.transform=lambda data:m.transform_archive(base_transform(data),authority)
            self.assertEqual(authority.main(),0)
            installed=authority.ASAR.read_bytes()
            self.assertEqual(m.state(installed),'fixed')
            self.assertEqual(authority.patch_state(installed)['timestamp'],'fixed')
            self.assertEqual(len(list(authority.BACKUP_ROOT.iterdir())),1)
            self.assertEqual(authority.main(),0)
            self.assertEqual(authority.ASAR.read_bytes(),installed)


if __name__ == '__main__': unittest.main()
