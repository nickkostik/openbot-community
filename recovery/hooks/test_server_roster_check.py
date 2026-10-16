import importlib.util
import json
import pathlib
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('roster',pathlib.Path(__file__).with_name('server-roster-check.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
A='00000000-0000-4000-8000-000000000001'
B='00000000-0000-4000-8000-000000000002'
def row(aid=A,harness='box'):
    return {'agentId':aid,'legacyAgentId':aid,'id':'123','harness':harness,'viewerIsOwner':True}
def write(path,data):path.write_bytes(data)


class ServerRosterTests(unittest.TestCase):
    def journal(self,sd,aid=A,sid='123'):
        (sd/'openbot-box-remint.json').write_text(json.dumps({'entries':[{'phase':'active','newId':aid,'serverId':sid}]}))

    def test_forced_local_box_labels_cannot_hide_server_temporal(self):
        report=m.compare({A:'123'},[row(harness='temporal')])
        self.assertEqual(report['state'],'drift');self.assertEqual(report['counts']['temporal'],1)
        self.assertEqual(report['driftAgentIds'],[A]);self.assertFalse(report['nativeSendVerified'])

    def test_box_registration_is_not_a_send_proof(self):
        report=m.compare({A:'123'},[row()])
        self.assertEqual(report['state'],'bound-box');self.assertFalse(report['nativeSendVerified'])

    def test_missing_duplicate_unowned_and_mismatched_bindings_are_distinct(self):
        cases=[([], 'missing'),([row(),row()], 'invalidBindings'),
               ([{**row(),'viewerIsOwner':False}], 'invalidBindings'),
               ([{**row(),'legacyAgentId':B}], 'invalidBindings'),
               ([{**row(),'id':'999'}], 'invalidBindings'),
               ([row(harness='unknown')], 'invalidBindings')]
        for rows,kind in cases:
            report=m.compare({A:'123'},rows)
            self.assertEqual(report['counts'][kind],1);self.assertEqual(report['state'],'drift')

    def test_historical_hidden_rows_are_not_part_of_active_authority(self):
        expected=m.authority({'entries':[{'phase':'active','newId':A,'serverId':'123'}, {'phase':'copied','newId':B}]})
        self.assertEqual(expected,{A:'123'})
        self.assertEqual(m.compare(expected,[row(),row(B,'temporal')])['state'],'bound-box')

    def test_invalid_active_authority_is_refused(self):
        for entry in [{'newId':'wrong','serverId':'123'},{'newId':A,'serverId':123}]:
            with self.assertRaises((RuntimeError,ValueError)):
                m.authority({'entries':[{'phase':'active',**entry}]})

    def test_partial_cutover_keeps_the_remaining_temporal_source_held(self):
        journal={'entries':[{'phase':'active','newId':A,'serverId':'123'},
                            {'phase':'registered-box','oldId':B,'sourceServerId':'456'}]}
        expected=m.authority(journal)
        self.assertEqual(expected,{A:'123',B:'456'})
        report=m.compare(expected,[row(),{**row(B,'temporal'),'id':'456'}])
        self.assertEqual(report['state'],'drift');self.assertEqual(report['driftAgentIds'],[B])

    def test_cache_expires_and_identity_changes_force_immediate_server_read(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);self.journal(sd);calls=[]
            def fetch():calls.append(True);return [row(),row(B)]
            first=m.check(sd,write,1000,fetch)
            cached=m.check(sd,write,1001,fetch);self.assertTrue(cached['cached']);self.assertEqual(len(calls),1)
            m.check(sd,write,121001,fetch);self.assertEqual(len(calls),2)
            self.journal(sd,B);m.check(sd,write,121002,fetch);self.assertEqual(len(calls),3)
            self.assertFalse(first['nativeSendVerified'])

    def test_drift_alert_stays_on_failed_fetch_and_clears_after_verified_box(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);self.journal(sd)
            m.check(sd,write,0,lambda:[row(harness='temporal')])
            alert=sd/'openbot-server-roster.alert.json';before=alert.read_bytes()
            def fail():raise RuntimeError('network')
            with self.assertRaises(RuntimeError):m.check(sd,write,120000,fail)
            self.assertEqual(alert.read_bytes(),before)
            m.check(sd,write,120000,lambda:[row()]);self.assertFalse(alert.exists())

    def test_authority_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as root:
            sd=pathlib.Path(root);self.journal(sd)
            path=sd/'openbot-box-remint.json';path.rename(sd/'other');path.symlink_to(sd/'other')
            with self.assertRaises(RuntimeError):m.check(sd,write,0,lambda:[])


if __name__=='__main__':unittest.main()
