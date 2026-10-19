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

    def test_known_late_tail_is_repaired_before_any_activation_and_republished(self):
        recovery = self.recovery(); original = M.load
        checker = mock.Mock(); checker.source_freshness.side_effect = RuntimeError(
            'Source history changed after reviewed merge; incremental preservation required before activation')
        tail = mock.Mock(); tail.pending.return_value=False;tail.waiting_for_reload.return_value=False; tail.FRESHNESS_ERROR = str(checker.source_freshness.side_effect)
        tail.TailRepair.return_value.run.return_value = {'phase':'waiting-for-publication','importedMessages':1}
        def loader(name):
            return checker if name == 'history-merge-receipt' else tail if name == 'history-tail-repair' else original(name)
        with mock.patch.object(M,'load',side_effect=loader), mock.patch.object(self.r,'activate') as activate, mock.patch.object(self.r,'publish',wraps=self.r.publish) as publish:
            result = recovery.run()
        self.assertEqual(result['phase'],'waiting-for-publication')
        self.assertEqual(result['lateHistoryRepair']['importedMessages'],1)
        self.assertEqual(tail.TailRepair.return_value.run.call_count,1)
        activate.assert_not_called();self.assertEqual(publish.call_count,2)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'registered-box')

    def test_prepared_tail_transaction_resumes_before_publication_verification(self):
        recovery=self.recovery();self.r.stage(fleet.OLD);self.r.register(fleet.OLD)
        original=M.load;tail=mock.Mock();tail.pending.return_value=True
        tail.TailRepair.return_value.run.return_value={'phase':'waiting-for-publication','importedMessages':1}
        with mock.patch.object(M,'load',side_effect=lambda name:tail if name=='history-tail-repair' else original(name)), mock.patch.object(self.r,'verify') as verify, mock.patch.object(self.r,'activate') as activate:
            result=recovery.run()
        self.assertEqual(result['phase'],'waiting-for-publication');verify.assert_not_called();activate.assert_not_called()
        tail.TailRepair.return_value.run.assert_called_once()

    def test_published_ui_cannot_activate_before_tail_sdk_reload_ack(self):
        recovery=self.recovery();self.r.stage(fleet.OLD);self.r.register(fleet.OLD)
        original=M.load;tail=mock.Mock();tail.pending.return_value=False;tail.waiting_for_reload.return_value=True
        with mock.patch.object(M,'load',side_effect=lambda name:tail if name=='history-tail-repair' else original(name)), mock.patch.object(self.r,'verify') as verify, mock.patch.object(self.r,'activate') as activate:
            result=recovery.run()
        self.assertEqual(result['phase'],'waiting-for-publication');verify.assert_not_called();activate.assert_not_called()

    def test_other_history_failure_never_invokes_automatic_tail_writer(self):
        recovery = self.recovery(); original = M.load
        checker = mock.Mock(); checker.source_freshness.side_effect = RuntimeError('Immutable merge evidence changed')
        tail = mock.Mock();tail.pending.return_value=False;tail.waiting_for_reload.return_value=False;tail.FRESHNESS_ERROR = 'a different exact known error'
        def loader(name):
            return checker if name=='history-merge-receipt' else tail if name=='history-tail-repair' else original(name)
        with mock.patch.object(M,'load',side_effect=loader), mock.patch.object(self.r,'activate') as activate, self.assertRaisesRegex(RuntimeError,'Immutable'):
            recovery.run()
        tail.TailRepair.assert_not_called();activate.assert_not_called()

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

    def test_refresh_publication_authority_requires_no_journal_and_only_reads_roster(self):
        (self.root/'openbot-box-remint.json').unlink()
        self.roster.extend([fleet.row(fleet.NEW, 'box', '456'),
                            {**fleet.row(fleet.OTHER, 'box', '789'), 'viewerIsOwner': False}])
        protected = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.recovery().refresh_publication_bindings()
        self.assertEqual(result['phase'], 'publication-bindings-refreshed')
        self.assertEqual(result['boxAgentCount'], 1)
        self.assertFalse(result['nativeAppDeliveryVerified'])
        self.assertEqual(json.loads((self.root/'openbot-publication-bindings.json').read_text())['boxAgents'],
                         {fleet.NEW: '456'})
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])
        self.assertEqual(self.gateway_calls, [])
        self.assertEqual({p: p.read_bytes() for p in protected}, protected)
        self.assertFalse((self.root/'openbot-box-remint.json').exists())

    def test_refresh_refuses_duplicate_or_invalid_box_authority_without_overwriting_manifest(self):
        manifest = self.root/'openbot-publication-bindings.json'
        self.write(manifest, {'schemaVersion': 1, 'boxAgents': {'existing': '456'}})
        before = manifest.read_bytes()
        invalid = [
            [fleet.row(fleet.NEW, 'box', '456'), fleet.row(fleet.NEW, 'box', '456')],
            [{**fleet.row(fleet.NEW, 'box', '456'), 'legacyAgentId': fleet.OLD}],
            [fleet.row(fleet.NEW, 'box', 'not-numeric')],
            [None],
        ]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                self.recovery().refresh_publication_bindings(rows)
            self.assertEqual(manifest.read_bytes(), before)

    def test_refresh_does_not_publish_authority_after_mode_changes_during_roster_read(self):
        original = self.r.rows
        def changed():
            result = original()
            (self.root/'openbot-mode').write_text('official')
            return result
        self.r.rows = changed
        with self.assertRaisesRegex(RuntimeError, 'withdrawn'):
            self.recovery().refresh_publication_bindings()
        self.assertFalse((self.root/'openbot-publication-bindings.json').exists())

    def test_refresh_respects_existing_manifest_symlink_and_does_not_change_target(self):
        target = self.root/'target.json'; target.write_text('protected')
        (self.root/'openbot-publication-bindings.json').symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            self.recovery().refresh_publication_bindings()
        self.assertEqual(target.read_text(), 'protected')

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
            self.assertTrue(M.maybe_start(self.root).get('lockHeld'))

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

    def test_requested_resume_keeps_job_and_journal_and_passes_writer_lock(self):
        state = {'jobId': '00000000-0000-4000-8000-000000000001',
                 'phase': 'review-needed', 'nextCheckAtMs': 9999999999999}
        self.write(self.root/M.STATE, state)
        protected = {p:p.read_bytes() for p in (self.root/'openbot-box-remint.json',
                                               self.root/'openbot-mode', self.source/'settings.json')}
        with mock.patch.object(M.subprocess, 'Popen') as start:
            start.return_value.pid = 123
            self.assertEqual(M.maybe_start(self.root)['phase'], 'review-needed')
            start.assert_not_called()
            result = M.maybe_start(self.root, requested=True)
            self.assertEqual(result['jobId'], state['jobId'])
            self.assertEqual(result['phase'], 'starting')
            self.assertIn('--worker-lock-fd', start.call_args.args[0])
            self.assertEqual(len(start.call_args.kwargs['pass_fds']), 1)
        self.assertEqual({p:p.read_bytes() for p in protected}, protected)
        recovery = self.recovery()
        self.assertEqual(recovery.progress['jobId'], state['jobId'])
        self.assertEqual(recovery.run()['counts'], {'planned':0, 'copied':0, 'registered-box':0, 'active':1})

    def test_explicit_retry_changes_only_failed_preparation_job_and_preserves_marker(self):
        state={'jobId':'00000000-0000-4000-8000-000000000001','phase':'review-needed'}
        journal=self.r.read();marker=pathlib.Path(journal['backup'])/('bounded-tail-job-'+state['jobId']+'.json')
        self.write(marker,{'phase':'review-needed','jobId':state['jobId']});before=marker.read_bytes()
        self.assertEqual(M.requested_job(self.root,state,False),state['jobId'])
        self.assertNotEqual(M.requested_job(self.root,state,True),state['jobId'])
        self.assertEqual(marker.read_bytes(),before)
        for phase in('preparing','prepared','completed','accepted','waiting-for-publication'):
            self.write(marker,{'phase':phase,'jobId':state['jobId']})
            self.assertEqual(M.requested_job(self.root,state,True),state['jobId'])
        self.write(marker,{'phase':'review-needed','jobId':state['jobId'],'intents':[]})
        self.assertEqual(M.requested_job(self.root,state,True),state['jobId'])
        self.write(marker,{'phase':'review-needed','jobId':'other'})
        self.assertEqual(M.requested_job(self.root,state,True),state['jobId'])

    def test_status_reports_real_lock_and_diagnostic_dependency(self):
        self.assertFalse(M.snapshot(self.root)['workerRunning'])
        with M.lock(self.root):
            result = M.snapshot(self.root)
            self.assertTrue(result['lockHeld'])
            self.assertFalse(result['workerRunning'])
            self.assertEqual(result['diagnosticAssistant'], 'disabled')
        (self.root/'openbot-mode').write_text('official')
        self.assertFalse(M.snapshot(self.root)['enabled'])
        with mock.patch.object(M.subprocess, 'Popen', side_effect=AssertionError('Official cannot spawn')):
            self.assertEqual(M.maybe_start(self.root, requested=True)['phase'], 'disabled')

    def test_refusal_is_reviewable_and_private_error_is_not_public_progress(self):
        recovery = self.recovery()
        with mock.patch.object(recovery.r, 'stage', side_effect=RuntimeError('SDK history conflict private-detail')):
            with mock.patch.object(M, 'Recovery', return_value=recovery), mock.patch('sys.argv', ['recovery', '--root', str(self.root)]):
                with self.assertRaises(SystemExit): M.main()
        state = json.loads((self.root/M.STATE).read_text())
        self.assertEqual(state['phase'], 'review-needed')
        self.assertEqual(state['refusalPhase'], 'copying')
        self.assertEqual(state['diagnosticCode'], 'history-conflict')
        self.assertNotIn('private-detail', json.dumps(state))
        with mock.patch.object(M.subprocess, 'Popen', side_effect=AssertionError('Review must not auto-retry')):
            self.assertEqual(M.maybe_start(self.root, now=99999999999999)['phase'], 'review-needed')

    def test_writer_lock_survives_request_parent_return(self):
        real_start = M.subprocess.Popen
        workers = []
        def isolated_worker(argv, **kwargs):
            # Keep only the inherited lock alive; this fixture must not invoke
            # the production worker or any network/host operation.
            child = real_start(['/usr/bin/python3', '-c', 'import time; time.sleep(10)'], **kwargs)
            workers.append(child)
            return child
        try:
            with mock.patch.object(M.subprocess, 'Popen', side_effect=isolated_worker):
                M.maybe_start(self.root, requested=True)
            with self.assertRaises(BlockingIOError):
                with M.lock(self.root): pass
            self.assertTrue(M.snapshot(self.root)['lockHeld'])
            self.assertFalse(M.snapshot(self.root)['workerRunning'])
        finally:
            for child in workers:
                child.terminate(); child.wait(timeout=5)
        self.assertFalse(M.snapshot(self.root)['workerRunning'])

    def test_status_refreshes_registered_counts_instead_of_stale_success(self):
        journal = self.r.read()
        journal['entries'] = [{**journal['entries'][0], 'phase': 'registered-box',
            'oldId': '00000000-0000-4000-8000-' + str(i + 100).zfill(12),
            'newId': '00000000-0000-4000-8000-' + str(i + 200).zfill(12)} for i in range(34)]
        self.write(self.root/'openbot-box-remint.json', journal)
        self.write(self.root/M.STATE, {'phase':'bound-box', 'agentCount':99, 'counts':{'active':99},
                                     'nativeAppDeliveryVerified':True, 'billingVerified':True})
        before = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with M.lock(self.root):
            state = M.snapshot(self.root)
        self.assertEqual(state['phase'], 'waiting-for-publication')
        self.assertEqual(state['agentCount'], 34)
        self.assertEqual(state['counts'], {'planned':0, 'copied':0, 'registered-box':34, 'active':0})
        self.assertEqual(state['verifiedAgents'], 0)
        self.assertTrue(state['lockHeld'])
        self.assertFalse(state['workerRunning'])
        self.assertFalse(state['publicationVerified'])
        self.assertFalse(state['nativeAppDeliveryVerified'])
        self.assertFalse(state['billingVerified'])
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        self.assertEqual(self.calls, [])

    def test_completed_status_requires_current_journal_and_exact_publication_receipt(self):
        self.recovery().run()
        state = M.snapshot(self.root)
        self.assertEqual(state['phase'], 'bound-box')
        self.assertTrue(state['publicationVerified'])
        proof = self.root/'openbot-custom-dispatch-verification.json'
        self.write(proof, {'results':[{'oldId':fleet.OTHER, 'newId':fleet.NEW, 'status':'verified-box'}]})
        stale = M.snapshot(self.root)
        self.assertEqual(stale['phase'], 'waiting-for-publication')
        self.assertFalse(stale['publicationVerified'])
        self.assertEqual(stale['counts']['active'], 1)
        self.assertEqual(stale['verifiedAgents'], 0)

    def test_worker_identity_must_match_owned_process_script_and_root(self):
        import io
        argv = ['/usr/bin/python3', str(M.HERE/'custom-dispatch-recovery.py'), '--root', str(self.root), '--worker-lock-fd', '3']
        with mock.patch.object(pathlib.Path, 'open', return_value=io.BytesIO(('\0'.join(argv)+'\0').encode())):
            self.assertTrue(M.verified_worker(self.root, {'workerPid':M.os.getpid()}))
        argv[3] = '/tmp/other-data'
        with mock.patch.object(pathlib.Path, 'open', return_value=io.BytesIO(('\0'.join(argv)+'\0').encode())):
            self.assertFalse(M.verified_worker(self.root, {'workerPid':M.os.getpid()}))
        self.assertFalse(M.verified_worker(self.root, {'workerPid':-1}))

    def test_refresh_cli_dispatch_survives_transfer_cli_integration(self):
        recovery = self.recovery()
        with mock.patch.object(M, 'Recovery', return_value=recovery), mock.patch.object(recovery, 'refresh_publication_bindings', return_value={'phase':'publication-bindings-refreshed'}) as refresh:
            with mock.patch('sys.argv', ['recovery', '--root', str(self.root), '--refresh-publication-bindings']), mock.patch('builtins.print'):
                M.main()
            refresh.assert_called_once_with()

    def add_untracked_source(self, local=True, hidden=False, owner=True):
        self.roster.append({**fleet.row(fleet.OTHER, 'temporal', '789'), 'viewerIsOwner':owner})
        if local:
            self.write(self.root/'agents'/fleet.OTHER/'profile.json', {'harness':'box', 'serverId':'789', 'name':'Additional source'})
            self.write(self.root/'agents'/fleet.OTHER/'settings.json', {'hiddenFromSidebar':hidden})

    def test_visible_source_outside_plan_refuses_before_copy_without_changing_authority(self):
        self.add_untracked_source()
        recovery = self.recovery()
        protected = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(self.r, 'stage', side_effect=AssertionError('Must not copy an incomplete source scope')):
            result = recovery.run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['untrackedSourceAgents'], 1)
        self.assertEqual(result['untrackedVisibleAgents'], 1)
        self.assertEqual(result['unknownSourceAgents'], 0)
        self.assertFalse(result['rosterComplete'])
        self.assertEqual({p:p.read_bytes() for p in protected}, protected)
        self.assertFalse(any(method.startswith('Create') or method=='SetGrokBotAgentClientState' for method,_ in self.calls))

    def test_cloud_only_source_is_counted_and_initial_plan_is_not_created_blindly(self):
        self.add_untracked_source(local=False)
        (self.root/'openbot-box-remint.json').unlink()
        with mock.patch.object(self.r, 'initialize', side_effect=AssertionError('Cloud-only source requires a complete importer')):
            result = self.recovery().run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['untrackedSourceAgents'], 1)
        self.assertEqual(result['unknownSourceAgents'], 1)
        self.assertFalse((self.root/'openbot-box-remint.json').exists())
        self.assertFalse(any(method.startswith('Create') for method,_ in self.calls))

    def test_known_owned_hidden_archives_do_not_block_or_get_resurrected(self):
        for i in range(200):
            aid = '00000000-0000-4000-8000-' + str(i + 1000).zfill(12)
            sid = str(i + 1000)
            self.roster.append(fleet.row(aid, 'temporal', sid))
            self.write(self.root/'agents'/aid/'profile.json', {'harness':'temporal', 'serverId':sid})
            self.write(self.root/'agents'/aid/'settings.json', {'hiddenFromSidebar':True})
        recovery = self.recovery(); before = (self.root/'openbot-box-remint.json').read_bytes()
        self.assertTrue(recovery.check_roster_scope(self.r.read()))
        self.assertEqual(recovery.progress['hiddenArchiveAgents'], 200)
        self.assertEqual(recovery.progress['untrackedSourceAgents'], 0)
        self.assertEqual((self.root/'openbot-box-remint.json').read_bytes(), before)
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])

    def test_hidden_setting_without_matching_binding_and_unknown_owner_need_review(self):
        self.add_untracked_source(hidden=True)
        self.write(self.root/'agents'/fleet.OTHER/'profile.json', {'harness':'box', 'serverId':'different'})
        result = self.recovery().run()
        self.assertEqual(result['unknownSourceAgents'], 1)
        self.assertEqual(result['hiddenArchiveAgents'], 0)
        self.roster[-1]['viewerIsOwner'] = None
        result = self.recovery().run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['unknownSourceAgents'], 1)

    def test_explicit_nonowned_temporal_source_does_not_join_owner_transfer(self):
        self.add_untracked_source(local=False, owner=False)
        recovery = self.recovery()
        self.assertTrue(recovery.check_roster_scope(self.r.read()))
        self.assertEqual(recovery.progress['untrackedSourceAgents'], 0)

    def test_owned_box_is_not_reminted_but_unknown_routing_or_owner_is_reviewed(self):
        self.roster.append(fleet.row(fleet.OTHER, 'box', '789'))
        recovery = self.recovery()
        self.assertTrue(recovery.check_roster_scope(self.r.read()))
        self.write(self.root/'agents'/fleet.OTHER/'profile.json', {'harness':'box', 'serverId':'789'})
        self.write(self.root/'agents'/fleet.OTHER/'settings.json', {'hiddenFromSidebar':True})
        self.roster[-1]['harness'] = 'unknown-routing'
        self.assertFalse(recovery.check_roster_scope(self.r.read()))
        self.assertEqual(recovery.progress['unknownSourceAgents'], 1)
        self.roster[-1].update(harness='box', viewerIsOwner=None)
        self.assertFalse(recovery.check_roster_scope(self.r.read()))
        self.assertEqual(recovery.progress['unknownSourceAgents'], 1)

    def test_known_historical_aliases_remain_covered_without_a_new_generation(self):
        journal = self.r.read()
        terminal = '00000000-0000-4000-8000-000000000004'
        journal['previousGenerations'] = [{'entries':[{'oldId':fleet.OTHER, 'newId':terminal,
            'sourceServerId':'789', 'serverId':'790', 'phase':'active'}]}]
        self.write(self.root/'openbot-box-remint.json', journal)
        self.add_untracked_source(local=False)
        recovery = self.recovery(); before = (self.root/'openbot-box-remint.json').read_bytes()
        self.assertTrue(recovery.check_roster_scope(self.r.read()))
        self.assertEqual(recovery.progress['untrackedSourceAgents'], 0)
        self.assertEqual((self.root/'openbot-box-remint.json').read_bytes(), before)

    def test_completed_cohort_does_not_replace_current_authority_for_new_sources(self):
        self.recovery().run(); self.add_untracked_source()
        before = (self.root/'openbot-box-remint.json').read_bytes()
        protected = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file() and p.name in ('store.db','profile.json','settings.json','automation.json')}
        with mock.patch.object(self.r, 'initialize', side_effect=AssertionError('Must retain the current BOX cohort')):
            result = self.recovery().run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['counts']['active'], 1)
        self.assertEqual(result['untrackedVisibleAgents'], 1)
        self.assertEqual((self.root/'openbot-box-remint.json').read_bytes(), before)
        self.assertEqual({p:p.read_bytes() for p in protected}, protected)

    def test_source_appearing_mid_copy_is_detected_before_activation(self):
        stage = self.r.stage
        def with_new_source(aid):
            result = stage(aid); self.add_untracked_source(); return result
        with mock.patch.object(self.r, 'stage', side_effect=with_new_source), mock.patch.object(self.r, 'activate', side_effect=AssertionError('Incomplete fleet must not activate')):
            result = self.recovery().run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['counts']['registered-box'], 1)
        self.assertEqual(result['untrackedSourceAgents'], 1)
        self.assertEqual([(e['oldId'],e['newId']) for e in self.r.read()['entries']], [(fleet.OLD,fleet.NEW)])
        self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])

    def test_source_appearing_after_activation_prevents_global_completion(self):
        restore = self.r.restore_routines
        def with_new_source():
            result = restore(); self.add_untracked_source(); return result
        with mock.patch.object(self.r, 'restore_routines', side_effect=with_new_source):
            result = self.recovery().run()
        self.assertEqual(result['phase'], 'review-needed')
        self.assertEqual(result['counts']['active'], 1)
        self.assertEqual(result['untrackedSourceAgents'], 1)
        self.assertEqual(len(self.r.read()['entries']), 1)
        self.assertFalse(M.snapshot(self.root)['rosterComplete'])

    def test_publication_proof_does_not_replace_a_fresh_whole_roster_check(self):
        self.recovery().run()
        state = M.snapshot(self.root)
        self.assertTrue(state['rosterComplete'])
        with mock.patch.object(M.time, 'time', return_value=(state['rosterCheckedAtMs']+M.ROSTER_SCOPE_TTL_MS)/1000):
            expired = M.snapshot(self.root)
        self.assertTrue(expired['publicationVerified'])
        self.assertFalse(expired['rosterComplete'])
        self.assertEqual(expired['phase'], 'checking-bindings')
        self.assertFalse(expired['nativeAppDeliveryVerified'])

    def test_opt_in_cannot_be_a_symlink(self):
        flag=self.root/'openbot-local-only.enable';flag.unlink();flag.symlink_to(self.root/'openbot-mode')
        self.assertFalse(M.allowed(self.root))

    def test_refusal_persists_before_starting_optional_advisor(self):
        recovery = self.recovery()
        recovery.progress['jobId'] = '00000000-0000-4000-8000-000000000001'
        def started(root):
            saved = json.loads((root/M.STATE).read_text())
            self.assertEqual(saved['phase'], 'review-needed')
            self.assertEqual(saved['diagnosticCode'], 'history-conflict')
            return True
        with mock.patch.object(M, 'Recovery', return_value=recovery), \
             mock.patch.object(recovery, 'run', side_effect=RuntimeError('sdk graph conflict private-data')), \
             mock.patch.object(M, 'start_diagnostic', side_effect=started) as advisor, \
             mock.patch('sys.argv', ['recovery', '--root', str(self.root)]):
            with self.assertRaises(SystemExit) as exit:
                M.main()
        self.assertEqual(exit.exception.code, 1)
        advisor.assert_called_once_with(self.root)
        self.assertEqual((self.root/'openbot-mode').read_text().strip(), 'custom')

    def test_advisor_process_is_bounded_tool_free_and_failure_never_mutates_transfer(self):
        self.write(self.root/M.STATE, {'jobId':'00000000-0000-4000-8000-000000000001',
            'phase':'review-needed','refusalPhase':'copying','diagnosticCode':'history-conflict','counts':{}})
        self.write(self.root/'openbot-plan.json', {'agents':{'*':{'providerId':'opencode','modelId':'muse'}},
            'catalog':{'providers':[{'id':'opencode','origin':'https://opencode.ai/zen/go/v1'}],
                       'models':[{'id':'opencode:muse','providerId':'opencode','slug':'muse'}]}})
        before = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(M.subprocess, 'Popen') as spawn:
            self.assertTrue(M.start_diagnostic(self.root))
            argv = spawn.call_args.args[0]
            self.assertEqual(argv[:5], ['/usr/bin/timeout', '--signal=TERM', '15s', '/usr/bin/python3', str(M.HERE/'transfer-diagnostics.py')])
            self.assertEqual(argv[5:], ['--state', str(self.root/M.STATE), '--run'])
            self.assertTrue(spawn.call_args.kwargs['close_fds'])
            self.assertNotIn('pass_fds', spawn.call_args.kwargs)
            self.assertEqual(spawn.call_args.kwargs['stdout'], M.subprocess.DEVNULL)
        with mock.patch.object(M.subprocess, 'Popen', side_effect=OSError('private-error')):
            self.assertFalse(M.start_diagnostic(self.root))
        self.assertEqual({p:p.read_bytes() for p in before}, before)
        (self.root/'openbot-mode').write_text('official')
        with mock.patch.object(M.subprocess, 'Popen') as spawn:
            self.assertFalse(M.start_diagnostic(self.root))
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
