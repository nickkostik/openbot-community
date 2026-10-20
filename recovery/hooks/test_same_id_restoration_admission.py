"""The restoration admission must not create agents or reset visible preferences."""
import copy
import json
import os
import pathlib
import types
import unittest
from unittest import mock
import test_remint_box_fleet as fixture
from test_remint_box_fleet import M, OLD, NEW


class AdmissionTests(unittest.TestCase):
    setUp=fixture.RemintTests.setUp
    write=fixture.RemintTests.write
    rpc=fixture.RemintTests.rpc
    gateway=fixture.RemintTests.gateway
    def ready(self):
        self.r.stage(OLD);self.r.register(NEW)
        journal=self.r.read();entry=journal['entries'][0]
        entry['phase']='active';self.r.save(journal)
        settings=self.root/'agents'/NEW/'settings.json'
        self.write(settings,{'hiddenFromSidebar':False,'notificationsEnabled':True,'custom':'keep'})
        path=self.backup/'restoration.json'
        self.write(path,{'schema':'openbot-history-merge-receipt/live-restoration-v1','previousReceipt':None})
        self.fd=os.open(self.root/'openbot-custom-dispatch.lock',os.O_RDWR|os.O_CREAT,0o600)
        self.addCleanup(os.close,self.fd)
        old_gateway=self.r.m.gateway
        self.r.m.gateway=lambda name,body: {'isBusy':False} if name=='getHostStatus' else old_gateway(name,body)
        original=M.helper; self.live_checks=[]
        validator=original('history-merge-receipt')
        def accept(r,j,e,p):
            e['historyMergeReceipt']={'path':p,'sha256':validator.digest(pathlib.Path(p).read_bytes())}
            return {'baselineEntries':1,'pruneAllowed':False}
        stub=types.SimpleNamespace(RESTORATION_SCHEMA=validator.RESTORATION_SCHEMA,
            checked=validator.checked,accept=accept)
        def helper(name):
            if name=='history-merge-receipt':return stub
            if name=='same-id-history-restoration':return types.SimpleNamespace(validate_live_graph=lambda *a:self.live_checks.append(a))
            return original(name)
        self.patch=mock.patch.object(M,'helper',side_effect=helper);self.patch.start();self.addCleanup(self.patch.stop)
        self.calls.clear();self.gateway_calls.clear()
        return path,settings

    def test_registered_restoration_keeps_visible_settings_and_actual_phase(self):
        path,settings=self.ready();before=settings.read_bytes()
        result=self.r.accept_history_merge(NEW,str(path),self.fd)
        self.assertEqual(result['baselineEntries'],1)
        self.assertEqual(settings.read_bytes(),before)
        self.assertEqual(self.r.read()['entries'][0]['phase'],'active')
        self.assertEqual(len(self.live_checks),1)
        self.assertFalse(any(name.startswith(('Create','Set','Update')) for name,_ in self.calls))

    def test_registered_restoration_requires_actual_inherited_dispatch_descriptor(self):
        path,_=self.ready();before=M.JOURNAL.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'dispatch writer lock'):
            self.r.accept_history_merge(NEW,str(path))
        self.assertEqual(M.JOURNAL.read_bytes(),before)

    def test_registered_restoration_refuses_native_busy_without_journal_changes(self):
        path,_=self.ready();before=M.JOURNAL.read_bytes()
        self.r.m.gateway=lambda name,body:{'isBusy':True}
        with self.assertRaisesRegex(RuntimeError,'idle native host'):
            self.r.accept_history_merge(NEW,str(path),self.fd)
        self.assertEqual(M.JOURNAL.read_bytes(),before)

    def test_registered_restoration_does_not_silently_pause_enabled_routine(self):
        path,_=self.ready();before=M.JOURNAL.read_bytes()
        automation=self.root/'agents'/NEW/'automations'/'routine'/'automation.json'
        value=json.loads(automation.read_bytes());value['enabled']=True;self.write(automation,value)
        with self.assertRaisesRegex(RuntimeError,'all target routines paused'):
            self.r.accept_history_merge(NEW,str(path),self.fd)
        self.assertEqual(M.JOURNAL.read_bytes(),before)
        self.assertTrue(json.loads(automation.read_bytes())['enabled'])

    def test_adopt_existing_copy_never_creates_or_changes_sidebar(self):
        path,settings=self.ready();before=settings.read_bytes()
        entry=copy.deepcopy(self.r.read()['entries'][0]);journal=self.r.read();journal['entries']=[];self.r.save(journal)
        template={key:entry[key] for key in ('oldId','newId','name','kind','sourceServerId','serverId')}
        template.update(selectionSourceId=OLD,selectionSourceServerId='123',requiresHistoryMerge=True,hiddenTemporalSdkAvailable=False)
        result=self.r.adopt_restored_existing(template,str(path),self.fd)
        self.assertTrue(result['registeredExisting'])
        self.assertEqual(settings.read_bytes(),before)
        self.assertEqual(self.r.read()['entries'][0]['serverId'],'456')
        self.assertFalse(any(name.startswith(('Create','Set','Update')) for name,_ in self.calls))
        self.assertFalse(result['publicationVerified'])


if __name__=='__main__':unittest.main()
