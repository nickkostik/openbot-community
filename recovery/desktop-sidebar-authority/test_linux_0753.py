"""Exercise the reviewed 0.75.3 JavaScript and archive boundaries."""
import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_temporal_sidebar_preferences import archive, patcher


class Linux0753Tests(unittest.TestCase):
    def fixture(self, variant=0):
        m = patcher(); a = m.load_authority(); layout = a.LAYOUTS['0.75.3']
        renderer = b'\n'.join([layout['rosters'][variant], layout['times'][0],
                               layout['extraTime'][0], layout['rows'][0],
                               *[v[variant == 1] for v in layout['legacy']], m.SLEEP_STOCK])
        main = b'\n'.join(m.LAYOUTS[1][0][:2])
        return m, a, archive({'main.cjs': main, 'renderer.js': renderer})

    def test_stock_and_old_visibility_recovery_converge_and_keep_asset_integrity(self):
        outputs = []
        for variant in (0, 1, 2):
            m, a, raw = self.fixture(variant)
            fixed = a.refresh_integrity(raw, m.transform_archive(a.transform(raw), a))
            self.assertEqual(len(fixed), len(raw))
            self.assertEqual(a.patch_state(fixed)['release'], '0.75.3')
            self.assertEqual(a.transform(fixed), fixed)
            self.assertEqual(m.transform_archive(fixed, a), fixed)
            _, hs, _, js = struct.unpack_from('<IIII', fixed)
            before = json.loads(raw[16:16+js]); after = json.loads(fixed[16:16+js])
            for name, entry in after['files'].items():
                self.assertEqual(entry['offset'], before['files'][name]['offset'])
                self.assertEqual(entry['size'], before['files'][name]['size'])
                start = 8 + hs + int(entry['offset'])
                self.assertEqual(hashlib.sha256(fixed[start:start+entry['size']]).hexdigest(), entry['integrity']['hash'])
            outputs.append(fixed)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])

    def test_partial_or_mixed_generation_refuses(self):
        m, a, raw = self.fixture()
        invalid = [raw.replace(a.EXTRA_TIME_0753_STOCK, a.EXTRA_TIME_0753_FIXED),
                   raw.replace(a.ROW_TIME_STOCK, a.ROW_TIME_0753_FIXED),
                   raw + a.ROSTER_STOCK,
                   raw.replace(m.LAYOUTS[1][0][1], m.HANDLERS_STOCK)]
        for data in invalid:
            with self.subTest(case=invalid.index(data)), self.assertRaises(ValueError):
                m.transform_archive(a.transform(data), a)
        split = archive({'runtime.cjs': m.LAYOUTS[1][0][0],
                         'handlers.cjs': m.LAYOUTS[1][0][1], 'renderer.js': m.SLEEP_STOCK})
        with self.assertRaisesRegex(ValueError, 'placement'):
            m.transform_archive(split, a)

    def test_both_date_readers_and_message_row_reject_corrupt_dates(self):
        m, a, _ = self.fixture()
        source = "const ks=e=>typeof e==='number'&&Number.isFinite(e);"
        source += a.TIME_0753_FIXED.decode().split('function zdt(')[0]
        source += a.EXTRA_TIME_0753_FIXED.decode().split('function jft(')[0]
        source += "const bad=[179261664684048608,Infinity,NaN,'123',null,undefined];"
        source += "for(const f of [rq,rJ]){for(const timestampMs of bad)if(f({timestampMs})!==null)throw Error('corrupt date accepted');for(const timestampMs of [0,1792616646840,864e13,-864e13])new Intl.DateTimeFormat('en').format(f({timestampMs}));}"
        source += "const Y={jsx:(tag,props)=>({tag,...props})};function row(r){let t=[],i='time',c;"
        source += a.ROW_TIME_0753_FIXED.decode()
        source += "{dateTime:new Date(r).toISOString()}),t[5]=i,t[6]=r,t[7]=c):c=t[7];return c}"
        source += "if(row(bad[0])!==null||row(1792616646840).dateTime!=='2026-10-21T21:04:06.840Z')throw Error('bad row date');"
        subprocess.check_call(['node', '--input-type=module', '-e', source])

    def test_current_roster_preserves_previews_and_pending_rows(self):
        m, a, _ = self.fixture()
        source = "const M7=({row})=>row,Yxn=(r,v)=>({...r,...v}),j7=()=>({}),kj=()=>0;"
        source += a.ROSTER_0753_FIXED.decode()
        source += "const cached=[{id:'coach',path:'/old',harness:'box',updatedAt:20,lastMessagePreview:'history'},{id:'gone'},{id:'pending'}];"
        source += "const fresh=[{id:'coach',path:'/agents/coach',name:'Dating Coach',harness:'temporal',updatedAt:10,isHiddenFromSidebar:false}];"
        source += "const rows=tSn(cached,fresh,new Map([['pending',{}]]));if(rows.length!==2||rows[0].path!=='/agents/coach'||rows[0].lastMessagePreview!=='history'||rows[0].updatedAt!==20)throw Error('roster data lost');if(tSn(cached,null)!==cached)throw Error('incomplete roster pruned');"
        subprocess.check_call(['node', '--input-type=module', '-e', source])

    def test_server_preferences_use_current_invariant_and_no_computer(self):
        m, _, _ = self.fixture()
        source = "const assert=v=>{if(!v)throw Error('invariant')},Wat={invariant:assert};let stored,fail=false;const n=()=>({getGrokBotUserRuntimeSettings:async()=>({settings:stored}),updateGrokBotUserRuntimeSettings:async body=>{if(fail)throw Error('offline');stored=body;return{settings:stored}}}),o={};"
        source += 'const runtime={' + m.LAYOUTS[1][1][0].decode() + '};'
        source += "const e={grokBot:{runtimeSettings:runtime},readHostSettingsFromBox:()=>{throw Error('old Computer called')}};"
        source += 'const ipc={' + m.HANDLERS_FIXED.decode() + '};'
        source += "assert(await ipc.getHostSidebarSections()===null);const sections=[{id:'me',agentIds:['coach']}];assert(JSON.stringify(await ipc.setHostSidebarSections({sections}))===JSON.stringify(sections));assert((await ipc.getHostSidebarSections())[0].agentIds[0]==='coach');fail=true;let refused=false;try{await ipc.setHostSidebarSections({sections:[]})}catch{refused=true}assert(refused);"
        subprocess.check_call(['node', '--input-type=module', '-e', source])

    def test_replaced_running_executable_still_refuses_install(self):
        m = patcher()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); proc = root/'proc'; proc.mkdir(); entry = proc/'123'; entry.mkdir()
            binary = root/'grok-bot'; binary.touch()
            (entry/'exe').symlink_to(str(binary)+' (deleted)')
            (entry/'cmdline').write_bytes(b'grok-bot\0--remote-debugging-port=9222\0')
            self.assertTrue(m.desktop_running(binary, proc))
            (entry/'cmdline').write_bytes(b'grok-bot\0--type=renderer\0')
            self.assertFalse(m.desktop_running(binary, proc))


if __name__ == '__main__':
    unittest.main()
