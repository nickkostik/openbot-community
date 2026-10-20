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

    def test_prepared_tail_evidence_recovers_before_exact_apply_without_replanning(self):
        repair, intent, target, receipt = self.setup_apply()
        retention = T.module('evidence-retention'); journal = self.r.read()
        retention.retain(journal['backup'], intent)
        job = repair.recovery.progress['jobId']
        T.write(self.backup / ('bounded-tail-job-' + job + '.json'),
                {'phase': 'prepared', 'jobId': job, 'intents': [intent]}, True)
        _, metadata, _ = retention.locations(self.backup, intent)
        required = [self.backup / item['path'] for item in json.loads(T.owned(metadata))['files']]
        captures = {path: path.read_bytes() for path in required if path.suffix == '.db'}
        native_before = {path: path.read_bytes() for path in target.iterdir() if path.is_file()}
        for path in captures: path.unlink()
        self.assertEqual(retention.recover_pending(journal, self.root, self.fd, job)['restored'], len(captures))
        self.assertEqual({path: path.read_bytes() for path in captures}, captures)
        self.assertEqual({path: path.read_bytes() for path in native_before}, native_before)
        self.assertEqual(repair.apply(journal, intent)['added'], 1)
        H.validate(self.r, self.r.read(), self.r.read()['entries'][0])
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'], T.reference(receipt))

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

    def test_completed_job_with_unchanged_heads_is_an_immutable_noop(self):
        repair,intent,target,_=self.setup_apply();value=json.loads(T.owned(intent['path']));value.update(addedMessages=1,textBytes=5);T.write(pathlib.Path(intent['path']),value);intent=T.reference(pathlib.Path(intent['path']))
        repair.prepare=lambda *args:intent
        result=repair.run();self.assertEqual(result['importedMessages'],1)
        path,_=T.publication_state(repair.recovery);before=path.read_bytes()
        self.assertTrue(repair.run()['noop']);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(T.rows(target/'store.db')),2)

    def test_interrupted_preparation_does_not_rebuild_over_unknown_state(self):
        repair,intent,target,_=self.setup_apply();path=self.backup/('bounded-tail-job-'+repair.recovery.progress['jobId']+'.json')
        T.write(path,{'phase':'preparing','jobId':repair.recovery.progress['jobId']},True)
        with self.assertRaisesRegex(RuntimeError,'Interrupted'):repair.run()
        self.assertEqual(len(T.rows(target/'store.db')),1)

    def test_second_completed_round_appends_real_sql_prefix_and_pins_linked_receipt(self):
        repair,first,target,first_receipt=self.setup_apply()
        value=json.loads(T.owned(first['path']));value.update(addedMessages=1,textBytes=30)
        T.write(pathlib.Path(first['path']),value);first=T.reference(pathlib.Path(first['path']))
        repair.prepare=lambda *args:first;repair.run()
        ack={'requestId':str(uuid.uuid4()),'phase':'acknowledged'}
        T.record_publication(repair.recovery,{'publicationReloadRequestId':ack['requestId']})
        first_plan,_=T.publication_state(repair.recovery);plan_bytes=first_plan.read_bytes();parent_bytes=first_receipt.read_bytes()
        prefix=T.rows(target/'store.db');sdk_before=T.graph(target/'conversation-blobs.db')
        parent=T.reference(first_receipt);previous=json.loads(parent_bytes)
        folder=first_receipt.parent/'second-round';folder.mkdir()
        for filename in ('store.db','conversation-blobs.db'):
            T.snapshot(target/filename,folder/filename);T.snapshot(target/filename,folder/('before-'+filename))
        tail=item(2,'second literal tail');event=json.loads(base64.b64decode(tail['body']))
        with contextlib.closing(sqlite3.connect(folder/'store.db'))as sql:
            sql.execute('INSERT INTO transcript_entries VALUES(3,?,?)',(event['id'],json.dumps(event)));sql.commit()
        child=T.incremental_receipt(previous,parent,{'store':T.reference(folder/'store.db'),'blobs':T.reference(folder/'conversation-blobs.db')})
        captured=json.loads(T.owned(previous['sources'][-1]['path']));fresh={'generation':1,'entries':captured['entries']+[tail]}
        capture=folder/'cloud.json';T.write(capture,fresh,True)
        child['sources'][-1].update(T.reference(capture));child['sourceOrders'][-1]['entryIds'].append(event['id'])
        child['projections'].append({'sourceId':OLD,'sourceKind':'platform','sourceEntryId':event['id'],'targetEntryId':event['id']})
        receipt=folder/'receipt.json';T.write(receipt,child,True)
        journal=self.r.read();entry=journal['entries'][0]
        value={'newId':NEW,'oldReceipt':parent,'newReceipt':T.reference(receipt),'guards':repair.identity(journal,entry),
               'databaseInodes':{name:T.database_identity(target/name)for name in ('store.db','conversation-blobs.db')},
               'sourceHeads':child['sources'],'beforeStore':T.reference(folder/'before-store.db'),
               'beforeSDK':T.reference(folder/'before-conversation-blobs.db'),'plannedStore':child['baseline']['store'],
               'plannedSDK':child['baseline']['blobs'],'selectedRoute':repair.route(entry),'addedMessages':1,'textBytes':19}
        intent=folder/'intent.json';T.write(intent,value,True)
        repair.prepare=lambda journal,entry,accepted:T.reference(intent) if accepted==parent else self.fail('parent changed')
        original=self.r.m.rpc
        self.r.m.rpc=lambda method,body: {'generation':1,'entries':fresh['entries'][-body['limit']:][::-1]} if method=='ListGrokBotTranscriptEntries' else original(method,body)
        with mock.patch.object(self.r.reload,'read',return_value=ack):
            self.assertEqual(repair.run()['importedMessages'],1)
        self.assertEqual(T.rows(target/'store.db')[:len(prefix)],prefix)
        self.assertEqual(len(T.rows(target/'store.db')),3);self.assertEqual(T.graph(target/'conversation-blobs.db'),sdk_before)
        self.assertEqual(self.r.read()['entries'][0]['historyMergeReceipt'],T.reference(receipt))
        self.assertEqual(first_plan.read_bytes(),plan_bytes);self.assertEqual(first_receipt.read_bytes(),parent_bytes)
        H.validate(self.r,self.r.read(),self.r.read()['entries'][0])
        self.assertTrue(repair.run()['noop']);self.assertEqual(len(T.rows(target/'store.db')),3)

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





class TailRoundTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name);self.backup=self.root/'backup';self.backup.mkdir()
        self.job=str(uuid.uuid4());self.writes=[];self.prepares=[];self.ack={'requestId':str(uuid.uuid4()),'phase':'acknowledged'}
        def ref(name,value):
            path=self.backup/name;T.write(path,value,True);return T.reference(path)
        self.ref=ref;self.parent=ref('receipt-1.json',{'revision':1})
        self.journal={'backup':str(self.backup),'entries':[{'newId':NEW,'phase':'registered-box','historyMergeReceipt':self.parent}]}
        def referenced(root,reference):
            raw=T.owned(reference['path']);self.assertEqual(T.sha(raw),reference['sha256']);return raw
        self.r=types.SimpleNamespace(read=lambda:json.loads(json.dumps(self.journal)),
            entry=lambda journal,identifier:next(e for e in journal['entries']if e['newId']==identifier),
            reload=types.SimpleNamespace(read=lambda root:self.ack))
        self.recovery=types.SimpleNamespace(root=self.root,r=self.r,progress={'jobId':self.job},require=lambda:None)
        self.repair=object.__new__(T.TailRepair);self.repair.root=self.root;self.repair.r=self.r;self.repair.recovery=self.recovery
        self.repair.validator=types.SimpleNamespace(referenced=referenced);self.repair.require=lambda:None;self.repair.fault=lambda phase:None
        self.first=self.backup/('bounded-tail-job-'+self.job+'.json')
        first_intent=ref('intent-1.json',{'newId':NEW,'newReceipt':self.parent})
        T.write(self.first,{'phase':'completed','jobId':self.job,'intents':[first_intent],'reports':[{'added':1}],
                            'reloadRequestId':self.ack['requestId']},True)
        self.first_bytes=self.first.read_bytes();self.repair.new_tail_witnesses=lambda _:[]
        def apply(journal,reference):
            intent=json.loads(referenced(self.backup,reference));current=self.journal['entries'][0]['historyMergeReceipt']
            if current==intent['newReceipt']:return {'added':1,'resumed':True}
            self.assertEqual(current,intent['oldReceipt']);self.journal['entries'][0]['historyMergeReceipt']=intent['newReceipt'];self.writes.append(reference)
            self.repair.fault('accepted');return {'added':1,'resumed':False}
        self.repair.apply=apply

    def queue(self,number=2):
        parent=self.journal['entries'][0]['historyMergeReceipt'];child=self.ref('receipt-'+str(number)+'.json',{'revision':number})
        witness={'newId':NEW,'parentReceipt':parent,'sourceAgentId':OLD,'before':{'generation':1,'count':number-1},
                 'after':{'generation':1,'count':number},'addedMessages':1,'deltaSha256':str(number)*64}
        intent=self.ref('intent-'+str(number)+'.json',{'newId':NEW,'oldReceipt':parent,'newReceipt':child,'addedMessages':1,'textBytes':4})
        self.repair.new_tail_witnesses=lambda _:[witness] if self.journal['entries'][0]['historyMergeReceipt']==parent else []
        def prepare(journal,entry,accepted):
            self.assertEqual(accepted,parent);self.prepares.append(intent);return intent
        self.repair.prepare=prepare;self.repair.intent_witnesses=lambda *args:[witness]
        return intent,child

    def fault(self,point):
        self.repair.fault=lambda phase:(_ for _ in ()).throw(InterruptedError(point)) if phase==point else None

    def acknowledge(self):
        self.ack={'requestId':str(uuid.uuid4()),'phase':'acknowledged'}
        T.record_publication(self.recovery,{'publicationReloadRequestId':self.ack['requestId']})

    def test_second_actual_witness_keeps_same_job_and_completed_parent_bytes(self):
        intent,child=self.queue();result=self.repair.run()
        path,state=T.publication_state(self.recovery)
        self.assertEqual(result['importedMessages'],1);self.assertEqual(state['roundIndex'],2)
        self.assertEqual(state['previousRound'],T.reference(self.first));self.assertEqual(state['jobId'],self.job)
        self.assertEqual(self.journal['entries'][0]['historyMergeReceipt'],child);self.assertEqual(self.first.read_bytes(),self.first_bytes)
        self.acknowledge();self.assertFalse(T.waiting_for_reload(self.recovery))
        before=path.read_bytes();self.assertTrue(self.repair.run()['noop']);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.writes,[intent])

    def test_no_growth_never_creates_an_empty_round(self):
        self.assertTrue(self.repair.run()['noop']);self.assertEqual(list(self.backup.glob('*round-*')),[])
        self.assertEqual(self.first.read_bytes(),self.first_bytes)

    def test_pending_or_displaced_publication_ack_cannot_roll_over(self):
        self.queue()
        for ack in ({'requestId':self.ack['requestId'],'phase':'pending'}, {'requestId':str(uuid.uuid4()),'phase':'acknowledged'}, None):
            self.ack=ack
            with self.subTest(ack=ack),self.assertRaisesRegex(RuntimeError,'publication acknowledgement'):self.repair.run()
        self.assertEqual(self.writes,[]);self.assertEqual(self.first.read_bytes(),self.first_bytes)

    def test_crash_before_rollover_keeps_original_state(self):
        self.queue();self.fault('before-round-rollover')
        with self.assertRaises(InterruptedError):self.repair.run()
        self.assertEqual(list(self.backup.glob('*round-*')),[]);self.assertEqual(self.writes,[])
        self.repair.fault=lambda _:None;self.assertEqual(self.repair.run()['importedMessages'],1)

    def test_crash_after_rollover_resumes_same_round_without_new_job(self):
        self.queue();self.fault('round-rollover')
        with self.assertRaises(InterruptedError):self.repair.run()
        path,state=T.publication_state(self.recovery);before=state['previousRound'];self.assertTrue(T.pending(self.journal,self.job))
        self.repair.new_tail_witnesses=lambda _:self.fail('must resume witnessed round')
        self.repair.fault=lambda _:None;self.repair.run()
        self.assertEqual(T.publication_state(self.recovery)[0],path);self.assertEqual(T.publication_state(self.recovery)[1]['previousRound'],before)
        self.assertEqual(self.first.read_bytes(),self.first_bytes)

    def test_crash_after_prepared_cursor_does_not_prepare_again(self):
        self.queue();self.fault('round-entry-prepared')
        with self.assertRaises(InterruptedError):self.repair.run()
        self.repair.prepare=lambda *args:self.fail('must reuse durable intent');self.repair.fault=lambda _:None
        self.repair.run();self.assertEqual(len(self.prepares),1);self.assertEqual(len(self.writes),1)

    def test_crash_after_acceptance_never_double_applies(self):
        self.queue();self.fault('accepted')
        with self.assertRaises(InterruptedError):self.repair.run()
        self.repair.fault=lambda _:None;self.repair.run();self.assertEqual(len(self.writes),1)
        self.assertEqual(self.first.read_bytes(),self.first_bytes)

    def test_crash_after_completion_still_requires_this_rounds_reload(self):
        self.queue();self.fault('round-completed')
        with self.assertRaises(InterruptedError):self.repair.run()
        path,state=T.publication_state(self.recovery);self.assertEqual(state['phase'],'completed');self.assertNotIn('reloadRequestId',state)
        requested=str(uuid.uuid4());self.r.publish=lambda:{'publicationReloadRequestId':requested}
        self.assertTrue(T.waiting_for_reload(self.recovery));self.assertEqual(json.loads(path.read_text())['reloadRequestId'],requested)
        self.ack={'requestId':requested,'phase':'acknowledged'};self.assertFalse(T.waiting_for_reload(self.recovery));self.assertEqual(len(self.writes),1)

    def test_same_parent_replay_and_changed_completed_file_refuse(self):
        self.queue();self.repair.run();self.acknowledge()
        self.journal['entries'][0]['historyMergeReceipt']=self.parent
        with self.assertRaisesRegex(RuntimeError,'accepted parent changed'):self.repair.run()
        self.journal['entries'][0]['historyMergeReceipt']=json.loads(T.owned(self.writes[0]['path']))['newReceipt']
        self.first.write_bytes(self.first_bytes+b' ')
        with self.assertRaisesRegex(RuntimeError,'parent or publication'):self.repair.run()

    def test_prepared_cursor_and_intent_parent_tampering_refuse(self):
        intent,_=self.queue();self.fault('round-prepared')
        with self.assertRaises(InterruptedError):self.repair.run()
        self.repair.fault=lambda _:None;path,state=T.publication_state(self.recovery);saved=dict(state)
        state['preparedEntries']=[];T.write(path,state)
        with self.assertRaisesRegex(RuntimeError,'cursor'):self.repair.run()
        T.write(path,saved);value=json.loads(T.owned(intent['path']));value['oldReceipt']={'path':'wrong','sha256':'0'*64}
        T.write(pathlib.Path(intent['path']),value);saved['intents']=[T.reference(pathlib.Path(intent['path']))];T.write(path,saved)
        with self.assertRaisesRegex(RuntimeError,'witnessed parent'):self.repair.run()
        self.assertEqual(self.writes,[])

    def test_eight_total_rounds_are_bounded_and_unchanged_last_round_is_noop(self):
        for number in range(2,T.MAX_ROUNDS+1):
            self.queue(number);self.repair.run();self.acknowledge()
        self.assertEqual(len(T.tail_rounds(self.journal,self.job)),8)
        self.assertTrue(self.repair.run()['noop']);self.queue(9)
        with self.assertRaisesRegex(RuntimeError,'budget exhausted'):self.repair.run()
        self.assertEqual(len(self.writes),7);self.assertEqual(self.first.read_bytes(),self.first_bytes)


