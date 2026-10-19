"""Complete-copy and cutover safety regressions, without live sends."""
import base64
import copy
import contextlib
import importlib.util
import json
import pathlib
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

SPEC=importlib.util.spec_from_file_location('remint_box_fleet',pathlib.Path(__file__).with_name('remint-box-fleet.py'))
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
OLD='00000000-0000-4000-8000-000000000001'
NEW='00000000-0000-4000-8000-000000000002'
OTHER='00000000-0000-4000-8000-000000000003'
KIND='GROK_BOT_AGENT_KIND_AGENT'


def row(aid=OLD,harness='temporal',sid='123'):
    return {'agentId':aid,'legacyAgentId':aid,'id':sid,'name':'Specialist',
            'harness':harness,'kind':KIND,'viewerIsOwner':True}


class RemintTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=pathlib.Path(t.name)
        self.r=M.Reminter()
        host=self.root/'native-host.cjs';host.write_text('fixture native host')
        for name,value in [('HOST',host),('host_pids',lambda _: [os.getpid()])]:
            patch=mock.patch.object(self.r.reload,name,value);patch.start();self.addCleanup(patch.stop)
        for mod,name,value in [(M,'SD',self.root),(M,'JOURNAL',self.root/'openbot-box-remint.json'),
                              (self.r.m,'SD',self.root)]:
            p=mock.patch.object(mod,name,value);p.start();self.addCleanup(p.stop)
        self.source=self.root/'agents'/OLD;self.source.mkdir(parents=True)
        (self.root/'openbot-local-only.enable').touch();(self.root/'openbot-mode').write_text('custom')
        self.profile={'harness':'box','serverId':'123','name':'Specialist','description':'Preserve specialist role',
                      'avatarColor':'blue','custom':{'references':OLD,'budget':8192}}
        self.write(self.source/'profile.json',self.profile)
        self.write(self.source/'settings.json',{'hiddenFromSidebar':False,'notificationsEnabled':False})
        self.write(self.root/'openbot-bot-models.json',{'assignments':{OLD:'provider:model',OTHER:'unrelated'},
                                                    'reasoning':{OLD:'xhigh'},'customDefault':'max'})
        self.write(self.source/'automations'/'routine'/'automation.json',{'enabled':True,'prompt':'Preserve routine'})
        with contextlib.closing(sqlite3.connect(self.source/'store.db')) as c:
            c.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT)')
            c.execute('INSERT INTO kv VALUES(?,?)',('metadata',json.dumps({'agentId':OLD}).encode().hex()))
            c.execute('CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,entry TEXT)')
            c.execute('INSERT INTO transcript_entries VALUES(1,?,?)',('message-1',json.dumps({'id':'message-1','timestampMs':1,'agentId':OLD})))
            c.commit()
        self.roster=[row()];self.created=row(NEW,'box','456');self.calls=[];self.gateway_calls=[]
        self.cloud_routines={}
        self.server_settings={'sidebarSections':{'sections':[{'id':'me','name':'Me','agentIds':[OLD,OTHER]},
                                                           {'id':'unassigned','name':'Unassigned'}]},
                              'pinnedAgents':{'agentIds':[OLD]},'hasSeenOnboarding':True,'mainAgentId':OLD}
        self.host_settings={'notifications':{'isEnabled':False,'allowedApps':[]},'sidebarSections':[]}
        self.server_transcripts={OLD:[],NEW:[{'seq':'1','body':base64.b64encode(json.dumps({'id':'message-1','timestampMs':1,'agentId':NEW}).encode()).decode()}]}
        self.r.m.rpc=self.rpc;self.r.m.gateway=self.gateway
        self.backup=self.root/'backup';self.backup.mkdir(mode=0o700)
        self.write(M.JOURNAL,{'backup':str(self.backup),'entries':[{'oldId':OLD,'newId':NEW,
            'sourceServerId':'123','name':'Specialist','kind':KIND,'phase':'planned'}]})

    def write(self,p,d):
        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d))

    def rpc(self,method,body):
        self.calls.append((method,copy.deepcopy(body)))
        if method=='ListGrokBotAgents':return {'agents':copy.deepcopy(self.roster)}
        if method=='ListGrokBotAgentAutomations':return {'automations':copy.deepcopy(self.cloud_routines.get(body['agentId'],[]))}
        if method=='SetGrokBotAgentAutomationEnabled':
            for wrapper in self.cloud_routines.get(body['agentId'],[]):
                if wrapper['automationId']==body['automationId']:
                    record=json.loads(wrapper['recordJson']);record['isEnabled']=body['isEnabled'];wrapper['recordJson']=json.dumps(record)
            return {}
        if method in ('CreateGrokBotAgent','CreateGrokBotRoom'):
            self.roster.append(copy.deepcopy(self.created));return {'agent':copy.deepcopy(self.created)}
        if method=='ListGrokBotTranscriptEntries':return {'generation':1,'entries':copy.deepcopy(self.server_transcripts.get(body['agentId'],[]))}
        if method=='GetGrokBotUserRuntimeSettings':return {'settings':copy.deepcopy(self.server_settings)}
        if method=='UpdateGrokBotUserRuntimeSettings':self.server_settings.update(copy.deepcopy(body));return {}
        if method=='SetGrokBotAgentClientState':return {}
        if method=='SetGrokBotMainAgent':self.server_settings['mainAgentId']=body['agentId'];return {'settings':copy.deepcopy(self.server_settings)}
        raise AssertionError(method)

    def gateway(self,method,body):
        self.gateway_calls.append((method,copy.deepcopy(body)))
        if method=='listAgents':return [{'id':NEW,'harness':'box'}]
        if method=='getHostSettings':return copy.deepcopy(self.host_settings)
        if method=='setHostSettings':self.host_settings.update(copy.deepcopy(body));return {}
        if method=='setAgentHiddenFromSidebar':
            p=self.root/'agents'/body['id']/'settings.json';d=json.loads(p.read_text())
            self.write(p,{**d,'hiddenFromSidebar':body['isHidden']});return {}
        if method=='getAgentAutomations':
            return [{'id':p.parent.name,'isEnabled':json.loads(p.read_text())['enabled']}
                    for p in (self.root/'agents'/body['id']/'automations').rglob('automation.json')]
        if method=='setAgentAutomationEnabled':
            p=self.root/'agents'/body['id']/'automations'/body['automationId']/'automation.json'
            self.write(p,{**json.loads(p.read_text()),'enabled':body['isEnabled']})
            return self.gateway('getAgentAutomations',{'id':body['id']})
        raise AssertionError(method)

    def stage_server_routine(self, enabled=True, with_local=False):
        if not with_local:
            (self.source/'automations'/'routine'/'automation.json').unlink(missing_ok=True)
        self.r.stage(OLD); self.r.register(OLD)
        record = {'id':'server-routine', 'name':'Scheduled check', 'prompt':'Check peer ' + OLD,
                  'trigger':{'type':'cron', 'schedule':'0 * * * *', 'firesAt':'derived',
                             'window':{'earlyMinutes':0,'lateMinutes':2}, 'timing':'exact'},
                  'schedule':'0 * * * *', 'triggerDescription':'Every hour',
                  'triggerPresentation':{'summary':'Hourly'}, 'provenance':'user',
                  'sessionId':'existing-session', 'isEnabled':enabled, 'createdAt':'original',
                  'lastRunAt':None,'nextRunAt':'next','runs':[], 'filePath':'old/path'}
        wrapper = {'automationId':OTHER, 'recordJson':json.dumps(record)}
        self.cloud_routines[OLD] = [copy.deepcopy(wrapper)]
        relative = 'automations/server-routine/automation.json'
        native = {key:value for key,value in record.items() if key in
                  ('name','prompt','trigger','schedule','triggerPresentation','provenance','sessionId','createdAt','lastRunAt')}
        native = self.r.m.remap_json(native, {OLD:NEW})
        native['trigger'].pop('firesAt')
        native['enabled'] = False
        self.write(self.root/'agents'/NEW/relative, native)
        authority = {'schema':'openbot-server-routine-authority/v1','oldId':OLD,'newId':NEW,
                     'automations':[{**wrapper,'nativePath':relative}]}
        path=self.backup/'server-routines.json';self.write(path,authority)
        journal=self.r.read();entry=journal['entries'][0]
        entry['serverRoutineAuthority']={'path':str(path),'sha256':M.hashlib.sha256(path.read_bytes()).hexdigest()}
        self.r.save(journal)
        return relative, path

    def test_server_only_routine_needs_no_fabricated_old_native_file(self):
        relative,_=self.stage_server_routine()
        ordering=[];rpc=self.r.m.rpc;gateway=self.r.m.gateway
        def tracked_rpc(method,body):
            if method=='SetGrokBotAgentAutomationEnabled':ordering.append('pause-cloud')
            return rpc(method,body)
        def tracked_gateway(method,body):
            if method=='setAgentAutomationEnabled':ordering.append('enable-native')
            return gateway(method,body)
        self.r.m.rpc=tracked_rpc;self.r.m.gateway=tracked_gateway
        self.r.activate(OLD)
        self.assertFalse((self.source/relative).exists())
        self.assertTrue(json.loads((self.root/'agents'/NEW/relative).read_text())['enabled'])
        self.assertEqual(ordering,['pause-cloud','enable-native'])
        self.assertTrue(self.r.read()['entries'][0]['serverRoutinesPaused'])

    def test_server_and_native_local_routines_restore_together(self):
        relative,_=self.stage_server_routine(with_local=True)
        self.r.activate(OLD)
        states=self.r.read()['entries'][0]['activationAutomationStates']
        self.assertEqual(states,{'automations/routine/automation.json':True,relative:True})

    def test_current_server_pause_overrides_captured_enabled_switch(self):
        relative,_=self.stage_server_routine()
        record=json.loads(self.cloud_routines[OLD][0]['recordJson']);record['isEnabled']=False
        record['lastRunAt']='later';record['trigger']['firesAt']='later-derived'
        self.cloud_routines[OLD][0]['recordJson']=json.dumps(record)
        self.r.activate(OLD)
        self.assertFalse(json.loads((self.root/'agents'/NEW/relative).read_text())['enabled'])
        self.assertFalse(any(name=='setAgentAutomationEnabled' for name,_ in self.gateway_calls))

    def test_server_authored_changes_refuse_before_any_cutover(self):
        for field,value in [('prompt','New instructions'),('sessionId','another-session'),('provenance','different')]:
            with self.subTest(field=field):
                relative,path=self.stage_server_routine()
                record=json.loads(self.cloud_routines[OLD][0]['recordJson']);record[field]=value
                self.cloud_routines[OLD][0]['recordJson']=json.dumps(record)
                with self.assertRaisesRegex(RuntimeError,'payload changed'):self.r.activate(OLD)
                self.assertFalse(any(name=='UpdateGrokBotUserRuntimeSettings' for name,_ in self.calls))
                # Reset the test's transfer phase for the next independent case.
                self.roster=[row()];self.calls=[]
                journal=self.r.read();journal['entries'][0].update(phase='planned')
                journal['entries'][0].pop('serverRoutineAuthority',None);self.r.save(journal)
                import shutil
                shutil.rmtree(self.root/'agents'/NEW)

    def test_server_authority_rejects_tampering_foreign_identity_and_symlink(self):
        relative,path=self.stage_server_routine()
        journal=self.r.read();entry=journal['entries'][0]
        original=path.read_bytes()
        path.write_bytes(original+b' ')
        with self.assertRaisesRegex(RuntimeError,'digest'):self.r.server_routine_states(journal,entry)
        path.write_bytes(original)
        data=json.loads(original);data['oldId']=OTHER;self.write(path,data)
        entry['serverRoutineAuthority']['sha256']=M.hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'another transfer'):self.r.server_routine_states(journal,entry)
        path.write_bytes(original);entry['serverRoutineAuthority']['sha256']=M.hashlib.sha256(original).hexdigest()
        target=self.backup/'real.json';path.rename(target);path.symlink_to(target)
        with self.assertRaises((RuntimeError, OSError)):self.r.server_routine_states(journal,entry)

    def test_server_destination_change_or_source_ownership_change_refuses(self):
        relative,_=self.stage_server_routine()
        path=self.root/'agents'/NEW/relative;before=path.read_bytes()
        definition=json.loads(before);definition['trigger']['timing']='different';self.write(path,definition)
        with self.assertRaisesRegex(RuntimeError,'configuration changed'):self.r.activate(OLD)
        path.write_bytes(before);self.roster[0]['viewerIsOwner']=False
        with self.assertRaisesRegex(RuntimeError,'ownership'):self.r.activate(OLD)

    def test_server_only_routine_restore_retries_preserve_later_native_pause(self):
        relative,_=self.stage_server_routine();self.r.activate(OLD)
        path=self.root/'agents'/NEW/relative;definition=json.loads(path.read_bytes())
        definition['enabled']=False;self.write(path,definition);before=path.read_bytes()
        self.assertEqual(self.r.restore_routines(OLD)[0]['state'],'already-restored')
        self.assertEqual(path.read_bytes(),before)

    def test_server_pause_refusal_keeps_destination_disabled_and_can_resume(self):
        relative,_=self.stage_server_routine()
        rpc=self.r.m.rpc
        def refused(method,body):
            return {} if method=='SetGrokBotAgentAutomationEnabled' else rpc(method,body)
        self.r.m.rpc=refused
        with self.assertRaisesRegex(RuntimeError,'remain paused'):self.r.activate(OLD)
        entry=self.r.read()['entries'][0]
        self.assertTrue(entry['serverRoutinePauseIntent'])
        self.assertTrue(entry['activationAutomationStates'][relative])
        self.assertFalse(json.loads((self.root/'agents'/NEW/relative).read_text())['enabled'])
        self.assertFalse(any(name=='setAgentAutomationEnabled' for name,_ in self.gateway_calls))
        self.r.m.rpc=rpc;self.r.activate(OLD)
        self.assertTrue(json.loads((self.root/'agents'/NEW/relative).read_text())['enabled'])

    def test_server_authority_cannot_overwrite_local_routine_switch_authority(self):
        _,path=self.stage_server_routine(with_local=True)
        journal=self.r.read();entry=journal['entries'][0]
        data=json.loads(path.read_bytes());wrapper=data['automations'][0]
        record=json.loads(wrapper['recordJson']);record['id']='routine'
        wrapper['recordJson']=json.dumps(record);wrapper['nativePath']='automations/routine/automation.json'
        self.write(path,data);entry['serverRoutineAuthority']['sha256']=M.hashlib.sha256(path.read_bytes()).hexdigest()
        self.cloud_routines[OLD][0]['recordJson']=wrapper['recordJson']
        self.r.save(journal)
        with self.assertRaisesRegex(RuntimeError,'collides'):self.r.activate(OLD)
        self.assertFalse(any(name=='SetGrokBotAgentAutomationEnabled' for name,_ in self.calls))

    def test_non_cron_authored_fire_time_is_not_ignored(self):
        before={'trigger':{'type':'once','firesAt':'original'}}
        after={'trigger':{'type':'once','firesAt':'changed'}}
        self.assertNotEqual(self.r.routine_semantics(before),self.r.routine_semantics(after))

    def test_added_server_routine_requires_another_reviewed_copy(self):
        self.stage_server_routine(with_local=True)
        self.cloud_routines[OLD].append({'automationId':NEW,'recordJson':json.dumps(
            {'id':'new-unreviewed','isEnabled':True})})
        with self.assertRaisesRegex(RuntimeError,'list changed'):self.r.activate(OLD)
        self.assertFalse(any(name=='SetGrokBotAgentAutomationEnabled' for name,_ in self.calls))

    def test_selection_uses_visible_owned_matching_temporal_rows_only(self):
        profiles={OLD:{'profile':self.profile,'settings':{}},OTHER:{'profile':{'harness':'box'},'settings':{}}}
        self.assertEqual(len(M.source_candidates([row()],profiles)),1)
        profiles[OLD]['settings']['hiddenFromSidebar']=True
        self.assertEqual(M.source_candidates([row()],profiles),[])
        profiles[OLD]['settings']={}
        self.assertEqual(M.source_candidates([row(harness='box')],profiles),[])
        self.assertEqual(M.source_candidates([{**row(),'name':'OpenBot Local Routing Verification'}],profiles),[])

    def test_wrong_owner_or_binding_stops_plan(self):
        for bad in [{**row(),'viewerIsOwner':False},{**row(),'legacyAgentId':OTHER},{**row(),'id':'999'}]:
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):
                M.source_candidates([bad],{OLD:{'profile':self.profile,'settings':{}}})

    def test_preferences_preserve_category_labels_and_unrelated_ids(self):
        before=copy.deepcopy(self.server_settings)
        d=M.remap_preferences(before,{OLD:NEW})
        self.assertEqual(d['sidebarSections']['sections'][0],{'id':'me','name':'Me','agentIds':[NEW,OTHER]})
        self.assertEqual(d['sidebarSections']['sections'][1]['agentIds'],[])
        self.assertEqual(d['pinnedAgents']['agentIds'],[NEW])
        self.assertEqual(before,self.server_settings)

    def test_cutover_keeps_the_source_in_its_section_until_visibility_switches(self):
        paired=M.transfer_preferences(self.server_settings,{OLD:NEW})
        self.assertEqual(paired['sidebarSections']['sections'][0]['agentIds'],[OLD,NEW,OTHER])
        self.assertEqual(M.remap_preferences(paired,{OLD:NEW})['sidebarSections']['sections'][0]['agentIds'],[NEW,OTHER])
        # Resume after a previous pass moved the positions but failed before
        # hiding the sources: they get their original section back immediately.
        final=M.remap_preferences(self.server_settings,{OLD:NEW})
        self.assertEqual(M.transfer_preferences(final,{OLD:NEW}),paired)

    def test_transitional_preferences_ignore_identity_mappings_without_losing_either_counterpart(self):
        for mapping in ({OLD:NEW, NEW:NEW}, {OLD:OLD, NEW:OLD}):
            with self.subTest(mapping=mapping):
                paired = M.transfer_preferences(self.server_settings, mapping)
                ids = paired['sidebarSections']['sections'][0]['agentIds']
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(set(ids), {OLD, NEW, OTHER})
                self.assertEqual(set(paired['pinnedAgents']['agentIds']), {OLD, NEW})
                self.assertEqual(M.remap_preferences(paired, mapping), M.remap_preferences(self.server_settings, mapping))

    def test_native_collapsed_sections_are_not_reset_by_the_platform_projection(self):
        self.host_settings['sidebarSections']=[{'id':'me','name':'Me','agentIds':[OLD,OTHER],'isCollapsed':True}]
        original_gateway=self.r.m.gateway
        def native_merge(method,body):
            if method=='setHostSettings' and 'sidebarSections' in body:
                body={**body,'sidebarSections':M.preserve_native_section_state(body['sidebarSections'],self.host_settings['sidebarSections'])}
            return original_gateway(method,body)
        self.r.m.gateway=native_merge
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        self.assertTrue(self.host_settings['sidebarSections'][0]['isCollapsed'])
        self.assertEqual(self.host_settings['sidebarSections'][0]['agentIds'],[NEW,OTHER])

    def test_final_visibility_write_can_resume_sections_and_main_pointer(self):
        self.host_settings['sidebarSections']=[{'id':'me','name':'Me','agentIds':[OLD,OTHER],'isCollapsed':True}]
        self.r.stage(OLD);self.r.register(OLD)
        original = self.r.m.rpc
        def disconnect(method, body):
            if method == 'SetGrokBotMainAgent':
                raise RuntimeError('Final control-plane write interrupted')
            return original(method, body)
        self.r.m.rpc=disconnect
        with self.assertRaisesRegex(RuntimeError,'interrupted'):
            self.r.activate(OLD)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'active')
        self.assertTrue(self.r.read()['entries'][0]['routinesRestored'])
        self.assertNotIn('preferencesFinalized',self.r.read())
        self.assertEqual(self.server_settings['mainAgentId'],OLD)
        self.assertEqual(self.host_settings['sidebarSections'][0]['agentIds'],[OLD,NEW,OTHER])
        # A section rename or pin edit made during the interruption is retained.
        self.server_settings['sidebarSections']['sections'][0]['name']='My section'
        self.server_settings['pinnedAgents']['agentIds']=[OTHER,NEW]
        self.r.m.rpc=original
        self.assertEqual(self.r.finalize_preferences()['state'],'finalized')
        self.assertEqual(self.server_settings['mainAgentId'],NEW)
        self.assertEqual(self.host_settings['sidebarSections'][0]['agentIds'],[NEW,OTHER])
        self.assertEqual(self.host_settings['sidebarSections'][0]['name'],'My section')
        self.assertTrue(self.host_settings['sidebarSections'][0]['isCollapsed'])
        self.assertEqual(self.host_settings['pinnedAgentIds'],[OTHER,NEW])
        self.assertTrue(self.r.read()['preferencesFinalized'])
        before=len(self.calls)
        self.assertEqual(self.r.finalize_preferences()['state'],'already-finalized')
        self.assertEqual(len(self.calls),before)

    def test_finalization_refuses_an_incomplete_visibility_handoff(self):
        self.r.stage(OLD);self.r.register(OLD)
        with self.assertRaisesRegex(RuntimeError,'must be active'):
            self.r.finalize_preferences()
        self.assertFalse(any(name=='UpdateGrokBotUserRuntimeSettings' for name,_ in self.calls))

    def test_reasoning_and_model_overrides_are_copied_together(self):
        before={'assignments':{OLD:'route',OTHER:'keep'},'reasoning':{OLD:'max'},'default':'high'}
        d=M.copy_bot_overrides(before,OLD,NEW)
        self.assertEqual(d['assignments'],{OLD:'route',NEW:'route',OTHER:'keep'})
        self.assertEqual(d['reasoning'],{OLD:'max',NEW:'max'})
        self.assertNotIn(NEW,before['assignments'])
        with self.assertRaises(RuntimeError):M.copy_bot_overrides({'assignments':{OLD:'a',NEW:'b'}},OLD,NEW)

    def test_next_generation_preserves_the_completed_journal_and_original_sources(self):
        previous=self.r.read();previous['entries'][0].update(oldId=OTHER,newId=OLD,phase='active');self.r.save(previous)
        profile_before=(self.source/'profile.json').read_bytes()
        second=self.r.initialize(next_generation=True)
        self.assertEqual(second['previousGenerations'],[previous])
        self.assertEqual(json.loads((pathlib.Path(second['backup'])/'previous-journal.json').read_text()),previous)
        self.assertEqual(second['entries'][0]['oldId'],OLD)
        self.assertEqual(second['entries'][0]['phase'],'planned')
        self.assertNotEqual(second['entries'][0]['newId'],NEW)
        self.assertEqual((self.source/'profile.json').read_bytes(),profile_before)
        self.assertEqual(self.r.initialize(),second)

    def test_unfinished_generation_is_never_replaced(self):
        before=M.JOURNAL.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'Finish the current'):
            self.r.initialize(next_generation=True)
        self.assertEqual(M.JOURNAL.read_bytes(),before)

    def test_activation_pauses_the_retired_cloud_scheduler_before_sidebar_cutover(self):
        self.cloud_routines[OLD]=[{'automationId':OTHER,'recordJson':json.dumps({'isEnabled':True,'prompt':'Preserve cloud routine'})}]
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        self.assertFalse(json.loads(self.cloud_routines[OLD][0]['recordJson'])['isEnabled'])
        backup=json.loads((self.backup/'retired-server-automations'/f'{OLD}.json').read_text())
        self.assertTrue(json.loads(backup['automations'][0]['recordJson'])['isEnabled'])
        methods=[name for name,_ in self.calls]
        self.assertLess(methods.index('SetGrokBotAgentAutomationEnabled'),methods.index('UpdateGrokBotUserRuntimeSettings'))

    def test_reenabled_cloud_schedule_prevents_activation(self):
        self.cloud_routines[OLD]=[{'automationId':OTHER,'recordJson':json.dumps({'isEnabled':True})}]
        original_rpc=self.r.m.rpc
        def refusal(method,body):
            if method=='SetGrokBotAgentAutomationEnabled':return {}
            return original_rpc(method,body)
        self.r.m.rpc=refusal
        self.r.stage(OLD);self.r.register(OLD)
        with self.assertRaisesRegex(RuntimeError,'remain paused'):self.r.activate(OLD)
        self.assertFalse(any(name=='UpdateGrokBotUserRuntimeSettings' for name,_ in self.calls))
        self.assertEqual(self.r.read()['entries'][0]['phase'],'registered-box')

    def test_already_disabled_cloud_schedules_need_one_authoritative_read(self):
        self.cloud_routines[OLD]=[{'automationId':OTHER,'recordJson':json.dumps({'isEnabled':False})}]
        result=self.r.pause_cloud_routines(OLD,self.backup)
        self.assertTrue(result['verifiedDisabled'])
        self.assertEqual([name for name,_ in self.calls],['ListGrokBotAgentAutomations'])

    def test_activation_restores_source_enabled_routines_through_the_native_runner(self):
        self.write(self.source/'automations'/'paused'/'automation.json',{'enabled':False,'prompt':'Keep paused'})
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        destination=self.root/'agents'/NEW/'automations'
        self.assertTrue(json.loads((destination/'routine'/'automation.json').read_text())['enabled'])
        self.assertFalse(json.loads((destination/'paused'/'automation.json').read_text())['enabled'])
        calls=[body for name,body in self.gateway_calls if name=='setAgentAutomationEnabled']
        self.assertEqual(calls,[{'id':NEW,'automationId':'routine','isEnabled':True}])
        self.assertTrue(self.r.read()['entries'][0]['routinesRestored'])

    def test_a_source_pause_after_copying_is_preserved_at_cutover(self):
        self.r.stage(OLD);self.r.register(OLD)
        self.write(self.source/'automations'/'routine'/'automation.json',{'enabled':False,'prompt':'User paused'})
        self.r.activate(OLD)
        self.assertFalse(json.loads((self.root/'agents'/NEW/'automations'/'routine'/'automation.json').read_text())['enabled'])
        self.assertFalse(any(name=='setAgentAutomationEnabled' for name,_ in self.gateway_calls))

    def test_changed_routine_list_refuses_before_retiring_or_remapping(self):
        self.r.stage(OLD);self.r.register(OLD)
        self.write(self.source/'automations'/'new-routine'/'automation.json',{'enabled':True,'prompt':'New work'})
        with self.assertRaisesRegex(RuntimeError,'list changed'):self.r.activate(OLD)
        self.assertFalse(any(name=='UpdateGrokBotUserRuntimeSettings' for name,_ in self.calls))
        self.assertTrue(json.loads((self.source/'automations'/'routine'/'automation.json').read_text())['enabled'])

    def test_restoration_retries_keep_a_later_user_pause(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        p=self.root/'agents'/NEW/'automations'/'routine'/'automation.json'
        self.write(p,{**json.loads(p.read_text()),'enabled':False})
        before=p.read_bytes();self.gateway_calls.clear()
        self.assertEqual(self.r.restore_routines(OLD)[0]['state'],'already-restored')
        self.r.activate(OLD)
        self.assertEqual(p.read_bytes(),before)
        self.assertFalse(any(name=='setAgentAutomationEnabled' for name,_ in self.gateway_calls))

    def test_native_noop_is_not_reported_as_success_and_can_be_retried(self):
        self.r.stage(OLD);self.r.register(OLD)
        original=self.r.m.gateway
        def noop(method,body):
            if method=='setAgentAutomationEnabled':return original('getAgentAutomations',{'id':body['id']})
            return original(method,body)
        self.r.m.gateway=noop
        with self.assertRaisesRegex(RuntimeError,'did not persist'):self.r.activate(OLD)
        self.assertFalse(self.r.read()['entries'][0].get('routinesRestored',False))
        self.r.m.gateway=original
        self.assertEqual(self.r.restore_routines(OLD)[0]['enabled'],1)

    def test_unactivated_copies_and_unknown_switch_paths_cannot_be_resumed(self):
        self.r.stage(OLD);self.r.register(OLD)
        with self.assertRaisesRegex(RuntimeError,'remain paused'):self.r.restore_routines(OLD)
        for states in [{'../outside':True},{'automations/routine/automation.json':1}]:
            with self.subTest(states=states),self.assertRaises(RuntimeError):
                self.r.checked_routine_states(NEW,states)

    def test_remint_history_resolves_old_group_and_peer_identifiers(self):
        journal={'previousGenerations':[{'entries':[{'oldId':OLD,'newId':NEW}]}],
                 'entries':[{'oldId':NEW,'newId':OTHER}]}
        self.assertEqual(M.identity_mapping(journal),{OLD:OTHER,NEW:OTHER})
        with self.assertRaisesRegex(RuntimeError,'cycle'):
            M.identity_mapping({'entries':[{'oldId':OLD,'newId':NEW},{'oldId':NEW,'newId':OLD}]})
        with self.assertRaisesRegex(RuntimeError,'conflicting'):
            M.identity_mapping({**journal,'entries':[{'oldId':OLD,'newId':OTHER}]})

    def test_complete_stage_preserves_source_roles_history_and_pauses_copy(self):
        source_db=(self.source/'store.db').read_bytes();source_profile=(self.source/'profile.json').read_bytes()
        e=self.r.stage(OLD);dst=self.root/'agents'/NEW
        self.assertEqual(e['phase'],'copied')
        p=json.loads((dst/'profile.json').read_text())
        self.assertNotIn('serverId',p);self.assertEqual(p['description'],self.profile['description'])
        self.assertEqual(p['custom']['references'],NEW)
        self.assertFalse(json.loads((dst/'automations'/'routine'/'automation.json').read_text())['enabled'])
        with contextlib.closing(sqlite3.connect(dst/'store.db')) as c:
            metadata=json.loads(bytes.fromhex(c.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
            self.assertEqual(metadata['agentId'],NEW)
            entry=json.loads(c.execute('SELECT entry FROM transcript_entries').fetchone()[0])
            self.assertEqual(entry['agentId'],NEW)
        self.assertEqual((self.source/'store.db').read_bytes(),source_db)
        self.assertEqual((self.source/'profile.json').read_bytes(),source_profile)
        self.assertEqual(self.r.stage(OLD),e)

    def test_fresh_box_registration_binds_matching_string_id_without_send(self):
        self.r.stage(OLD);e=self.r.register(OLD)
        self.assertEqual(e['phase'],'registered-box')
        self.assertEqual(json.loads((self.root/'agents'/NEW/'profile.json').read_text())['serverId'],'456')
        request=next(b for m,b in self.calls if m=='CreateGrokBotAgent')
        self.assertEqual((request['harness'],request['createIntent'],request['createCaller']),(1,2,4))
        self.assertFalse(request['kickstartRequested']);self.assertTrue(request['introductionSuppressed'])
        self.assertFalse(any('Send' in m or m=='sendPrompt' for m,b in self.calls+self.gateway_calls))

    def test_stage_preserves_quoted_ids_in_database_and_journal_messages(self):
        message = {'id':'message-1','timestampMs':1,'agentId':OLD,
                   'content':'User quoted ' + OLD,
                   'toolResult':{'agentId':OLD,'output':OLD}}
        with contextlib.closing(sqlite3.connect(self.source/'store.db')) as db:
            db.execute('UPDATE transcript_entries SET entry=?', (json.dumps(message),));db.commit()
        journal_dir = self.root/'agent-transcripts'/OLD
        journal_dir.mkdir(parents=True)
        text = json.dumps(message) + '\r\n{partial\n\n'
        (journal_dir/'session.jsonl').write_bytes(text.encode())
        (self.source/'notes.jsonl').write_bytes(text.encode())
        source_bytes = (self.source/'store.db').read_bytes()
        self.r.stage(OLD)
        destination = self.root/'agents'/NEW
        with contextlib.closing(sqlite3.connect(destination/'store.db')) as db:
            stored = json.loads(db.execute('SELECT entry FROM transcript_entries').fetchone()[0])
        self.assertEqual(stored, {**message,'agentId':NEW})
        for path in [destination/'notes.jsonl', self.root/'agent-transcripts'/NEW/'session.jsonl']:
            first, rest = path.read_bytes().decode().split('\r\n', 1)
            self.assertEqual(json.loads(first), {**message,'agentId':NEW})
            self.assertEqual(rest, '{partial\n\n')
        self.assertEqual((journal_dir/'session.jsonl').read_bytes(), text.encode())
        self.assertEqual((self.source/'store.db').read_bytes(), source_bytes)

    def test_temporal_wrong_id_and_wrong_kind_do_not_bind_or_hide_source(self):
        self.r.stage(OLD);before=(self.root/'agents'/NEW/'profile.json').read_bytes()
        for bad in [{**self.created,'harness':'temporal'},{**self.created,'legacyAgentId':OTHER},
                    {**self.created,'kind':'GROK_BOT_AGENT_KIND_ROOM'}]:
            with self.subTest(bad=bad):
                self.roster=[row()];self.created=bad
                with self.assertRaises(RuntimeError):self.r.register(OLD)
                self.assertEqual((self.root/'agents'/NEW/'profile.json').read_bytes(),before)
                self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
                self.assertEqual(self.r.read()['entries'][0]['phase'],'copied')

    def test_missing_or_reordered_published_history_blocks_cutover(self):
        self.r.stage(OLD);self.r.register(OLD)
        for entries in [[],[{'seq':'1','body':base64.b64encode(b'{"id":"wrong"}').decode()}]]:
            with self.subTest(entries=entries):
                self.server_transcripts[NEW]=entries
                self.assertEqual(self.r.verify()[0]['status'],'not-verified')
                with self.assertRaises(RuntimeError):self.r.activate()
                self.assertFalse(any(m=='UpdateGrokBotUserRuntimeSettings' for m,b in self.calls))

    def test_same_published_ids_with_wrong_message_bodies_block_cutover(self):
        self.r.stage(OLD);self.r.register(OLD)
        self.server_transcripts[NEW][0]['body']=base64.b64encode(json.dumps(
            {'id':'message-1','timestampMs':1,'agentId':NEW,'text':'different user conversation'}).encode()).decode()
        self.assertEqual(self.r.verify()[0]['status'],'not-verified')
        with self.assertRaises(RuntimeError):self.r.activate()
        self.assertFalse(any(m=='UpdateGrokBotUserRuntimeSettings' for m,b in self.calls))

    def test_same_id_divergent_source_and_cloud_are_preserved_and_not_registered(self):
        self.server_transcripts[OLD]=[{'seq':'1','body':base64.b64encode(json.dumps(
            {'id':'message-1','timestampMs':2,'agentId':OLD,'text':'late branch'}).encode()).decode()}]
        with self.assertRaisesRegex(RuntimeError,'Divergent same-ID history'):self.r.stage(OLD)
        with sqlite3.connect(self.root/'agents'/NEW/'store.db') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM transcript_entries').fetchone()[0],2)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'copied')
        self.assertEqual(self.r.stage(OLD)['phase'],'copied')
        with self.assertRaisesRegex(RuntimeError,'Divergent same-ID history'):self.r.register(OLD)
        self.assertFalse(any(m.startswith('Create') for m,b in self.calls))

    def test_cutover_preserves_notifications_pins_and_original_data(self):
        self.r.stage(OLD);self.r.register(OLD);before=(self.source/'store.db').read_bytes()
        self.assertEqual(self.r.verify()[0]['status'],'verified-box');self.r.activate(OLD)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'active')
        self.assertTrue(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertFalse(json.loads((self.root/'agents'/NEW/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertEqual(self.host_settings['notifications'],{'isEnabled':False,'allowedApps':[]})
        self.assertEqual(self.host_settings['pinnedAgentIds'],[NEW])
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'],[NEW])
        self.assertEqual(self.server_settings['mainAgentId'],NEW)
        self.assertIn(('SetGrokBotAgentClientState',{'agentId':NEW,'hiddenFromSidebar':False,'notificationsEnabled':False}),self.calls)
        self.assertEqual((self.source/'store.db').read_bytes(),before)
        self.assertTrue(json.loads((self.backup/'retired-automations'/OLD/'routine'/'automation.json').read_text())['enabled'])
        self.assertFalse(json.loads((self.source/'automations'/'routine'/'automation.json').read_text())['enabled'])
        self.r.register(OLD);self.assertEqual(self.r.read()['entries'][0]['phase'],'active')

    def test_wrong_metadata_identity_blocks_registration(self):
        self.r.stage(OLD)
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':OLD}).encode().hex(),))
            c.commit()
        with self.assertRaisesRegex(RuntimeError,'metadata'):self.r.register(OLD)
        self.assertFalse(any(m.startswith('Create') for m,b in self.calls))

    def test_room_refuses_unregistered_members_without_server_create(self):
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.write(self.root/'agents'/NEW/'group.json',{'version':1,'memberIds':[OTHER]})
        with self.assertRaises(RuntimeError):self.r.register(OLD)
        self.assertFalse(any(m=='CreateGrokBotRoom' for m,b in self.calls))

    def test_native_box_room_is_explicit_and_keeps_platform_kind_debt_visible(self):
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.write(self.root/'agents'/NEW/'group.json',{'version':1,'memberIds':[OTHER]})
        self.roster.append(row(OTHER,'box','789'))
        self.r.register(OLD,native_box_room=True)
        self.assertTrue(any(m=='CreateGrokBotAgent' for m,b in self.calls))
        self.assertFalse(any(m=='CreateGrokBotRoom' for m,b in self.calls))
        original=self.r.m.gateway
        self.r.m.gateway=lambda m,b: [{'id':NEW,'harness':'box','isGroup':True,'memberIds':[OTHER]}] if m=='listAgents' else original(m,b)
        result=self.r.verify()[0]
        self.assertEqual(result['status'],'verified-box');self.assertTrue(result['nativeGroupVerified'])
        self.assertFalse(result['platformRoomKindVerified'])

    def test_native_room_requires_box_members_before_creating_any_row(self):
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.write(self.root/'agents'/NEW/'group.json',{'version':1,'memberIds':[OTHER]})
        with self.assertRaises(RuntimeError):self.r.register(OLD,native_box_room=True)
        self.assertFalse(any(m.startswith('Create') for m,b in self.calls))

    def test_existing_empty_group_is_preserved_without_claiming_a_responder(self):
        self.write(self.source/'group.json',{'version':1,'memberIds':[]})
        self.r.stage(OLD);self.r.register(OLD)
        original=self.r.m.gateway
        self.r.m.gateway=lambda m,b: [{'id':NEW,'harness':'box','isGroup':False,'memberIds':[]}] if m=='listAgents' else original(m,b)
        result=self.r.verify()[0]
        self.assertEqual(result['status'],'verified-box');self.assertEqual(result['groupMemberCount'],0)
        self.assertFalse(result['replyReady']);self.assertFalse(result['platformRoomKindVerified'])
        self.assertFalse(result['nativeGroupVerified']);self.assertTrue(result['emptyGroupDefinitionPreserved'])
        self.r.activate(OLD)
        self.assertEqual(json.loads((self.root/'agents'/NEW/'group.json').read_text())['memberIds'],[])

    def test_refused_room_restaging_keeps_both_original_and_refused_copy(self):
        self.write(self.source/'group.json',{'version':1,'memberIds':[OTHER]})
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.roster.append(row(NEW,'temporal','456'))
        before=(self.source/'store.db').read_bytes();refused=(self.root/'agents'/NEW/'store.db').read_bytes()
        result=self.r.restage_room(OLD)
        self.assertNotEqual(result['newId'],NEW)
        self.assertEqual(result['registrationStrategy'],'native-box-room')
        self.assertEqual(result['refusedDestinations'],[{'agentId':NEW,'harness':'temporal'}])
        self.assertEqual((self.source/'store.db').read_bytes(),before)
        self.assertEqual((self.root/'agents'/NEW/'store.db').read_bytes(),refused)
        self.assertTrue(json.loads((self.root/'agents'/result['newId']/'settings.json').read_text())['hiddenFromSidebar'])

    def test_adopted_room_must_be_hidden_source_marked_and_have_explicit_members(self):
        self.write(self.source/'group.json',{'version':1,'memberIds':[OTHER]})
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.roster.append(row(NEW,'temporal','456'))
        candidate='00000000-0000-4000-8000-000000000004'
        self.r.m.copy_agent(OLD,candidate,{OLD:candidate},j)
        target=self.root/'agents'/candidate
        self.roster.append(row(candidate,'box','789'))
        with self.assertRaisesRegex(RuntimeError,'hidden staged'):self.r.restage_room(OLD,candidate)
        self.write(target/'settings.json',{'hiddenFromSidebar':True})
        (target/'.openbot-local-copy').write_text(OTHER+'\n')
        with self.assertRaisesRegex(RuntimeError,'different source'):self.r.restage_room(OLD,candidate)
        (target/'.openbot-local-copy').write_text(OLD+'\n')
        result=self.r.restage_room(OLD,candidate)
        self.assertEqual(result['newId'],candidate)
        self.assertEqual(result['phase'],'copied')

    def test_active_room_destination_cannot_be_rotated(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        with self.assertRaisesRegex(RuntimeError,'unactivated'):self.r.restage_room(OLD)
        self.assertEqual(self.r.read()['entries'][0]['newId'],NEW)

    def test_cutover_remaps_peer_roster_preserving_unrelated_entries_and_ancestry(self):
        peer=self.root/'openbot-recovery'/'roster.json'
        before=[{'id':OLD,'name':'Specialist','isGroup':False,'retiredId':None},
                {'id':OTHER,'name':'Existing local-only peer','isGroup':False,'custom':'preserve'}]
        self.write(peer,before);self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        after=json.loads(peer.read_text())
        self.assertEqual(after[0]['id'],NEW);self.assertEqual(after[0]['previousIds'],[OLD])
        self.assertIsNone(after[0]['retiredId']);self.assertEqual(after[1],before[1])
        self.assertEqual(json.loads((self.backup/'peer-roster-before.json').read_text()),before)
        unchanged=peer.read_bytes();self.r.activate(OLD)
        self.assertEqual(peer.read_bytes(),unchanged)

    def test_peer_room_uses_native_members_without_inventing_server_room_kind(self):
        self.write(self.source/'group.json',{'version':1,'memberIds':[OTHER]})
        self.r.stage(OLD);j=self.r.read();j['entries'][0]['kind']='GROK_BOT_AGENT_KIND_ROOM';self.r.save(j)
        self.roster.append(row(OTHER,'box','789'));self.r.register(OLD,native_box_room=True)
        original=self.r.m.gateway
        self.r.m.gateway=lambda m,b: [{'id':NEW,'harness':'box','isGroup':True,'memberIds':[OTHER]}] if m=='listAgents' else original(m,b)
        peer=self.root/'openbot-recovery'/'roster.json';self.write(peer,[{'id':OLD,'name':'Room','isGroup':True}])
        self.r.activate(OLD);record=json.loads(peer.read_text())[0]
        self.assertEqual(record['id'],NEW);self.assertTrue(record['isGroup'])
        self.assertEqual(record['memberIds'],[OTHER]);self.assertEqual(self.roster[-1]['kind'],KIND)

    def test_peer_roster_refuses_symlinks_and_duplicate_destinations(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        peer=self.root/'openbot-recovery'/'roster.json'
        self.write(peer,[{'id':OLD},{'id':NEW}]);before=peer.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'duplicate identities'):self.r.refresh_peer_roster()
        self.assertEqual(peer.read_bytes(),before)
        peer.unlink();peer.symlink_to(self.source/'profile.json');original=(self.source/'profile.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError,'owned regular'):self.r.refresh_peer_roster()
        self.assertEqual((self.source/'profile.json').read_bytes(),original)

    def test_staged_copy_cannot_change_peer_roster_and_hidden_active_target_is_refused(self):
        peer=self.root/'openbot-recovery'/'roster.json';self.write(peer,[{'id':OLD}])
        before=peer.read_bytes();self.r.stage(OLD);self.r.register(OLD)
        self.assertEqual(self.r.refresh_peer_roster()['changed'],0);self.assertEqual(peer.read_bytes(),before)
        self.r.activate(OLD);self.write(peer,[{'id':OLD}])
        self.write(self.root/'agents'/NEW/'settings.json',{'hiddenFromSidebar':True})
        before=peer.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'visibility cutover'):self.r.refresh_peer_roster()
        self.assertEqual(peer.read_bytes(),before)

    def test_separately_recovered_peer_requires_matching_role_and_retained_history(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        journal=self.r.read();journal['entries']=[];self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json';self.write(peer,[{'id':OLD}]);before=peer.read_bytes()
        target=self.root/'agents'/NEW/'profile.json';profile=json.loads(target.read_text())
        self.write(target,{**profile,'description':'Different specialist'})
        with self.assertRaisesRegex(RuntimeError,'different role'):self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(peer.read_bytes(),before);self.write(target,profile)
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute('DELETE FROM transcript_entries');c.commit()
        with self.assertRaisesRegex(RuntimeError,'retain source history'):self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(peer.read_bytes(),before)

    def test_existing_copy_cloud_merge_must_match_actual_published_destination_order(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        journal=self.r.read();journal['entries']=[];self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json';self.write(peer,[{'id':OLD}]);before=peer.read_bytes()
        with contextlib.closing(sqlite3.connect(self.source/'store.db')) as c:
            c.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('message-2',json.dumps({'id':'message-2'})));c.commit()
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute('UPDATE transcript_entries SET seq=3 WHERE seq=1')
            c.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('message-2',json.dumps({'id':'message-2'})));c.commit()
        with self.assertRaisesRegex(RuntimeError,'published history'):self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(peer.read_bytes(),before)
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as db:
            actual=[json.loads(row[0]) for row in db.execute('SELECT entry FROM transcript_entries ORDER BY seq')]
        self.server_transcripts[NEW]=[{'seq':str(seq),'body':base64.b64encode(json.dumps(entry).encode()).decode()}
                                     for seq,entry in enumerate(actual,1)]
        result=self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(json.loads(peer.read_text())[0]['id'],NEW)
        proof=result['existingCopyProofs'][0]
        self.assertTrue(proof['sourceIdsRetained']);self.assertTrue(proof['destinationPublicationVerified'])
        self.assertTrue(proof['sourcePayloadsRetained']);self.assertTrue(proof['destinationPayloadsPublished'])
        self.assertFalse(proof['sourceOrderPreserved'])

    def test_existing_peer_retained_ids_do_not_cover_changed_source_payload(self):
        self.r.stage(OLD); self.r.register(OLD); self.r.activate(OLD)
        journal=self.r.read(); journal['entries']=[]; self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json'; self.write(peer,[{'id':OLD}]); before=peer.read_bytes()
        with contextlib.closing(sqlite3.connect(self.source/'store.db')) as db:
            original=json.loads(db.execute('SELECT entry FROM transcript_entries').fetchone()[0])
            db.execute('UPDATE transcript_entries SET entry=?', (json.dumps({**original,
                'content':'Retain this quoted identity: '+OLD,'toolResult':{'agentId':OLD}}),)); db.commit()
        with self.assertRaisesRegex(RuntimeError,'source history payload'):
            self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(peer.read_bytes(),before)

    def test_existing_peer_published_ids_do_not_cover_changed_tool_payload(self):
        self.r.stage(OLD); self.r.register(OLD); self.r.activate(OLD)
        journal=self.r.read(); journal['entries']=[]; self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json'; self.write(peer,[{'id':OLD}]); before=peer.read_bytes()
        changed={'id':'message-1','timestampMs':1,'agentId':NEW,
                 'toolResult':{'status':'changed'}}
        self.server_transcripts[NEW]=[{'seq':'1','body':base64.b64encode(json.dumps(changed).encode()).decode()}]
        with self.assertRaisesRegex(RuntimeError,'published history payload'):
            self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(peer.read_bytes(),before)

    def test_existing_peer_preserves_literal_quoted_ids_with_owner_only_normalization(self):
        original={'id':'message-1','timestampMs':1,'agentId':OLD,
                  'content':'Quoted identity: '+OLD,'toolResult':{'agentId':OLD}}
        with contextlib.closing(sqlite3.connect(self.source/'store.db')) as db:
            db.execute('UPDATE transcript_entries SET entry=?',(json.dumps(original),)); db.commit()
        self.r.stage(OLD)
        target=self.root/'agents'/NEW/'store.db'
        with contextlib.closing(sqlite3.connect(target)) as db:
            copied=json.loads(db.execute('SELECT entry FROM transcript_entries').fetchone()[0])
        self.assertEqual(copied,{**original,'agentId':NEW})
        self.server_transcripts[NEW]=[{'seq':'1','body':base64.b64encode(json.dumps(copied).encode()).decode()}]
        self.r.register(OLD); self.r.activate(OLD)
        journal=self.r.read(); journal['entries']=[]; self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json'; self.write(peer,[{'id':OLD}])
        proof=self.r.refresh_peer_roster({OLD:NEW})['existingCopyProofs'][0]
        self.assertTrue(proof['sourcePayloadsRetained'])
        self.assertTrue(proof['destinationPayloadsPublished'])
        self.assertEqual(json.loads(peer.read_text())[0]['id'],NEW)

    def test_existing_peer_refuses_mixed_publication_generations_before_roster_write(self):
        self.r.stage(OLD); self.r.register(OLD); self.r.activate(OLD)
        journal=self.r.read(); journal['entries']=[]; self.r.save(journal)
        peer=self.root/'openbot-recovery'/'roster.json'; self.write(peer,[{'id':OLD}]); before=peer.read_bytes()
        extra={'id':'message-2','content':'Later message'}
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as db:
            first=json.loads(db.execute('SELECT entry FROM transcript_entries').fetchone()[0])
            db.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('message-2',json.dumps(extra))); db.commit()
        original_rpc=self.r.m.rpc; requests=[]
        def changing_generation(method,body):
            if method!='ListGrokBotTranscriptEntries':return original_rpc(method,body)
            requests.append(dict(body)); sequence=2 if len(requests)==1 else 1
            payload=extra if sequence==2 else first
            return {'generation':len(requests),'entries':[{'seq':str(sequence),
                    'body':base64.b64encode(json.dumps(payload).encode()).decode()}]}
        self.r.m.rpc=changing_generation
        with self.assertRaisesRegex(RuntimeError,'generation changed'):
            self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(requests[1]['generation'],1)
        self.assertEqual(peer.read_bytes(),before)

    def test_publication_requests_idle_supervision_without_claiming_delivery(self):
        self.r.stage(OLD);self.r.register(OLD)
        self.write(self.root/'computer-recovery-state.json',{'pendingHostReload':False,'other':'preserve'})
        host=self.root/'host.cjs';host.write_text('native')
        with mock.patch.object(self.r.reload,'HOST',host), mock.patch.object(self.r.reload,'host_pids',return_value=[os.getpid()]):
            result=self.r.publish()
        state=json.loads((self.root/'computer-recovery-state.json').read_text())
        self.assertFalse(state['pendingHostReload']);self.assertEqual(state['other'],'preserve')
        marker=json.loads((self.root/self.r.reload.NAME).read_text())
        self.assertEqual(marker['requestId'],self.r.read()['publicationReloadRequestId'])
        self.assertEqual(marker['requestId'],result['publicationReloadRequestId'])
        self.assertEqual(marker['phase'],'pending')
        self.assertFalse(result['publicationVerified']);self.assertFalse(result['nativeAppDeliveryVerified'])

    def test_publication_marker_survives_a_failed_journal_acknowledgement(self):
        self.r.stage(OLD); self.r.register(OLD)
        host = self.root / 'host.cjs'; host.write_text('native')
        before = M.JOURNAL.read_bytes()
        with mock.patch.object(self.r.reload, 'HOST', host), \
                mock.patch.object(self.r.reload, 'host_pids', return_value=[os.getpid()]), \
                mock.patch.object(self.r, 'save', side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError): self.r.publish()
        marker = json.loads((self.root / self.r.reload.NAME).read_text())
        self.assertEqual(marker['phase'], 'pending')
        self.assertEqual(M.JOURNAL.read_bytes(), before)
        self.assertFalse((self.root / 'computer-recovery-state.json').exists())

    def test_official_mode_refuses_remint_writes(self):
        (self.root/'openbot-mode').write_text('official')
        for command in [lambda:self.r.initialize(),lambda:self.r.stage(OLD),lambda:self.r.register(OLD),
                        lambda:self.r.publish(),lambda:self.r.activate(OLD)]:
            with self.assertRaisesRegex(RuntimeError,'Custom'):command()
        self.assertEqual(self.calls,[])

    def test_missing_copy_and_overlapping_generations_are_refused(self):
        with self.assertRaisesRegex(RuntimeError,'Copy must be verified'):self.r.register(OLD)
        j=self.r.read();j['entries'].append({**j['entries'][0],'oldId':NEW,'newId':OTHER});self.r.save(j)
        with self.assertRaisesRegex(RuntimeError,'one-to-one'):self.r.read()


if __name__=='__main__':unittest.main()
