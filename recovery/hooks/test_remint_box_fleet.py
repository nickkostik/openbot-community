"""Complete-copy and cutover safety regressions, without live sends."""
import base64
import copy
import contextlib
import importlib.util
import json
import pathlib
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
        self.server_transcripts[NEW]=[{'seq':str(seq),'body':base64.b64encode(json.dumps({'id':aid}).encode()).decode()}
                                     for seq,aid in [(1,'message-2'),(2,'message-1')]]
        result=self.r.refresh_peer_roster({OLD:NEW})
        self.assertEqual(json.loads(peer.read_text())[0]['id'],NEW)
        proof=result['existingCopyProofs'][0]
        self.assertTrue(proof['sourceIdsRetained']);self.assertTrue(proof['destinationPublicationVerified'])
        self.assertFalse(proof['sourceOrderPreserved'])

    def test_publication_requests_idle_supervision_without_claiming_delivery(self):
        self.r.stage(OLD);self.r.register(OLD)
        self.write(self.root/'computer-recovery-state.json',{'pendingHostReload':False,'other':'preserve'})
        result=self.r.publish();state=json.loads((self.root/'computer-recovery-state.json').read_text())
        self.assertTrue(state['pendingHostReload']);self.assertEqual(state['other'],'preserve')
        self.assertFalse(result['publicationVerified']);self.assertFalse(result['nativeAppDeliveryVerified'])

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
