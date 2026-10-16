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

    def test_patch_is_fixed_point_and_rejects_unknown_publisher(self):
        source='function openbotLocalFleetEnabled() {}\n'+M.ANCHOR+'\n'+M.STOCK+'\n});'
        changed=M.patch(source)
        self.assertEqual(M.patch(changed),changed)
        self.assertEqual(changed.count(M.MARK),1)
        subprocess.run(['node','--check'],input=changed,text=True,capture_output=True,check=True)
        with self.assertRaises(RuntimeError):M.patch('unknown upstream')


if __name__=='__main__':unittest.main()
