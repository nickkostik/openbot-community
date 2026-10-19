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
