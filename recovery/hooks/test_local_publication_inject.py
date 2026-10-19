import importlib.util
import pathlib
import subprocess
import unittest

SPEC=importlib.util.spec_from_file_location('publication',pathlib.Path(__file__).with_name('local-publication-inject.py'))
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)


class PublicationTests(unittest.TestCase):
    def test_only_matching_actual_box_bindings_publish_and_official_delegates(self):
        fixture='''const fake={};let custom=true;
function openbotLocalFleetEnabled(){return custom;}
const process={getuid:()=>1000};
function require(){return {lstatSync:()=>({isFile:()=>true,uid:1000,size:10}),readFileSync:p=>JSON.stringify(fake[p])};}
'''+M.HELPER+'''
const root='/home/box/sand-data/';
fake[root+'openbot-publication-bindings.json']={schemaVersion:1,boxAgents:{fresh:'456'}};
fake['/agents/fresh/profile.json']={serverId:'456'};
const assert=(x)=>{if(!x)throw Error('Publication policy failed')};
assert(openbotSkipTranscriptPublication('old-temporal','/agents')===true);
assert(openbotSkipTranscriptPublication('fresh','/agents')===false);
fake['/agents/fresh/profile.json'].serverId='different';assert(openbotSkipTranscriptPublication('fresh','/agents')===true);
delete fake[root+'openbot-publication-bindings.json'];assert(openbotSkipTranscriptPublication('fresh','/agents')===true);
custom=false;assert(openbotSkipTranscriptPublication('old-temporal','/agents')===false);
'''
        result=subprocess.run(['node','-e',fixture],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def fixture(self):
        return '''function openbotLocalFleetEnabled() { return true; }
class TranscriptEntryPublisher {
'''+M.DRAIN+'''
    return state;
  }
  async publishAgent(agentId, state) {
    try { await this.publishAgentInner(agentId, state); }
    catch (error42) {
'''+M.FAILURE+'''"warn", {});
    }
  }
}
'''+M.ANCHOR+'''
  start: (context2) => {
    const agentsRootDir = '/agents';
'''+M.PUBLISHER+'\n'+M.COMMIT+'''
        }); return response;
      },
'''+M.STOCK+'\n'+M.REPORT+'''
    });
'''+M.READY+''' () => {};
    let unsubscribe;
'''+M.STOP+'''
    });
  }
});'''

    def test_patch_is_fixed_point_and_rejects_unknown_publisher(self):
        source=self.fixture()
        changed=M.patch(source)
        self.assertEqual(M.patch(changed),changed)
        self.assertEqual(changed.count(M.MARK),1)
        self.assertEqual(changed.count(M.OBSERVER_MARK),1)
        self.assertIn('getPublisher().flushAgent(id)',changed)
        self.assertIn('openbotPublicationObserver.dispose()',changed)
        subprocess.run(['node','--check'],input=changed,text=True,capture_output=True,check=True)
        with self.assertRaises(RuntimeError):M.patch('unknown upstream')

    def test_existing_policy_upgrades_without_changing_native_payload_path(self):
        source=self.fixture().replace(M.ANCHOR,M.HELPER+'\n'+M.ANCHOR)
        source=source.replace(M.STOCK,M.STOCK.replace('readSandProfileHarness',
            'openbotSkipTranscriptPublication(agentId, agentsRootDir) || readSandProfileHarness'))
        changed=M.patch(source)
        self.assertEqual(changed.count(M.MARK),1)
        self.assertEqual(changed.count(M.PUBLISHER),1)
        self.assertNotIn('commitGrokBotTranscriptEntries',M.OBSERVER)
        self.assertNotIn('appendTranscriptEntry',M.OBSERVER)
        self.assertEqual(M.patch(changed),changed)

    def test_changed_contract_or_partial_observer_is_refused(self):
        for source in [self.fixture().replace(M.REPORT,'report: changed'),
                       M.patch(self.fixture()).replace(M.OBSERVED_STOP,M.STOP),
                       M.patch(self.fixture()).replace('const maxRetries = 6;','const maxRetries = 600;')]:
            with self.subTest(source=source[:30]),self.assertRaises(RuntimeError):M.patch(source)


if __name__=='__main__':unittest.main()
