"""A real copy/register/publication/cutover fixture for Custom recovery."""
import importlib.util
import base64
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

    def official_round_trip(self):
        self.recovery().run()
        # A fresh worker has fresh adapters; the prior Custom worker has exited.
        self.r.m.rpc = self.rpc; self.r.m.gateway = self.gateway
        spec = importlib.util.spec_from_file_location('roundtrip_official', pathlib.Path(__file__).with_name('official-dispatch-recovery.py'))
        official = importlib.util.module_from_spec(spec); spec.loader.exec_module(official)
        (self.root/'openbot-mode').write_text('official')
        official.Recovery(self.root, self.r).run()
        (self.root/'openbot-mode').write_text('custom')
        self.calls.clear(); self.gateway_calls.clear()

    def test_return_from_official_reuses_the_same_verified_copy_and_original_sections(self):
        self.official_round_trip()
        journal = self.r.read(); pair = [(e['oldId'], e['newId']) for e in journal['entries']]
        protected = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file() and p.name in ('store.db','automation.json','openbot-bot-models.json')}
        result = self.recovery().run()
        self.assertEqual(result['phase'], 'bound-box')
        self.assertFalse(any(name.startswith('Create') for name, _ in self.calls))
        self.assertEqual([(e['oldId'], e['newId']) for e in self.r.read()['entries']], pair)
        self.assertEqual(self.r.read().get('previousGenerations'), journal.get('previousGenerations'))
        self.assertEqual({p:p.read_bytes() for p in protected}, protected)
        self.assertEqual(self.server_settings['sidebarSections']['sections'][0]['agentIds'], [fleet.NEW, fleet.OTHER])
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fleet.NEW])
        self.assertEqual(self.server_settings['mainAgentId'], fleet.NEW)
        self.assertTrue(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertFalse(json.loads((self.root/'agents'/fleet.NEW/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertFalse(any(name=='setAgentAutomationEnabled' for name,_ in self.gateway_calls))
        self.assertFalse(result['nativeAppDeliveryVerified'])

    def test_official_messages_absent_from_the_copy_refuse_without_minting_or_hiding(self):
        self.official_round_trip()
        journal = (self.root/'openbot-box-remint.json').read_bytes()
        late = {'id':'official-late-message', 'agentId':fleet.OLD, 'timestampMs':2, 'kind':'message', 'content':'New source history'}
        self.server_transcripts[fleet.OLD] = [{'seq':'1','body':base64.b64encode(json.dumps(late).encode()).decode()}]
        with self.assertRaisesRegex(RuntimeError, 'uncopied history'): self.recovery().run()
        self.assertEqual((self.root/'openbot-box-remint.json').read_bytes(), journal)
        self.assertFalse(any(name.startswith('Create') or name=='SetGrokBotAgentClientState' for name,_ in self.calls))
        self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertTrue(json.loads((self.root/'agents'/fleet.NEW/'settings.json').read_text())['hiddenFromSidebar'])

    def test_return_keeps_later_copy_routine_pauses_and_independent_reasoning_choices(self):
        self.official_round_trip()
        path = self.root/'agents'/fleet.NEW/'automations/routine/automation.json'
        self.write(path, {**json.loads(path.read_text()), 'enabled':False})
        models = self.root/'openbot-bot-models.json';value = json.loads(models.read_text());value['reasoning'][fleet.NEW]='max';self.write(models,value)
        before = models.read_bytes()
        self.assertEqual(self.recovery().run()['phase'], 'bound-box')
        self.assertFalse(json.loads(path.read_text())['enabled'])
        self.assertEqual(models.read_bytes(), before)

    def test_changed_instructions_or_ownership_never_hide_an_unmerged_source(self):
        self.official_round_trip()
        path = self.source/'profile.json';self.write(path,{**json.loads(path.read_text()),'description':'New source instructions'})
        with self.assertRaisesRegex(RuntimeError,'instructions differ'):self.recovery().run()
        self.assertFalse(any(name=='SetGrokBotAgentClientState' or name.startswith('Create') for name,_ in self.calls))
        self.r.m.rpc=self.rpc;self.r.m.gateway=self.gateway;self.calls.clear()
        self.roster[0]['viewerIsOwner']=False
        with self.assertRaisesRegex(RuntimeError,'Source ownership'):self.r.reselect_active()
        self.assertFalse(any(name=='SetGrokBotAgentClientState' or name.startswith('Create') for name,_ in self.calls))

    def test_same_id_with_different_message_content_is_not_reselection_coverage(self):
        self.official_round_trip()
        changed = {'id':'message-1','timestampMs':1,'agentId':fleet.OLD,'content':'Divergent same-ID body'}
        self.server_transcripts[fleet.OLD] = [{'seq':'1','body':base64.b64encode(json.dumps(changed).encode()).decode()}]
        with self.assertRaisesRegex(RuntimeError,'uncopied history'):self.recovery().run()
        self.assertFalse(any(name=='SetGrokBotAgentClientState' or name.startswith('Create') for name,_ in self.calls))

    def test_quoted_identity_changes_are_not_reselection_coverage(self):
        self.official_round_trip()
        original = {'id':'message-1', 'timestampMs':1, 'agentId':fleet.OLD,
                    'content':'Keep this quoted identity: '+fleet.OLD,
                    'toolResult':{'agentId':fleet.OLD}}
        rewritten = {**original, 'agentId':fleet.NEW,
                     'content':'Keep this quoted identity: '+fleet.NEW,
                     'toolResult':{'agentId':fleet.NEW}}
        with fleet.contextlib.closing(fleet.sqlite3.connect(self.source/'store.db')) as db:
            db.execute('UPDATE transcript_entries SET entry=?', (json.dumps(original),)); db.commit()
        with fleet.contextlib.closing(fleet.sqlite3.connect(self.root/'agents'/fleet.NEW/'store.db')) as db:
            db.execute('UPDATE transcript_entries SET entry=?', (json.dumps(rewritten),)); db.commit()
        self.server_transcripts[fleet.NEW] = [{'seq':'1','body':base64.b64encode(json.dumps(rewritten).encode()).decode()}]
        peer_settings = self.root/'agents'/fleet.NEW/'settings.json'
        protected = {path:path.read_bytes() for path in (self.root/'openbot-box-remint.json',
                     self.source/'settings.json', peer_settings)}
        with self.assertRaisesRegex(RuntimeError, 'uncopied history'):
            self.recovery().run()
        self.assertEqual({path:path.read_bytes() for path in protected}, protected)
        self.assertFalse(any(name=='SetGrokBotAgentClientState' or name.startswith('Create') for name,_ in self.calls))

    def test_mode_change_during_existing_copy_visibility_stops_the_remaining_handoff(self):
        self.official_round_trip();original = self.rpc
        def switching(method,body):
            result=original(method,body)
            if method=='SetGrokBotAgentClientState':(self.root/'openbot-mode').write_text('official')
            return result
        self.r.m.rpc=switching
        with self.assertRaisesRegex(RuntimeError,'Custom'):self.recovery().run()
        self.assertEqual(sum(name=='SetGrokBotAgentClientState' for name,_ in self.calls),1)
        self.assertFalse(any(name=='setAgentHiddenFromSidebar' for name,_ in self.gateway_calls))

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
