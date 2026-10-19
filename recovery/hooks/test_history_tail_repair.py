"""Bounded tail import refuses forks, preserves prefixes, and resumes crashes."""
import base64
import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import sqlite3
import types
import unittest
import uuid
from unittest import mock
from test_history_merge_receipt import ReceiptTests, H
from test_remint_box_fleet import OLD, NEW, row

SPEC = importlib.util.spec_from_file_location('tail_repair', pathlib.Path(__file__).with_name('history-tail-repair.py'))
T = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(T)


def item(sequence, text='literal text', identifier=None):
    event = {'id': identifier or 'event-' + str(sequence), 'kind': 'message', 'role': 'user', 'content': text, 'timestampMs': sequence, 'isStreaming':False}
    return {'seq': str(sequence), 'entryId': event['id'], 'body': base64.b64encode(T.encoded(event)).decode()}


class BoundedCloudTests(unittest.TestCase):
    def fetch(self, old, fresh, generation=1):
        calls = []
        def rpc(method, body):
            calls.append((method, body)); return {'generation': generation, 'entries': fresh[-body['limit']:][::-1]}
        result = T.cloud_tail(rpc, {'agentId': OLD, 'generation': 1}, {'generation': 1, 'entries': old})
        self.assertLessEqual(len(calls), 2)
        return result

    def test_exact_append_and_literal_payload_not_reformatted(self):
        original = item(1); tail = item(2, 'do not replay tool calls\n[secret-shaped literal]')
        result, added = self.fetch([original], [original, tail])
        self.assertEqual(added, [tail]); self.assertEqual(result['entries'], [original, tail])

    def test_generation_reset_is_not_an_append(self):
        with self.assertRaisesRegex(RuntimeError, 'generation'):
            self.fetch([item(1)], [item(1), item(2)], generation=2)

    def test_changed_boundary_is_a_fork(self):
        with self.assertRaisesRegex(RuntimeError, 'boundary'):
            self.fetch([item(1)], [item(1, 'edited'), item(2)])

    def test_missing_sequence_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'contiguous'):
            self.fetch([item(1)], [item(1), item(3)])

    def test_128_is_bounded_but_129_is_refused(self):
        old = [item(1)]
        self.assertEqual(len(self.fetch(old, [item(i) for i in range(1, 130)])[1]), 128)
        with self.assertRaisesRegex(RuntimeError, 'bound'):
            self.fetch(old, [item(i) for i in range(1, 131)])

    def test_literal_converter_rejects_tools_and_nontext_attachments(self):
        for event in ({'kind': 'tool-call'}, {'kind': 'message', 'role': 'user', 'content': []},
                      {'kind': 'send-message', 'message': {'type': 'image', 'content': 'a'}},
                      {'kind':'message','role':'user','content':'text','tool_calls':[]},
                      {'kind':'message','role':'assistant','content':'text','attachments':[]},
                      {'kind':'send-message','message':{'type':'text','content':'text','execute':True}}):
            with self.subTest(event=event), self.assertRaises(RuntimeError): T.literal(event)
        self.assertEqual(T.literal({'id':'done','kind': 'send-message','timestampMs':1, 'message': {'type': 'text', 'content': 'unchanged'}}), ('assistant', 'unchanged'))


class CompletedEnvelopeTests(unittest.TestCase):
    def test_only_completed_known_native_text_fields_are_supported(self):
        event=json.loads(base64.b64decode(item(1)['body']))
        self.assertEqual(T.literal(event),('user','literal text'))
        self.assertEqual(T.literal({**event,'isStreaming':False,'timestampMs':1.25,'requestId':'request','clientNonce':'nonce','batchId':'batch','sentWhileOfflineAtMs':0.5}),('user','literal text'))
        incomplete={k:v for k,v in event.items()if k!='isStreaming'}
        with self.assertRaises(RuntimeError):T.literal(incomplete)
        for extra in ({'isStreaming':True},{'isStreaming':None},{'status':'failed'},{'cancelled':True},
                      {'parts':[{'type':'image','url':'private'}]}, {'tool_calls':[]}, {'attachments':[]},
                      {'richText':{}},{'replyTo':'other'}, {'sentWhileOfflineAtMs':float('inf')},{'batchId':{}},
                      {'timestampMs':float('nan')},{'timestampMs':True},{'requestId':'line\nbreak'}, {'inlineImage':{'data':'private'}},
                      {'toolEnvelope':{'name':'send_message'}}, {'senderAgentId':OLD},
                      {'contentMeta':{'recipientAgentId':OLD}}, {'deliveryStatus':'cancelled'}):
            with self.subTest(extra=extra),self.assertRaises(RuntimeError):T.literal({**event,**extra})

    def test_cross_bot_destination_and_unknown_nested_send_fields_require_review(self):
        event={'id':'sent','kind':'send-message','timestampMs':1,'message':{'type':'text','content':'unchanged'}}
        self.assertEqual(T.literal(event),('assistant','unchanged'))
        for extra in ({'destination':{'type':'agent','agentId':OLD}}, {'recipientAgentId':OLD}, {'deliveryResult':{'status':'failed'}}):
            with self.subTest(extra=extra),self.assertRaises(RuntimeError):T.literal({**event,**extra})
            with self.subTest(nested=extra),self.assertRaises(RuntimeError):T.literal({**event,'message':{**event['message'],**extra}})


