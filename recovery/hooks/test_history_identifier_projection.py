"""Unpublished identifier repairs preserve exact messages, SDK and cloud prefix."""
import base64
import contextlib
import json
import pathlib
import shutil
import sqlite3
import unittest
import test_history_merge_receipt as fixture
H,OLD,NEW=fixture.H,fixture.OLD,fixture.NEW


class IdentifierProjectionTests(unittest.TestCase):
    setUp=fixture.ReceiptTests.setUp
    write=fixture.ReceiptTests.write
    rpc=fixture.ReceiptTests.rpc
    gateway=fixture.ReceiptTests.gateway
    prepare_receipt=fixture.ReceiptTests.prepare_receipt
    def prepare_projection(self):
        original=self.prepare_receipt()
        legacy='agent-outbound:'+('a'*64)+'~openbot-branch-'+('b'*64)
        source=pathlib.Path(self.receipt['sources'][0]['path'])
        baseline=pathlib.Path(self.receipt['baseline']['store']['path'])
        active=self.root/'agents'/NEW/'store.db'
        for db,owner,alias in ((source,OLD,'message-1'),(baseline,NEW,legacy),(active,NEW,legacy)):
            with contextlib.closing(sqlite3.connect(db))as c:
                prior=json.loads(c.execute('SELECT entry FROM transcript_entries').fetchone()[0])
                c.execute('DELETE FROM transcript_entries')
                records=[{'id':'published','kind':'message','role':'user','agentId':owner,'content':'first','timestampMs':1},{**prior,'id':alias}]
                c.executemany('INSERT INTO transcript_entries VALUES(?,?,?)',[(i+1,v['id'],json.dumps(v))for i,v in enumerate(records)]);c.commit()
        self.receipt['sources'][0]['sha256']=H.digest(source.read_bytes())
        self.receipt['baseline']['store']['sha256']=H.digest(baseline.read_bytes())
        self.receipt['sourceOrders'][0]['entryIds']=['published','message-1']
        self.receipt['projections']=[{'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':'published','targetEntryId':'published'},{'sourceId':OLD,'sourceKind':'sqlite','sourceEntryId':'message-1','targetEntryId':legacy}]
        self.write(original,self.receipt);self.r.accept_history_merge(NEW,str(original))
        parent={'path':str(original),'sha256':H.digest(original.read_bytes())}
        folder=original.parent/'identifiers';folder.mkdir()
        revised=folder/'store.db';shutil.copyfile(baseline,revised)
        new_id=self.r.merger.compact_branch_identifier(legacy,{'published',legacy})
        for db in(revised,active):
            with contextlib.closing(sqlite3.connect(db))as c:
                value=json.loads(c.execute('SELECT entry FROM transcript_entries WHERE seq=2').fetchone()[0]);value['id']=new_id
                c.execute('UPDATE transcript_entries SET id=?,entry=?WHERE seq=2',(new_id,json.dumps(value)));c.commit()
        prefix=folder/'published.json'
        with contextlib.closing(sqlite3.connect(baseline))as c:body=c.execute('SELECT entry FROM transcript_entries WHERE seq=1').fetchone()[0]
        self.write(prefix,{'schema':'openbot-platform-transcript-prefix/v1','agentId':NEW,'generation':1,'capturedAtMs':0,'entries':[{'seq':'1','entryId':'published','body':base64.b64encode(body.encode()).decode()}]})
        native=folder/'native-sdk.json';self.write(native,{'schema':'openbot-identifier-sdk-reference-proof/v1','workerSha256':H.IDENTIFIER_WORKER_SHA,'rootSha256':None,'rootAbsent':True,'rootRoundtrip':False,'checkedIds':[legacy],'structuralReferences':[],'graphUnchanged':True,'graphSha256':H.digest(b'[]'),'graphBlobs':0,'sourceUpdated':False,'liveWrites':0,'providerCalls':0,'networkAttempts':0,'toolAttempts':0})
        ref=lambda p:{'path':str(p),'sha256':H.digest(p.read_bytes())}
        proof=folder/'proof.json';self.write(proof,{'schema':H.IDENTIFIER_PROOF,'previousReceipt':parent,'publishedPrefix':ref(prefix),'changes':[{'seq':2,'oldId':legacy,'newId':new_id}],'nativeSdkProof':ref(native)})
        revision=json.loads(json.dumps(self.receipt));revision.update(schema=H.IDENTIFIER_SCHEMA,previousReceipt=parent,identifierProof=ref(proof));revision['baseline']['store']=ref(revised);revision['projections'][1]['targetEntryId']=new_id
        receipt=folder/'receipt.json';self.write(receipt,revision)
        return original,receipt,revision,proof,native,prefix,legacy,new_id

    def save_proof(self,receipt,revision,proof):
        revision['identifierProof']['sha256']=H.digest(proof.read_bytes());self.write(receipt,revision)

    def test_only_unpublished_alias_changes_and_previous_receipt_remains_valid_evidence(self):
        original,receipt,revision,*_=self.prepare_projection();before=original.read_bytes()
        result=self.r.accept_history_merge(NEW,str(receipt))
        self.assertEqual(result['baselineEntries'],2);self.assertEqual(original.read_bytes(),before)
        self.r.local_identity(self.r.read()['entries'][0])
        original.write_bytes(before+b' ')
        with self.assertRaisesRegex(RuntimeError,'Immutable'):
            self.r.local_identity(self.r.read()['entries'][0])

    def test_cannot_reproject_an_already_published_alias(self):
        original,receipt,revision,proof,native,prefix,*_=self.prepare_projection()
        with contextlib.closing(sqlite3.connect(self.receipt['baseline']['store']['path']))as db:row=db.execute('SELECT id,entry FROM transcript_entries WHERE seq=2').fetchone()
        value=json.loads(prefix.read_text());value['entries'].append({'seq':'2','entryId':row[0],'body':base64.b64encode(row[1].encode()).decode()});self.write(prefix,value)
        evidence=json.loads(proof.read_text());evidence['publishedPrefix']['sha256']=H.digest(prefix.read_bytes());self.write(proof,evidence);self.save_proof(receipt,revision,proof)
        with self.assertRaisesRegex(RuntimeError,'already published'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_changed_non_identifier_payload_is_refused(self):
        _,receipt,revision,*_=self.prepare_projection();store=pathlib.Path(revision['baseline']['store']['path'])
        with contextlib.closing(sqlite3.connect(store))as db:
            value=json.loads(db.execute('SELECT entry FROM transcript_entries WHERE seq=2').fetchone()[0]);value['content']='edited message';db.execute('UPDATE transcript_entries SET entry=?WHERE seq=2',(json.dumps(value),));db.commit()
        revision['baseline']['store']['sha256']=H.digest(store.read_bytes());self.write(receipt,revision)
        with self.assertRaisesRegex(RuntimeError,'altered a message'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_unproved_sdk_structural_reference_is_refused(self):
        _,receipt,revision,proof,native,*_=self.prepare_projection();value=json.loads(native.read_text());value['structuralReferences']=[{'path':'user.messageId'}];self.write(native,value)
        evidence=json.loads(proof.read_text());evidence['nativeSdkProof']['sha256']=H.digest(native.read_bytes());self.write(proof,evidence);self.save_proof(receipt,revision,proof)
        with self.assertRaisesRegex(RuntimeError,'native proof'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_cannot_replace_sdk_proof_or_source_variants(self):
        _,receipt,revision,*_=self.prepare_projection();revision['sources'][0]['generation']=2;self.write(receipt,revision)
        with self.assertRaisesRegex(RuntimeError,'preserved evidence'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_non_deterministic_or_oversized_replacement_is_refused(self):
        _,receipt,revision,proof,*_=self.prepare_projection();value=json.loads(proof.read_text());value['changes'][0]['newId']='unreviewed-id';self.write(proof,value);self.save_proof(receipt,revision,proof)
        with self.assertRaisesRegex(RuntimeError,'synthetic|deterministic'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_reprojection_does_not_authorize_a_message_append(self):
        _,receipt,revision,*_=self.prepare_projection();store=pathlib.Path(revision['baseline']['store']['path'])
        with contextlib.closing(sqlite3.connect(store))as db:
            db.execute('INSERT INTO transcript_entries VALUES(3,?,?)',('extra',json.dumps({'id':'extra','kind':'message','role':'user','content':'not in original'})));db.commit()
        revision['baseline']['store']['sha256']=H.digest(store.read_bytes());self.write(receipt,revision)
        with self.assertRaisesRegex(RuntimeError,'add or remove'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_native_proof_requires_version_pin_and_no_execution(self):
        _,receipt,revision,proof,native,*_=self.prepare_projection();value=json.loads(native.read_text());value['providerCalls']=1;self.write(native,value)
        evidence=json.loads(proof.read_text());evidence['nativeSdkProof']['sha256']=H.digest(native.read_bytes());self.write(proof,evidence);self.save_proof(receipt,revision,proof)
        with self.assertRaisesRegex(RuntimeError,'native proof'):
            self.r.accept_history_merge(NEW,str(receipt))

    def test_cloud_prefix_raw_byte_mismatch_is_refused(self):
        _,receipt,revision,proof,native,prefix,*_=self.prepare_projection();value=json.loads(prefix.read_text());value['entries'][0]['body']=base64.b64encode(b'{}').decode();self.write(prefix,value)
        evidence=json.loads(proof.read_text());evidence['publishedPrefix']['sha256']=H.digest(prefix.read_bytes());self.write(proof,evidence);self.save_proof(receipt,revision,proof)
        with self.assertRaisesRegex(RuntimeError,'raw baseline'):
            self.r.accept_history_merge(NEW,str(receipt))
