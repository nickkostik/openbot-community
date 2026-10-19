"""Immutable merge evidence must survive turns while refusing lost/edited history."""
import contextlib
import hashlib
import importlib.util
import json
import pathlib
import shutil
import sqlite3
import unittest
import test_remint_box_fleet as fixture
from test_remint_box_fleet import M, OLD, NEW, OTHER, KIND, row

SPEC=importlib.util.spec_from_file_location('history_receipt',pathlib.Path(__file__).with_name('history-merge-receipt.py'))
H=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(H)


class ReceiptTests(unittest.TestCase):
    setUp=fixture.RemintTests.setUp
    write=fixture.RemintTests.write
    rpc=fixture.RemintTests.rpc
    gateway=fixture.RemintTests.gateway
    def prepare_receipt(self):
        self.r.stage(OLD)
        for db in (self.source/'store.db',self.root/'agents'/NEW/'store.db'):
            with contextlib.closing(sqlite3.connect(db)) as c:
                for seq,raw in c.execute('SELECT seq,entry FROM transcript_entries').fetchall():
                    c.execute('UPDATE transcript_entries SET entry=? WHERE seq=?',(json.dumps({**json.loads(raw),'kind':'message','role':'user'}),seq))
                c.commit()
        journal=self.r.read();entry=journal['entries'][0]
        entry['copyProof']['historyIdConflicts']=2;self.r.save(journal)
        root=self.backup/'reviewed';root.mkdir()
        source=root/'source.db';baseline=root/'baseline.db';blobs=root/'blobs.db'
        shutil.copyfile(self.source/'store.db',source)
        shutil.copyfile(self.root/'agents'/NEW/'store.db',baseline)
        with contextlib.closing(sqlite3.connect(blobs)) as c:
            c.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)');c.commit()
        source_blobs=root/'source-blobs.db';shutil.copyfile(blobs,source_blobs)
        report=root/'native.json';self.write(report,{'rootReadbackSha256':None,'referencedBlobs':[]})
        ref=lambda p:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        sdk=root/'sdk.json';self.write(sdk,{'schema':'openbot-sdk-merge-proof/v1','engineSha256':'a'*64,
            'rootSlot':'root','rootSha256':None,'blobs':{},'sourceRoots':{OLD:None},'nativeReport':ref(report)})
        receipt=root/'receipt.json'
        self.receipt={'schema':H.SCHEMA,'oldId':OLD,'newId':NEW,'selectionSourceId':OLD,'backup':str(self.backup),
            'baseline':{'store':ref(baseline),'blobs':ref(blobs)},
            'sources':[{'agentId':OLD,'kind':'sqlite',**ref(source),'generation':None,'sdkRootSha256':None,'blobSnapshot':ref(source_blobs)}],
            'projections':[{'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':'message-1','targetEntryId':'message-1'}],
            'sourceOrders':[{'sourceId':OLD,'sourceKind':'sqlite','entryIds':['message-1']}],
            'orderResolutions':[],'sdkProof':ref(sdk)}
        self.write(receipt,self.receipt)
        return receipt

    def prepare_delta(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path))
        previous={'path':str(path),'sha256':H.digest(path.read_bytes())}
        delta=json.loads(json.dumps(self.receipt));delta.update(schema=H.DELTA_SCHEMA,previousReceipt=previous)
        folder=path.parent/'delta';folder.mkdir()
        for key in ('store','blobs'):
            source=pathlib.Path(delta['baseline'][key]['path']);target=folder/source.name
            shutil.copyfile(source,target);delta['baseline'][key]={'path':str(target),'sha256':H.digest(target.read_bytes())}
        event={'id':'new-tail','kind':'message','role':'user','content':'a newly arrived cloud message'}
        source=folder/'platform.json';self.write(source,{'generation':1,'entries':[{'seq':'1','body':__import__('base64').b64encode(json.dumps(event).encode()).decode()}]})
        delta['sources'].append({'agentId':OLD,'kind':'platform','path':str(source),'sha256':H.digest(source.read_bytes()),'generation':1,'sdkRootSha256':None,'blobSnapshot':None})
        # A real prior platform snapshot (empty) must already be pinned. Rebuild
        # the original receipt before acceptance in this isolated fixture.
        original=json.loads(path.read_text());empty=path.parent/'empty-platform.json';self.write(empty,{'generation':1,'entries':[]})
        original['sources'].append({**delta['sources'][-1],'path':str(empty),'sha256':H.digest(empty.read_bytes())})
        original['sourceOrders'].append({'sourceId':OLD,'sourceKind':'platform','entryIds':[]})
        self.write(path,original);journal=self.r.read();journal['entries'][0].pop('historyMergeReceipt');self.r.save(journal)
        self.r.accept_history_merge(NEW,str(path));delta['previousReceipt']['sha256']=H.digest(path.read_bytes())
        delta['sourceOrders'].append({'sourceId':OLD,'sourceKind':'platform','entryIds':[event['id']]})
        delta['projections'].append({'sourceId':OLD,'sourceKind':'platform','sourceEntryId':event['id'],'targetEntryId':event['id']})
        for db in (pathlib.Path(delta['baseline']['store']['path']),self.root/'agents'/NEW/'store.db'):
            with contextlib.closing(sqlite3.connect(db)) as c:
                c.execute('INSERT INTO transcript_entries VALUES(2,?,?)',(event['id'],json.dumps(event)));c.commit()
        delta['baseline']['store']['sha256']=H.digest(pathlib.Path(delta['baseline']['store']['path']).read_bytes())
        output=folder/'receipt.json';self.write(output,delta);return path,output,delta

    def test_incremental_receipt_appends_without_rewriting_prior_evidence(self):
        previous,path,delta=self.prepare_delta();before=previous.read_bytes()
        self.assertEqual(self.r.accept_history_merge(NEW,str(path))['baselineEntries'],2)
        self.assertEqual(previous.read_bytes(),before)
        self.r.local_identity(self.r.read()['entries'][0])
        previous.write_bytes(before+b' ')
        with self.assertRaisesRegex(RuntimeError,'Immutable'):
            self.r.local_identity(self.r.read()['entries'][0])

    def test_incremental_receipt_cannot_skip_pinned_parent(self):
        _,path,delta=self.prepare_delta();delta['previousReceipt']['sha256']='0'*64;self.write(path,delta)
        with self.assertRaisesRegex(RuntimeError,'different immutable'):
            self.r.accept_history_merge(NEW,str(path))

    def test_incremental_receipt_cannot_change_prefix_payload_or_order(self):
        _,path,delta=self.prepare_delta();store=pathlib.Path(delta['baseline']['store']['path'])
        with contextlib.closing(sqlite3.connect(store)) as c:
            c.execute("UPDATE transcript_entries SET entry=? WHERE seq=1",(json.dumps({'id':'message-1','kind':'message','role':'user','content':'edited'}),));c.commit()
        delta['baseline']['store']['sha256']=H.digest(store.read_bytes());self.write(path,delta)
        with self.assertRaisesRegex(RuntimeError,'strictly append'):
            self.r.accept_history_merge(NEW,str(path))

    def test_incremental_receipt_cannot_read_an_escaped_new_baseline(self):
        _,path,delta=self.prepare_delta();delta['baseline']['store']['path']=str(self.source/'store.db');self.write(path,delta)
        with self.assertRaisesRegex(RuntimeError,'escapes'):
            self.r.accept_history_merge(NEW,str(path))

    def test_incremental_receipt_cannot_drop_previous_projection(self):
        _,path,delta=self.prepare_delta();delta['projections']=delta['projections'][1:];self.write(path,delta)
        with self.assertRaisesRegex(RuntimeError,'earlier projections'):
            self.r.accept_history_merge(NEW,str(path))

    def test_incremental_receipt_cannot_reset_source_generation(self):
        _,path,delta=self.prepare_delta();delta['sources'][-1]['generation']=2;self.write(path,delta)
        with self.assertRaisesRegex(RuntimeError,'generation'):
            self.r.accept_history_merge(NEW,str(path))

    def test_incremental_sdk_must_keep_previous_root_and_every_blob(self):
        _,path,delta=self.prepare_delta();previous=json.loads(pathlib.Path(delta['previousReceipt']['path']).read_text())
        old_sdk=pathlib.Path(previous['sdkProof']['path']);old=json.loads(old_sdk.read_text());old.update(rootSlot='root',rootSha256='1'*64,blobs={'root':'1'*64,'older':'2'*64});self.write(old_sdk,old)
        previous['sdkProof']['sha256']=H.digest(old_sdk.read_bytes())
        new_sdk=path.parent/'sdk.json';new={**old,'rootSha256':'3'*64,'blobs':{'root':'3'*64,'older':'2'*64}}
        self.write(new_sdk,new);delta['sdkProof']={'path':str(new_sdk),'sha256':H.digest(new_sdk.read_bytes())}
        with self.assertRaisesRegex(RuntimeError,'previous exact root'):
            H.validate_extension(self.backup,delta,previous)
        new['blobs']['archived-root']='1'*64;self.write(new_sdk,new);delta['sdkProof']['sha256']=H.digest(new_sdk.read_bytes())
        H.validate_extension(self.backup,delta,previous)
        del new['blobs']['older'];self.write(new_sdk,new);delta['sdkProof']['sha256']=H.digest(new_sdk.read_bytes())
        with self.assertRaisesRegex(RuntimeError,'archived blob'):
            H.validate_extension(self.backup,delta,previous)

    def test_incremental_receipt_chain_is_bounded(self):
        _,path,delta=self.prepare_delta()
        with self.assertRaisesRegex(RuntimeError,'chain exceeds bound'):
            H.validate(self.r,self.r.read(),self.r.read()['entries'][0],delta['previousReceipt'],H.MAX_RECEIPT_DEPTH+1)

    def test_registered_incremental_receipt_requires_hidden_paused_box(self):
        _,path,delta=self.prepare_delta();journal=self.r.read();journal['entries'][0]['phase']='registered-box';self.r.save(journal)
        directory=self.root/'agents'/NEW
        self.write(directory/'profile.json',{'harness':'box','serverId':'456'})
        journal=self.r.read();journal['entries'][0]['serverId']='456';self.r.save(journal)
        self.roster.append(row(NEW,'box','456'))
        self.write(directory/'settings.json',{'hiddenFromSidebar':False})
        with self.assertRaisesRegex(RuntimeError,'hidden paused BOX'):
            self.r.accept_history_merge(NEW,str(path))
        self.write(directory/'settings.json',{'hiddenFromSidebar':True})
        routine=directory/'automations'/'one'/'automation.json';self.write(routine,{'enabled':True})
        with self.assertRaisesRegex(RuntimeError,'hidden paused BOX'):
            self.r.accept_history_merge(NEW,str(path))
        self.write(routine,{'enabled':False})
        self.assertEqual(self.r.accept_history_merge(NEW,str(path))['baselineEntries'],2)

    def test_registered_incremental_rejects_changed_server_box_binding(self):
        _,path,delta=self.prepare_delta();journal=self.r.read();journal['entries'][0].update(phase='registered-box',serverId='456');self.r.save(journal)
        directory=self.root/'agents'/NEW;self.write(directory/'profile.json',{'harness':'box','serverId':'456'});self.write(directory/'settings.json',{'hiddenFromSidebar':True})
        self.roster.append(row(NEW,'box','789'))
        with self.assertRaisesRegex(RuntimeError,'binding changed'):
            self.r.accept_history_merge(NEW,str(path))

    def test_registered_merge_cannot_replace_with_an_unlinked_v1_receipt(self):
        path=self.prepare_receipt();journal=self.r.read();journal['entries'][0]['phase']='registered-box';self.r.save(journal)
        with self.assertRaisesRegex(RuntimeError,'hidden paused BOX'):
            self.r.accept_history_merge(NEW,str(path))

    def test_source_freshness_rejects_sqlite_tail_and_same_count_head_edit(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path));journal=self.r.read();entry=journal['entries'][0]
        self.assertTrue(H.source_freshness(self.r,journal,entry)['sourceHeadsUnchanged'])
        with contextlib.closing(sqlite3.connect(self.source/'store.db'))as db:
            db.execute("UPDATE transcript_entries SET entry=?",(json.dumps({'id':'message-1','kind':'message','role':'user','text':'new version'}),));db.commit()
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            H.source_freshness(self.r,journal,entry)

    def test_source_freshness_rejects_cloud_generation_and_later_head(self):
        _,path,delta=self.prepare_delta();self.r.accept_history_merge(NEW,str(path));journal=self.r.read();entry=journal['entries'][0]
        source=pathlib.Path(delta['sources'][-1]['path']);capture=json.loads(source.read_text());self.server_transcripts[OLD]=capture['entries']
        self.assertTrue(H.source_freshness(self.r,journal,entry)['sourceHeadsUnchanged'])
        self.server_transcripts[OLD]=[{'seq':'2','body':__import__('base64').b64encode(json.dumps({'id':'later','kind':'message','role':'user','content':'later'}).encode()).decode()}]
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            H.source_freshness(self.r,journal,entry)
        original=self.r.m.rpc
        self.r.m.rpc=lambda method,body:{'generation':2,'entries':capture['entries']}if method=='ListGrokBotTranscriptEntries'else original(method,body)
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            H.source_freshness(self.r,journal,entry)

    def test_source_freshness_rejects_new_sdk_root_without_ui_change(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path));journal=self.r.read();entry=journal['entries'][0]
        with contextlib.closing(sqlite3.connect(self.source/'store.db'))as db:
            db.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':OLD,'latestRootBlobId':'root'}).encode().hex(),));db.commit()
        with contextlib.closing(sqlite3.connect(self.source/'conversation-blobs.db'))as db:
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)');db.execute('INSERT INTO blobs VALUES(?,?)',('root',b'later native context'));db.commit()
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            H.source_freshness(self.r,journal,entry)

    def test_activation_freshness_refuses_before_hiding_or_pausing_sources(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path));self.r.register(NEW)
        self.r.verify=lambda aid=None:[{'newId':NEW,'status':'verified-box'}]
        with contextlib.closing(sqlite3.connect(self.source/'store.db'))as db:
            db.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('later',json.dumps({'id':'later','kind':'message','role':'user','content':'new message'})));db.commit()
        before=self.r.read();self.calls=[];self.gateway_calls=[]
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            self.r.activate(NEW)
        self.assertEqual(self.r.read(),before)
        self.assertFalse(any(method=='SetGrokBotAgentClientState'for method,_ in self.calls))
        self.assertTrue(json.loads((self.source/'automations/routine/automation.json').read_text())['enabled'])

    def test_source_freshness_checks_sdk_graph_retained_in_store_database(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path));journal=self.r.read();entry=journal['entries'][0]
        with contextlib.closing(sqlite3.connect(self.source/'store.db'))as db:
            db.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':OLD,'latestRootBlobId':'root'}).encode().hex(),))
            db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)');db.execute('INSERT INTO blobs VALUES(?,?)',('root',b'later embedded SDK context'));db.commit()
        with self.assertRaisesRegex(RuntimeError,'Source history changed'):
            H.source_freshness(self.r,journal,entry)

    def test_receipt_keeps_original_conflict_proof_and_allows_new_turns(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path))
        self.assertEqual(self.r.read()['entries'][0]['copyProof']['historyIdConflicts'],2)
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('new-message',json.dumps({'id':'new-message','text':'new reply'})));c.commit()
        self.r.local_identity(self.r.read()['entries'][0])

    def test_equal_count_changed_payload_cannot_pass(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path))
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute("UPDATE transcript_entries SET entry=?",(json.dumps({'id':'message-1','text':'silently altered'}),));c.commit()
        with self.assertRaisesRegex(RuntimeError,'lost or changed'):
            self.r.local_identity(self.r.read()['entries'][0])

    def test_changed_baseline_or_receipt_cannot_replace_pin(self):
        path=self.prepare_receipt();self.r.accept_history_merge(NEW,str(path))
        original=path.read_bytes();path.write_bytes(original+b' ')
        with self.assertRaisesRegex(RuntimeError,'Immutable'):
            self.r.local_identity(self.r.read()['entries'][0])
        with self.assertRaisesRegex(RuntimeError,'different immutable'):
            self.r.accept_history_merge(NEW,str(path))

    def test_fabricated_source_projection_and_unknown_schema_fail(self):
        path=self.prepare_receipt()
        self.receipt['projections'][0]['targetEntryId']='absent';self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'Source payload'):
            self.r.accept_history_merge(NEW,str(path))
        self.receipt['skipConflicts']=True;self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'schema'):
            self.r.accept_history_merge(NEW,str(path))

    def test_symlink_receipt_and_escaped_baseline_fail(self):
        path=self.prepare_receipt();link=path.with_name('link.json');link.symlink_to(path)
        with self.assertRaisesRegex(RuntimeError,'regular'):
            self.r.accept_history_merge(NEW,str(link))
        self.receipt['baseline']['store']['path']=str(self.source/'store.db');self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'escapes'):
            self.r.accept_history_merge(NEW,str(path))

    def test_order_flip_without_review_cannot_pass(self):
        path=self.prepare_receipt()
        baseline=pathlib.Path(self.receipt['baseline']['store']['path'])
        source=pathlib.Path(self.receipt['sources'][0]['path'])
        for db,aid in ((baseline,NEW),(source,OLD)):
            with contextlib.closing(sqlite3.connect(db)) as c:
                c.execute('INSERT INTO transcript_entries VALUES(2,?,?)',('message-2',json.dumps({'id':'message-2','agentId':aid,'timestampMs':2,'kind':'message','role':'user'})));c.commit()
        self.receipt['baseline']['store']['sha256']=hashlib.sha256(baseline.read_bytes()).hexdigest()
        self.receipt['sources'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        self.receipt['projections'].append({'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':'message-2','targetEntryId':'message-2'})
        self.receipt['sourceOrders'][0]['entryIds'].append('message-2');self.write(path,self.receipt)
        with contextlib.closing(sqlite3.connect(self.root/'agents'/NEW/'store.db')) as c:
            c.execute('DELETE FROM transcript_entries');
            c.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',[(1,'message-2',json.dumps({'id':'message-2','agentId':NEW,'timestampMs':2})),(2,'message-1',json.dumps({'id':'message-1','agentId':NEW,'timestampMs':1}))]);c.commit()
        with self.assertRaisesRegex(RuntimeError,'lost or changed'):
            self.r.accept_history_merge(NEW,str(path))

    def test_return_initializer_copies_terminal_and_preserves_stock_binding(self):
        self.r.stage(OLD);self.r.register(OLD);self.r.activate(OLD)
        # User selected the original stock peer; platform subsequently migrated L.
        self.write(self.source/'settings.json',{'hiddenFromSidebar':False})
        copied=self.root/'agents'/NEW
        p=json.loads((copied/'profile.json').read_text());self.write(copied/'profile.json',{**p,'harness':'temporal'})
        self.roster[-1]['harness']='temporal'
        journal=self.r.initialize(True,True);entry=journal['entries'][0]
        self.assertEqual((entry['oldId'],entry['selectionSourceId'],entry['selectionSourceServerId']),(NEW,OLD,'123'))
        self.assertEqual(M.identity_mapping(journal)[OLD],entry['newId'])
        with self.assertRaisesRegex(RuntimeError,'reviewed immutable'):
            self.r.stage(NEW)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'copied')

    def test_partial_lineage_selection_does_not_retire_other_generations(self):
        journal={'previousGenerations':[{'entries':[{'oldId':OLD,'newId':NEW},{'oldId':OTHER,'newId':'00000000-0000-4000-8000-000000000004'}]}],
                 'entries':[{'oldId':NEW,'newId':'00000000-0000-4000-8000-000000000005'}]}
        target=journal['entries'][0]['newId']
        self.assertEqual(M.lineage_mapping(journal,{target}),{OLD:target,NEW:target})
        preferences={'sidebarSections':{'sections':[{'id':'coding','agentIds':[OLD,NEW,OTHER]}]},'pinnedAgents':{'agentIds':[OLD]}}
        paired=M.transfer_preferences(preferences,M.lineage_mapping(journal,{target}))
        self.assertEqual(paired['sidebarSections']['sections'][0]['agentIds'],[OLD,NEW,target,OTHER])

    def test_reviewed_cross_turn_arrival_requires_exact_payloads_and_small_skew(self):
        path=self.prepare_receipt()
        send={'kind':'send-message','id':'t112s748','agentId':OLD,'timestampMs':1506,'text':'previous turn reply'}
        user={'kind':'message','role':'user','id':'t113u','agentId':OLD,'timestampMs':1000,'text':'next turn request'}
        source=pathlib.Path(self.receipt['sources'][0]['path'])
        baseline=pathlib.Path(self.receipt['baseline']['store']['path'])
        active=self.root/'agents'/NEW/'store.db'
        for db,records in ((source,[send,user]),(baseline,[{**user,'agentId':NEW},{**send,'agentId':NEW}]),(active,[{**user,'agentId':NEW},{**send,'agentId':NEW}])):
            with contextlib.closing(sqlite3.connect(db)) as c:
                c.execute('DELETE FROM transcript_entries')
                c.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',[(i+1,v['id'],json.dumps(v)) for i,v in enumerate(records)]);c.commit()
        self.receipt['sources'][0]['sha256']=H.digest(source.read_bytes())
        self.receipt['baseline']['store']['sha256']=H.digest(baseline.read_bytes())
        self.receipt['sourceOrders'][0]['entryIds']=[send['id'],user['id']]
        self.receipt['projections']=[{'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':v['id'],'targetEntryId':v['id']} for v in (send,user)]
        self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'Source order'):
            self.r.accept_history_merge(NEW,str(path))
        resolution={'sourceId':OLD,'sourceKind':'sqlite','beforeId':send['id'],'afterId':user['id'],
            'beforePayloadSha256':H.digest(H.payload(self.r,send,{OLD:NEW})),
            'afterPayloadSha256':H.digest(H.payload(self.r,user,{OLD:NEW})),
            'reason':'cross-turn-adjacent-arrival'}
        self.receipt['orderResolutions']=[resolution];self.write(path,self.receipt)
        self.assertEqual(self.r.accept_history_merge(NEW,str(path))['baselineEntries'],2)
        # An edited resolution never silently authorizes a different history.
        resolution['afterPayloadSha256']='0'*64;self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'Immutable'):
            self.r.local_identity(self.r.read()['entries'][0])

    def test_sdk_proof_cannot_claim_a_missing_source_root(self):
        path=self.prepare_receipt();sdk=pathlib.Path(self.receipt['sdkProof']['path'])
        proof=json.loads(sdk.read_text());proof['sourceRoots'][OLD]='b'*64;self.write(sdk,proof)
        self.receipt['sources'][0]['sdkRootSha256']='b'*64
        self.receipt['sdkProof']['sha256']=H.digest(sdk.read_bytes());self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'Source SDK root'):
            self.r.accept_history_merge(NEW,str(path))

    def test_sdk_baseline_binds_metadata_root_and_every_blob(self):
        path=self.prepare_receipt();store=pathlib.Path(self.receipt['baseline']['store']['path'])
        blobs=pathlib.Path(self.receipt['baseline']['blobs']['path'])
        root=b'complete native root bytes';source_root=b'archived original root bytes'
        with contextlib.closing(sqlite3.connect(blobs)) as c:
            c.executemany('INSERT INTO blobs VALUES(?,?)',[('slot',root),('archived',source_root)]);c.commit()
        with contextlib.closing(sqlite3.connect(store)) as c:
            c.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':NEW,'latestRootBlobId':'slot'}).encode().hex(),));c.commit()
        sdk=pathlib.Path(self.receipt['sdkProof']['path']);proof=json.loads(sdk.read_text())
        proof.update(rootSlot='slot',rootSha256=H.digest(root),blobs={'slot':H.digest(root),'archived':H.digest(source_root)},sourceRoots={OLD:H.digest(source_root)})
        self.write(sdk,proof)
        source_db=pathlib.Path(self.receipt['sources'][0]['path'])
        with contextlib.closing(sqlite3.connect(source_db)) as c:
            c.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':OLD,'latestRootBlobId':'slot'}).encode().hex(),));c.commit()
        source_blobs=pathlib.Path(self.receipt['sources'][0]['blobSnapshot']['path'])
        with contextlib.closing(sqlite3.connect(source_blobs)) as c:
            c.execute('INSERT INTO blobs VALUES(?,?)',('slot',source_root));c.commit()
        self.receipt['sources'][0]['sha256']=H.digest(source_db.read_bytes())
        self.receipt['sources'][0]['blobSnapshot']['sha256']=H.digest(source_blobs.read_bytes())
        self.receipt['sources'][0]['sdkRootSha256']=H.digest(source_root)
        self.receipt['baseline']['store']['sha256']=H.digest(store.read_bytes())
        self.receipt['baseline']['blobs']['sha256']=H.digest(blobs.read_bytes())
        self.receipt['sdkProof']['sha256']=H.digest(sdk.read_bytes());self.write(path,self.receipt)
        self.r.accept_history_merge(NEW,str(path))
        # A different root pointer cannot be blessed even when both files hash.
        with contextlib.closing(sqlite3.connect(store)) as c:
            c.execute("UPDATE kv SET value=? WHERE key='metadata'",(json.dumps({'agentId':NEW,'latestRootBlobId':'other'}).encode().hex(),));c.commit()
        self.receipt['baseline']['store']['sha256']=H.digest(store.read_bytes());self.write(path,self.receipt)
        entry=self.r.read()['entries'][0]
        with self.assertRaisesRegex(RuntimeError,'different SDK root'):
            H.validate(self.r,self.r.read(),entry,{'path':str(path),'sha256':H.digest(path.read_bytes())})

    def test_annotations_keep_exact_source_order_provenance_without_false_chat_cycle(self):
        path=self.prepare_receipt()
        source=pathlib.Path(self.receipt['sources'][0]['path']);baseline=pathlib.Path(self.receipt['baseline']['store']['path'])
        event={'id':'annotation','kind':'notice','timestampMs':0,'text':'existing notice'}
        for db,at_start in ((source,False),(baseline,True),(self.root/'agents'/NEW/'store.db',True)):
            with contextlib.closing(sqlite3.connect(db)) as c:
                original=json.loads(c.execute('SELECT entry FROM transcript_entries').fetchone()[0]);c.execute('DELETE FROM transcript_entries')
                records=[event,original] if at_start else [original,event]
                c.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',[(i+1,v['id'],json.dumps(v)) for i,v in enumerate(records)]);c.commit()
        self.receipt['sources'][0]['sha256']=H.digest(source.read_bytes())
        self.receipt['baseline']['store']['sha256']=H.digest(baseline.read_bytes())
        self.receipt['sourceOrders'][0]['entryIds'].append(event['id'])
        self.receipt['projections'].append({'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':event['id'],'targetEntryId':event['id']})
        self.write(path,self.receipt);self.r.accept_history_merge(NEW,str(path))
        # Unknown event types do not silently inherit annotation privileges.
        with contextlib.closing(sqlite3.connect(baseline)) as c:
            c.execute('UPDATE transcript_entries SET entry=? WHERE id=?',(json.dumps({**event,'kind':'new-approval'}),event['id']));c.commit()
        self.receipt['baseline']['store']['sha256']=H.digest(baseline.read_bytes());self.write(path,self.receipt)
        with self.assertRaisesRegex(RuntimeError,'Unclassified event kind'):
            H.validate(self.r,self.r.read(),self.r.read()['entries'][0],{'path':str(path),'sha256':H.digest(path.read_bytes())})
