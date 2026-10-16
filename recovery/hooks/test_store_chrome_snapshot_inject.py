import importlib.util
import pathlib
import shutil
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('chrome_snapshot', pathlib.Path(__file__).with_name('store-chrome-snapshot-inject.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class ChromeSnapshotTests(unittest.TestCase):
    def fixture(self):
        return m.CLASS + '\n  start() {\n    this.watcher = {\n' + m.OLD + '\n    };\n  }\n  snapshotBoxStoreNow() { return this.sync.snapshotNow({includeStoreDbs:true}); }\n};\n'

    def node(self, body):
        if not shutil.which('node'): self.skipTest('Node is required')
        script = '''const assert=require('node:assert/strict');
const turn=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const errorMessage=e=>e.message;
function service(snapshotNow){return {sync:{stopped:false,snapshotNow},logs:[],deps:{log(message){this.owner.logs.push(message);}}};}
function make(snapshotNow){const s=service(snapshotNow);s.deps.owner=s;return s;}
''' + m.HELPER + '\n(async()=>{\n' + body + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_patch_changes_only_reviewed_callback_and_helper(self):
        source=self.fixture();patched=m.patch(source)
        self.assertEqual(m.patch(patched),patched)
        self.assertEqual(patched.replace(m.HELPER,'').replace(m.NEW,m.OLD),source)
        self.assertIn('includeStoreDbs:true',patched)

    def test_unknown_partial_duplicate_and_outside_class_contracts_refuse(self):
        source=self.fixture();patched=m.patch(source)
        for bad in [source+source,source.replace(m.OLD,'unknown'),patched+m.MARKER,
                    patched.replace(m.NEW,m.OLD),patched.replace(m.HELPER,m.MARKER),
                    source+'openbotChromeSnapshotQueues',source+'openbotQueueChromeSessionSnapshot',
                    source.replace(m.OLD,'')+m.OLD]:
            with self.assertRaises(RuntimeError):m.patch(bad)

    def test_event_storm_has_one_active_snapshot_and_one_latest_trailing_capture(self):
        self.node('''const first=deferred();let revision=0;const captured=[],args=[];
const s=make(async options=>{args.push(options);captured.push(revision);if(captured.length===1)await first.promise;});
openbotQueueChromeSessionSnapshot(s);
for(let n=1;n<=1000;n++){revision=n;assert.equal(openbotQueueChromeSessionSnapshot(s),undefined);}
assert.equal(captured.length,1);first.resolve();for(let i=0;i<5;i++)await turn();
assert.deepEqual(captured,[0,1000]);assert.equal(s.logs.length,0);
for(const a of args)assert.deepEqual(a,{chromeSessionOnly:true,waitForInFlight:true});''')

    def test_changes_during_trailing_snapshot_are_not_lost(self):
        self.node('''const holds=[deferred(),deferred()];let revision=1;const captured=[];
const s=make(async()=>{captured.push(revision);if(captured.length<=2)await holds[captured.length-1].promise;});
openbotQueueChromeSessionSnapshot(s);revision=2;openbotQueueChromeSessionSnapshot(s);holds[0].resolve();
await turn();await turn();assert.deepEqual(captured,[1,2]);
revision=3;openbotQueueChromeSessionSnapshot(s);holds[1].resolve();
for(let i=0;i<5;i++)await turn();assert.deepEqual(captured,[1,2,3]);''')

    def test_waiting_manual_flush_runs_before_trailing_browser_capture(self):
        self.node('''const first=deferred();let active=false,inFlight;const order=[];
async function snapshotNow(options={}){
 if(active){if(!options.waitForInFlight)return;while(active)await Promise.allSettled([inFlight]);}
 active=true;const full=options.includeStoreDbs===true;order.push(full?'full':'chrome');
 const run=(async()=>{if(order.length===1)await first.promise;})();inFlight=run;
 try{return await run;}finally{active=false;inFlight=undefined;}
}
const s=make(snapshotNow);openbotQueueChromeSessionSnapshot(s);
for(let i=0;i<1000;i++)openbotQueueChromeSessionSnapshot(s);
const full=s.sync.snapshotNow({includeStoreDbs:true,waitForInFlight:true});first.resolve();await full;
for(let i=0;i<5;i++)await turn();assert.deepEqual(order,['chrome','full','chrome']);''')

    def test_rejection_logs_and_a_later_change_retries_without_a_spin(self):
        self.node('''let calls=0;const s=make(async()=>{calls++;if(calls===1)throw Error('fixture-failure');});
openbotQueueChromeSessionSnapshot(s);for(let i=0;i<5;i++)await turn();
assert.equal(calls,1);assert.deepEqual(s.logs,['chrome-session snapshot rejected: fixture-failure']);
openbotQueueChromeSessionSnapshot(s);for(let i=0;i<5;i++)await turn();assert.equal(calls,2);''')

    def test_stopped_and_absent_sync_do_not_start_or_replay_work(self):
        self.node('''let calls=0;const hold=deferred();const s=make(async()=>{calls++;await hold.promise;});
openbotQueueChromeSessionSnapshot({sync:null});s.sync.stopped=true;openbotQueueChromeSessionSnapshot(s);assert.equal(calls,0);
s.sync.stopped=false;openbotQueueChromeSessionSnapshot(s);openbotQueueChromeSessionSnapshot(s);
s.sync.stopped=true;hold.resolve();for(let i=0;i<5;i++)await turn();assert.equal(calls,1);''')

    def test_separate_owners_do_not_block_or_coalesce_each_other(self):
        self.node('''const hold=deferred();let a=0,b=0;
const one=make(async()=>{a++;await hold.promise;});const two=make(async()=>{b++;});
openbotQueueChromeSessionSnapshot(one);openbotQueueChromeSessionSnapshot(two);
for(let i=0;i<3;i++)await turn();assert.equal(a,1);assert.equal(b,1);hold.resolve();await turn();''')


if __name__=='__main__':unittest.main()
