"""A real copy/register/publication/cutover fixture for Custom recovery."""
import importlib.util
import json
import pathlib
import unittest
from unittest import mock
import test_remint_box_fleet as fleet

SPEC = importlib.util.spec_from_file_location('dispatch_recovery', pathlib.Path(__file__).with_name('custom-dispatch-recovery.py'))
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


class DispatchTests(fleet.RemintTests):
    def recovery(self):
        recovery = M.Recovery(self.root, self.r, sleep=lambda _: None, clock=lambda: 100.0)
        recovery.module = fleet.M
        self.write(self.root/'computer-recovery-state.json', {'pendingHostReload': False})
        return recovery

    def test_full_handoff_preserves_data_and_routines_and_converges(self):
        recovery = self.recovery()
        self.assertEqual(recovery.run()['phase'], 'bound-box')
        current = self.r.read()
        self.assertEqual(current['entries'][0]['phase'], 'active')
        self.assertTrue(current['entries'][0]['routinesRestored'])
        self.assertEqual(json.loads((self.root/'openbot-bot-models.json').read_text())['reasoning'][fleet.NEW], 'xhigh')
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fleet.NEW])
        self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'] is False)
        count = sum(name=='CreateGrokBotAgent' for name,_ in self.calls)
        recovery = self.recovery()
        self.assertEqual(recovery.run()['phase'], 'bound-box')
        self.assertEqual(sum(name=='CreateGrokBotAgent' for name,_ in self.calls), count)
        manifest=json.loads((self.root/'openbot-publication-bindings.json').read_text())
        self.assertEqual(manifest['boxAgents'], {fleet.NEW:'456'})
        self.assertFalse(recovery.progress['nativeAppDeliveryVerified'])

    def test_unpublished_history_never_hides_source_and_resume_uses_same_id(self):
        self.server_transcripts[fleet.NEW]=[]
        recovery=self.recovery()
        self.assertEqual(recovery.run()['phase'],'waiting-for-publication')
        self.assertEqual(self.r.read()['entries'][0]['phase'],'registered-box')
        self.assertTrue(json.loads((self.source/'automations/routine/automation.json').read_text())['enabled'])
        self.assertFalse(any(name=='UpdateGrokBotUserRuntimeSettings' for name,_ in self.calls))
        creates=sum(name=='CreateGrokBotAgent' for name,_ in self.calls)
        self.server_transcripts[fleet.NEW]=[{'seq':'1','body':__import__('base64').b64encode(json.dumps(
            {'id':'message-1','timestampMs':1,'agentId':fleet.NEW}).encode()).decode()}]
        self.assertEqual(self.recovery().run()['phase'],'bound-box')
        self.assertEqual(sum(name=='CreateGrokBotAgent' for name,_ in self.calls),creates)

    def test_all_active_resume_still_finishes_interrupted_preferences(self):
        self.r.stage(fleet.OLD);self.r.register(fleet.OLD)
        original=self.r.m.rpc
        def interrupted(method,body):
            if method=='SetGrokBotMainAgent':raise RuntimeError('interrupted')
            return original(method,body)
        self.r.m.rpc=interrupted
        with self.assertRaisesRegex(RuntimeError,'interrupted'):self.r.activate()
        self.assertEqual(self.r.read()['entries'][0]['phase'],'active')
        self.r.m.rpc=original
        creates=sum(name=='CreateGrokBotAgent' for name,_ in self.calls)
        self.assertEqual(self.recovery().run()['phase'],'bound-box')
        self.assertTrue(self.r.read()['preferencesFinalized'])
        self.assertEqual(self.server_settings['mainAgentId'],fleet.NEW)
        self.assertEqual(self.host_settings['pinnedAgentIds'],[fleet.NEW])
        self.assertEqual(sum(name=='CreateGrokBotAgent' for name,_ in self.calls),creates)

    def test_official_never_starts_or_mutates_handoff(self):
        (self.root/'openbot-mode').write_text('official')
        before=(self.root/'openbot-box-remint.json').read_bytes()
        with mock.patch.object(M.subprocess,'Popen',side_effect=AssertionError('must not spawn')):
            self.assertEqual(M.maybe_start(self.root)['phase'],'disabled')
        with self.assertRaisesRegex(RuntimeError,'withdrawn'):self.recovery().run()
        self.assertEqual((self.root/'openbot-box-remint.json').read_bytes(),before)
        self.assertEqual(self.calls,[])

    def test_mode_change_during_rate_backoff_stops_before_retry(self):
        recovery=self.recovery();calls=[]
        def call(method, body):
            calls.append(method)
            raise RuntimeError("ListGrokBotAgents: {'http': 429, 'isRetryable': True}")
        def sleep(seconds):
            if seconds>=5:(self.root/'openbot-mode').write_text('official')
        recovery.sleep=sleep
        with self.assertRaisesRegex(RuntimeError,'withdrawn'):recovery.pace(call)('ListGrokBotAgents',{})
        self.assertEqual(calls,['ListGrokBotAgents'])

    def test_rate_limit_recovers_and_auth_errors_do_not_storm(self):
        recovery=self.recovery();calls=[];delays=[];recovery.sleep=delays.append
        def call(method, body):
            calls.append(method)
            if len(calls)<3:raise RuntimeError("{'http': 429, 'isRetryable': True}")
            return {'agents':[]}
        self.assertEqual(recovery.pace(call)('ListGrokBotAgents',{}),{'agents':[]})
        self.assertEqual(recovery.progress['rateLimitRetries'],2)
        self.assertIn(5,delays);self.assertIn(10,delays)
        with self.assertRaises(RuntimeError):recovery.pace(lambda *a:(_ for _ in ()).throw(RuntimeError("{'http': 401, 'private': 'credential'}")))('ListGrokBotAgents',{})
        self.assertNotIn('credential',(self.root/M.STATE).read_text())

    def test_worker_lock_excludes_duplicate_spawn(self):
        with M.lock(self.root), mock.patch.object(M.subprocess,'Popen',side_effect=AssertionError('duplicate worker')):
            self.assertTrue(M.maybe_start(self.root).get('workerRunning'))

    def test_long_cutover_renews_once_on_401_without_recreating_agents(self):
        recovery=self.recovery();self.r.m.H={'Authorization':'private'};calls=[]
        def call(method, body):
            calls.append(method)
            if len(calls)==1:raise RuntimeError("{'http': 401}")
            self.assertIsNone(self.r.m.H)
            return {'agents':[]}
        self.assertEqual(recovery.pace(call)('ListGrokBotAgents',{}),{'agents':[]})
        self.assertEqual(calls,['ListGrokBotAgents','ListGrokBotAgents'])
        rejected=[]
        def deny(method,body):
            rejected.append(method);raise RuntimeError("{'http': 401}")
        with self.assertRaises(RuntimeError):recovery.pace(deny)('ListGrokBotAgents',{})
        self.assertEqual(len(rejected),2)

    def test_transient_platform_failure_resumes_the_same_operation(self):
        recovery=self.recovery();calls=[]
        def call(method,body):
            calls.append((method,body.copy()))
            if len(calls)==1:raise RuntimeError("ListGrokBotAgentAutomations: {'http': 503}")
            return {'automations':[]}
        body={'agentId':fleet.OLD}
        self.assertEqual(recovery.pace(call)('ListGrokBotAgentAutomations',body),{'automations':[]})
        self.assertEqual(calls,[('ListGrokBotAgentAutomations',body)]*2)
        self.assertEqual(recovery.progress['phase'],'waiting-for-control-plane')

    def test_native_host_reload_gap_retries_without_changing_credentials(self):
        recovery=self.recovery();calls=[];credential={'Authorization':'private'}
        self.r.m.H=credential
        def call(method,body):
            calls.append(method)
            if len(calls)==1:raise RuntimeError('Running host renewal credential missing')
            return {'agents':[]}
        self.assertEqual(recovery.pace(call)('ListGrokBotAgents',{}),{'agents':[]})
        self.assertEqual(len(calls),2)
        self.assertIs(self.r.m.H,credential)

    def test_opt_in_cannot_be_a_symlink(self):
        flag=self.root/'openbot-local-only.enable';flag.unlink();flag.symlink_to(self.root/'openbot-mode')
        self.assertFalse(M.allowed(self.root))


if __name__=='__main__':unittest.main()
