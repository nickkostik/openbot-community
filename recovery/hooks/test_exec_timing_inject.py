import importlib.util
import json
import pathlib
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('timing', pathlib.Path(__file__).with_name('exec-timing-inject.py'))
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class ExecTimingTests(unittest.TestCase):
    def test_patch_is_idempotent_and_rejects_drift(self):
        source = timing.ANCHOR + '\nasync *run(ctx,argsSerializer){\n' + timing.BEFORE + '\n}};\n' + timing.AUDIT_BEFORE + '\n' + timing.SHELL_BEFORE
        updated = timing.patch(source)
        self.assertEqual(timing.patch(updated), updated)
        legacy = updated.replace(timing.SHELL_AFTER, timing.SHELL_BEFORE).replace(timing.AFTER, timing.LEGACY_AFTER)
        self.assertEqual(timing.patch(legacy), updated)
        for broken in [source + source, updated.replace('"consumed"', '"different"'), 'unrecognized bundle']:
            with self.assertRaises(RuntimeError):
                timing.patch(broken)

    def test_setup_phases_keep_navigation_and_approval_order(self):
        source = '''
const events=[]; const openbotExecTimingEnabled=()=>true;
const openbotExecTiming=(r,phase,start)=>events.push({phase,elapsed:Date.now()-start});
const connect6=async()=>{await new Promise(r=>setTimeout(r,30));return {remoteAccessor:{get:()=>({async *execute(){events.push({phase:'daemon'});yield {value:'result'};}})}};};
const guardAutoReviewBarrier=()=>events.push({phase:'approval'});
const awaitShellNavigationBaseline=async()=>{await new Promise(r=>setTimeout(r,20));events.push({phase:'navigation'});};
const shellStreamExecutorResource={};const markConnectionAnswered=()=>{};
const rethrowOnFreshConnection=e=>{throw e;};const probeNavigationAfterShell=()=>events.push({phase:'after'});
''' + timing.SHELL_AFTER + '''
        for await (const chunk of connection.remoteAccessor.get(shellStreamExecutorResource).execute(ctx,args,options2)){yield chunk;}
      } catch(e){rethrowOnFreshConnection(e);}finally{probeNavigationAfterShell(ctx,connection);}
    })()
  };
(async()=>{const values=[];for await(const r of boxShellStream.execute({}, {toolCallId:'owned'}, {})){values.push(r);}console.log(JSON.stringify({events,values}));})();
'''
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual([e['phase'] for e in row['events']], ['connection-start', 'connection-ready', 'approval', 'baseline-start', 'navigation', 'baseline-ready', 'approval', 'daemon', 'after'])
        self.assertGreaterEqual(row['events'][1]['elapsed'], 25)
        self.assertGreaterEqual(row['events'][5]['elapsed'] - row['events'][3]['elapsed'], 15)
        self.assertEqual(row['values'], [{'value': 'result'}])

    def test_slow_consumer_is_distinguished_without_logging_command_or_credentials(self):
        source = '''
const traces=[];const openbotExecTimingEnabled=()=>true;
const openbotExecTiming=(request,phase,start,extra={})=>traces.push({phase,elapsed:Date.now()-start,...extra});
class Manager {nextId=0;constructor(client){this.client=client;} async *run(ctx,argsSerializer){
''' + timing.AFTER + '''
}}
(async()=>{
 const client={async *exec(ctx,r){
   yield {element:{case:'execClientMessage',value:{localExecutionTimeMs:3,message:{value:{event:{case:'stdout'}}}}}};
   yield {element:{case:'execClientControlMessage',value:{message:{case:'throw',value:{error:'daemon test error',stackTrace:'original stack'}}}}};
 }};
 const manager=new Manager(client);const values=[];let error;
 try {for await(const value of manager.run({},id=>({id,message:{case:'shellStreamArgs',value:{command:'PRIVATE-COMMAND',secret:'PRIVATE-KEY'}}}))){values.push(value);await new Promise(r=>setTimeout(r,35));}}
 catch(e){error={message:e.message,stack:e.stack};}
 console.log(JSON.stringify({traces,values,error,nextId:manager.nextId}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['nextId'], 1)
        self.assertEqual(row['error'], {'message': 'daemon test error', 'stack': 'original stack'})
        self.assertEqual(len(row['values']), 1)
        received, consumed = row['traces'][1:3]
        self.assertEqual(received['daemonElapsedMs'], 3)
        self.assertGreaterEqual(consumed['elapsed'] - received['elapsed'], 25)
        self.assertEqual(row['traces'][-1]['phase'], 'closed')
        # Inspect the real formatter rather than the test trace recorder.
        formatter = timing.HELPER.split('function openbotExecTiming(request', 1)[1].split('} /*', 1)[0]
        self.assertNotIn('command', formatter)
        self.assertNotIn('secret', formatter)


if __name__ == '__main__':
    unittest.main()