class IdentifierThenTailTests(unittest.TestCase):
    def test_child_drops_only_revision_specific_field_and_does_not_mutate_parent(self):
        parent = {'schema':H.IDENTIFIER_SCHEMA, 'identifierProof':{'path':'prior-proof','sha256':'a'*64},
                  'sources':[{'agentId':OLD}], 'previousReceipt':{'path':'older','sha256':'b'*64},
                  'unknownFutureSemanticField':'must remain so strict validator refuses it'}
        before = json.loads(json.dumps(parent)); reference={'path':'parent','sha256':'c'*64}
        child=T.incremental_receipt(parent,reference,{'store':{},'blobs':{}})
        self.assertNotIn('identifierProof',child);self.assertEqual(parent,before)
        self.assertEqual(child['previousReceipt'],reference);self.assertEqual(child['schema'],H.DELTA_SCHEMA)
        self.assertEqual(child['sources'],parent['sources'])
        self.assertEqual(child['unknownFutureSemanticField'],parent['unknownFutureSemanticField'])

    def test_real_identifier_receipt_then_incremental_is_canonically_validated(self):
        from test_history_identifier_projection import IdentifierProjectionTests
        fixture=IdentifierProjectionTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original,path,parent,proof,*_=fixture.prepare_projection()
        empty=original.parent/'platform-empty.json';fixture.write(empty,{'generation':1,'entries':[]})
        source={'agentId':OLD,'kind':'platform',**T.reference(empty),'generation':1,'sdkRootSha256':None,'blobSnapshot':None}
        # Establish platform authority in the immutable original before pinning it.
        original_value=json.loads(original.read_text());original_value['sources'].append(source)
        original_value['sourceOrders'].append({'sourceId':OLD,'sourceKind':'platform','entryIds':[]})
        fixture.write(original,original_value);journal=fixture.r.read();journal['entries'][0].pop('historyMergeReceipt');fixture.r.save(journal)
        active=fixture.root/'agents'/NEW/'store.db'
        projected_rows=T.rows(active)
        with contextlib.closing(sqlite3.connect(active))as sql:
            sql.execute('DELETE FROM transcript_entries');sql.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',T.rows(original_value['baseline']['store']['path']));sql.commit()
        fixture.r.accept_history_merge(NEW,str(original));parent['previousReceipt']=T.reference(original)
        with contextlib.closing(sqlite3.connect(active))as sql:
            sql.execute('DELETE FROM transcript_entries');sql.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',projected_rows);sql.commit()
        evidence=json.loads(proof.read_text());evidence['previousReceipt']=T.reference(original);fixture.write(proof,evidence)
        parent['identifierProof']=T.reference(proof);parent['sources'].append(source)
        parent['sourceOrders'].append({'sourceId':OLD,'sourceKind':'platform','entryIds':[]});fixture.write(path,parent)
        fixture.r.accept_history_merge(NEW,str(path));parent_bytes=path.read_bytes();proof_bytes=proof.read_bytes()
        folder=path.parent/'tail';folder.mkdir();store=folder/'store.db';T.snapshot(pathlib.Path(parent['baseline']['store']['path']),store)
        event=json.loads(base64.b64decode(item(3)['body']))
        for target in(store,fixture.root/'agents'/NEW/'store.db'):
            with contextlib.closing(sqlite3.connect(target))as sql:
                sql.execute('INSERT INTO transcript_entries VALUES(3,?,?)',(event['id'],json.dumps(event)));sql.commit()
        capture=folder/'platform.json';fixture.write(capture,{'generation':1,'entries':[item(3)]})
        child=T.incremental_receipt(parent,T.reference(path),{**parent['baseline'],'store':T.reference(store)})
        child['sources'][-1]={**source,**T.reference(capture)}
        child['sourceOrders'][-1]['entryIds']=[event['id']]
        child['projections'].append({'sourceId':OLD,'sourceKind':'platform','sourceEntryId':event['id'],'targetEntryId':event['id']})
        result=folder/'receipt.json';fixture.write(result,child)
        self.assertEqual(fixture.r.accept_history_merge(NEW,str(result))['baselineEntries'],3)
        self.assertEqual(path.read_bytes(),parent_bytes);self.assertEqual(proof.read_bytes(),proof_bytes)
        # An inherited revision-only field still fails; no validator loosening.
        child['identifierProof']=parent['identifierProof'];fixture.write(result,child)
        with self.assertRaisesRegex(RuntimeError,'Unknown history merge evidence schema'):
            H.validate(fixture.r,fixture.r.read(),fixture.r.read()['entries'][0],T.reference(result))

if __name__ == '__main__': unittest.main()