class ApplyTests(ReceiptTests):
    def setup_apply(self):
        previous, receipt_path, receipt = self.prepare_delta()
        journal = self.r.read(); entry = journal['entries'][0]; entry.update(phase='registered-box', serverId='456'); self.r.save(journal)
        target = self.root / 'agents' / NEW
        profile = json.loads((target / 'profile.json').read_text()); profile.update(harness='box', serverId='456'); self.write(target / 'profile.json', profile)
        self.write(target / 'settings.json', {'hiddenFromSidebar': True})
        for path in (target / 'automations').rglob('automation.json'):
            value=json.loads(path.read_text());value['enabled']=False;self.write(path,value)
        self.roster.append(row(NEW, 'box', '456'))
        with contextlib.closing(sqlite3.connect(target / 'store.db')) as sql:
            sql.execute('DELETE FROM transcript_entries WHERE seq=2');sql.commit()
        folder = receipt_path.parent
        before_store = folder / 'before-store.db'; T.snapshot(target / 'store.db', before_store)
        live_sdk = target / 'conversation-blobs.db'
        if not live_sdk.exists(): T.snapshot(pathlib.Path(receipt['baseline']['blobs']['path']), live_sdk)
        before_sdk = folder / 'before-sdk.db';T.snapshot(live_sdk,before_sdk)
        self.fd = os.open(self.root / 'openbot-custom-dispatch.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB);self.addCleanup(os.close,self.fd)
        recovery = types.SimpleNamespace(root=self.root,r=self.r,progress={'jobId':str(uuid.uuid4())},require=self.r.require_local_mode)
        repair = T.TailRepair(recovery,self.fd);repair.route=lambda _: {'modelId':'route','contextTokens':10000,'maxOutputTokens':1000}
        repair.local_sources=lambda *args:None
        original=self.r.m.rpc
        def rpc(method, body):
            if method=='ListGrokBotTranscriptEntries':return json.loads(pathlib.Path(receipt['sources'][-1]['path']).read_text())
            return original(method,body)
        self.r.m.rpc=rpc
        intent={'schemaVersion':1,'newId':NEW,'oldReceipt':T.reference(previous),'newReceipt':T.reference(receipt_path),
                'guards':repair.identity(journal,entry),'databaseInodes':{name:T.database_identity(target/name)for name in('store.db','conversation-blobs.db')},'serverId':'456','sourceHeads':receipt['sources'],
                'beforeStore':T.reference(before_store),'beforeSDK':T.reference(before_sdk),
                'plannedStore':receipt['baseline']['store'],'plannedSDK':receipt['baseline']['blobs'],'selectedRoute':repair.route(entry)}
        path=folder/'intent.json';T.write(path,intent,True)
        return repair,T.reference(path),target,receipt_path

    def test_sql_inode_prefix_and_every_setting_remain_intact(self):
        repair,intent,target,path=self.setup_apply();before_inode=(target/'store.db').stat().st_ino
        before_profile=(target/'profile.json').read_bytes();before_sdk=T.graph(target/'conversation-blobs.db')
        prefix=T.rows(target/'store.db');result=repair.apply(self.r.read(),intent)
        self.assertEqual(result['added'],1);self.assertEqual(T.rows(target/'store.db')[:len(prefix)],prefix)
        self.assertEqual((target/'store.db').stat().st_ino,before_inode)
        self.assertEqual((target/'profile.json').read_bytes(),before_profile);self.assertEqual(T.graph(target/'conversation-blobs.db'),before_sdk)
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'],T.reference(path))
        self.assertFalse(self.r.read()['entries'][0]['phase']=='active')

    def test_crash_after_ui_commit_resumes_exact_plan_without_double_append(self):
        repair,intent,target,path=self.setup_apply()
        def fault(phase):
            if phase=='ui-appended':raise InterruptedError('simulated crash')
        repair.fault=fault
        with self.assertRaises(InterruptedError):repair.apply(self.r.read(),intent)
        self.assertEqual(len(T.rows(target/'store.db')),2)
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'],json.loads(T.owned(intent['path']))['oldReceipt'])
        repair.fault=lambda _:None
        repair.apply(self.r.read(),intent);repair.apply(self.r.read(),intent)
        self.assertEqual(len(T.rows(target/'store.db')),2)
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'],T.reference(path))

    def test_crash_after_sdk_commit_can_pin_receipt_on_resume(self):
        repair,intent,target,path=self.setup_apply()
        repair.fault=lambda phase: (_ for _ in ()).throw(InterruptedError()) if phase=='sdk-appended' else None
        with self.assertRaises(InterruptedError):repair.apply(self.r.read(),intent)
        repair.fault=lambda _:None;repair.apply(self.r.read(),intent)
        H.validate(self.r,self.r.read(),self.r.read()['entries'][0]);self.assertEqual(len(T.rows(target/'store.db')),2)

    def test_live_cas_refuses_unplanned_row_without_erasing_it(self):
        repair,intent,target,_=self.setup_apply()
        with contextlib.closing(sqlite3.connect(target/'store.db')) as sql:
            sql.execute('INSERT INTO transcript_entries VALUES(3,?,?)',('external',json.dumps({'id':'external','kind':'message','role':'user','content':'do not erase'})));sql.commit()
        before=T.rows(target/'store.db')
        with self.assertRaisesRegex(RuntimeError,'UI CAS'):repair.apply(self.r.read(),intent)
        self.assertEqual(T.rows(target/'store.db'),before)

    def test_replaced_database_inode_refuses_even_when_rows_match(self):
        repair,intent,target,_=self.setup_apply();old=target/'store.db';replacement=target/'replacement.db';T.snapshot(old,replacement);os.replace(replacement,old)
        with self.assertRaisesRegex(RuntimeError,'inode changed'):repair.apply(self.r.read(),intent)
        self.assertEqual(len(T.rows(old)),1)

    def test_sdk_cas_refuses_concurrent_blob(self):
        repair,intent,target,_=self.setup_apply()
        with contextlib.closing(sqlite3.connect(target/'conversation-blobs.db'))as sql:sql.execute('INSERT INTO blobs VALUES(?,?)',('other',b'new'));sql.commit()
        with self.assertRaisesRegex(RuntimeError,'SDK CAS'):repair.apply(self.r.read(),intent)
        self.assertIn('other',T.graph(target/'conversation-blobs.db'))

    def test_state_changed_after_sdk_commit_is_not_pinned(self):
        repair,intent,target,_=self.setup_apply()
        def fault(phase):
            if phase=='sdk-appended':
                with contextlib.closing(sqlite3.connect(target/'store.db'))as sql:
                    sql.execute('UPDATE transcript_entries SET entry=?WHERE seq=2',(json.dumps({'id':'new-tail','kind':'message','role':'user','content':'changed'}),));sql.commit()
        repair.fault=fault
        with self.assertRaisesRegex(RuntimeError,'changed before receipt pin'):repair.apply(self.r.read(),intent)
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'],json.loads(T.owned(intent['path']))['oldReceipt'])

    def test_active_copy_is_never_modified(self):
        repair,intent,target,_=self.setup_apply();journal=self.r.read();journal['entries'][0]['phase']='active';self.r.save(journal)
        before=T.rows(target/'store.db')
        with self.assertRaisesRegex(RuntimeError,'active copy'):repair.apply(journal,intent)
        self.assertEqual(T.rows(target/'store.db'),before)

    def test_settings_or_binding_change_is_not_accepted(self):
        repair,intent,target,_=self.setup_apply();self.write(target/'settings.json',{'hiddenFromSidebar':False})
        with self.assertRaisesRegex(RuntimeError,'hidden BOX'):repair.apply(self.r.read(),intent)

    def test_inherited_writer_lock_required(self):
        repair,intent,target,_=self.setup_apply();repair.lock_fd=None
        with self.assertRaisesRegex(RuntimeError,'Inherited'):repair.apply(self.r.read(),intent)

    def test_new_cloud_head_after_prepare_requires_review(self):
        repair,intent,target,_=self.setup_apply();original=self.r.m.rpc
        self.r.m.rpc=lambda method,body:{'generation':1,'entries':[item(2)]} if method=='ListGrokBotTranscriptEntries' else original(method,body)
        before=T.rows(target/'store.db')
        with self.assertRaisesRegex(RuntimeError,'advanced again'):repair.apply(self.r.read(),intent)
        self.assertEqual(T.rows(target/'store.db'),before)

    def test_unaffected_active_cohort_is_not_a_tail_prepare_candidate(self):
        repair,intent,target,_=self.setup_apply();journal=self.r.read();journal['entries'][0]['phase']='active';self.r.save(journal)
        with mock.patch.object(repair,'prepare') as prepare, self.assertRaisesRegex(RuntimeError,'No bounded'):
            repair.run()
        prepare.assert_not_called();self.assertEqual(len(T.rows(target/'store.db')),1)

    def test_completed_job_cannot_start_a_second_automatic_attempt(self):
        repair,intent,target,_=self.setup_apply();value=json.loads(T.owned(intent['path']));value.update(addedMessages=1,textBytes=5);T.write(pathlib.Path(intent['path']),value);intent=T.reference(pathlib.Path(intent['path']))
        repair.prepare=lambda *args:intent
        result=repair.run();self.assertEqual(result['importedMessages'],1)
        with self.assertRaisesRegex(RuntimeError,'already completed'):repair.run()
        self.assertEqual(len(T.rows(target/'store.db')),2)

    def test_interrupted_preparation_does_not_rebuild_over_unknown_state(self):
        repair,intent,target,_=self.setup_apply();path=self.backup/('bounded-tail-job-'+repair.recovery.progress['jobId']+'.json')
        T.write(path,{'phase':'preparing','jobId':repair.recovery.progress['jobId']},True)
        with self.assertRaisesRegex(RuntimeError,'Interrupted'):repair.run()
        self.assertEqual(len(T.rows(target/'store.db')),1)

    def test_prepared_job_resumes_after_sql_crash_without_repreparing(self):
        repair,intent,target,_=self.setup_apply();path=self.backup/('bounded-tail-job-'+repair.recovery.progress['jobId']+'.json')
        T.write(path,{'phase':'prepared','jobId':repair.recovery.progress['jobId'],'intents':[intent]},True)
        repair.fault=lambda phase: (_ for _ in ()).throw(InterruptedError()) if phase=='ui-appended' else None
        with self.assertRaises(InterruptedError):repair.run()
        repair.fault=lambda _:None;repair.prepare=lambda *args: self.fail('must resume exact intent')
        self.assertEqual(repair.run()['importedMessages'],1);self.assertEqual(len(T.rows(target/'store.db')),2)

    def test_unreviewed_sdk_engine_cannot_invoke_codec_process(self):
        repair,_,_,_=self.setup_apply()
        with mock.patch.object(T.subprocess,'run') as run, self.assertRaisesRegex(RuntimeError,'unreviewed codec'):
            repair.run_codec(self.backup,{'engineSha256':'f'*64})
        run.assert_not_called()

    def test_tail_job_requires_exact_process_reload_ack_after_receipt_pin(self):
        repair,intent,target,_=self.setup_apply();path=self.backup/('bounded-tail-job-'+repair.recovery.progress['jobId']+'.json')
        T.write(path,{'phase':'completed','jobId':repair.recovery.progress['jobId'],'reports':[]},True)
        request_id=str(uuid.uuid4())
        with mock.patch.object(self.r,'publish',return_value={'publicationReloadRequestId':request_id}) as publish, mock.patch.object(self.r.reload,'read',return_value=None):
            self.assertTrue(T.waiting_for_reload(repair.recovery));publish.assert_called_once()
        self.assertEqual(json.loads(path.read_text())['reloadRequestId'],request_id)
        with mock.patch.object(self.r.reload,'read',return_value={'requestId':request_id,'phase':'pending'}), mock.patch.object(self.r,'publish') as publish:
            self.assertTrue(T.waiting_for_reload(repair.recovery));publish.assert_not_called()
        with mock.patch.object(self.r.reload,'read',return_value={'requestId':request_id,'phase':'acknowledged'}):
            self.assertFalse(T.waiting_for_reload(repair.recovery))
        displaced=str(uuid.uuid4())
        with mock.patch.object(self.r.reload,'read',return_value={'requestId':displaced,'phase':'acknowledged'}), mock.patch.object(self.r,'publish',return_value={'publicationReloadRequestId':displaced}) as publish:
            self.assertTrue(T.waiting_for_reload(repair.recovery));publish.assert_called_once()

    def test_symlinked_evidence_is_not_read(self):
        repair,intent,target,_=self.setup_apply();p=pathlib.Path(intent['path']);link=p.with_name('link.json');link.symlink_to(p)
        with self.assertRaisesRegex(RuntimeError,'symlink'):T.owned(link)


if __name__=='__main__':unittest.main()
