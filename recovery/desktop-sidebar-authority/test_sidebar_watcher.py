import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


class WatcherLifecycleTests(unittest.TestCase):
    def exercise(self, *, active, behavior):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = root / 'bin'; binary.mkdir()
            state = root / 'active'; state.write_text('yes' if active else 'no')
            events = root / 'events'; events.touch()
            asar = root / 'resources' / 'app.asar'; asar.parent.mkdir(); asar.write_text('stock')
            sudo = binary / 'sudo'; sudo.write_text('#!/bin/sh\nshift\nexec "$@"\n'); sudo.chmod(0o700)
            systemctl = binary / 'systemctl'
            systemctl.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
p=pathlib.Path(os.environ['WATCH_TEST_ROOT']);args=sys.argv[1:]
if 'is-active' in args:raise SystemExit(0 if(p/'active').read_text()=='yes'else 3)
action=next(x for x in args if x in ['start','stop'])
with(p/'events').open('a')as f:print(action,file=f)
(p/'active').write_text('yes'if action=='start'else'no')
'''); systemctl.chmod(0o700)
            patcher = root / 'patcher.py'
            patcher.write_text('''import json,os,pathlib,sys
p=pathlib.Path(os.environ['GROKBOT_ASAR']);mode=os.environ['WATCH_TEST_BEHAVIOR']
if '--check'in sys.argv:
 if mode=='preflight-failure':raise SystemExit(2)
 print(json.dumps({'changed':mode!='unchanged','valid':True}))
else:
 if mode=='patch-failure':raise SystemExit(2)
 if mode!='concurrent-no-change':p.write_text('fixed')
''')
            env = {**os.environ, 'PATH': str(binary)+os.pathsep+os.environ['PATH'],
                   'WATCH_TEST_ROOT': str(root), 'WATCH_TEST_BEHAVIOR': behavior,
                   'GROKBOT_ASAR': str(asar), 'GROKBOT_SIDEBAR_PATCHER': str(patcher),
                   'GROKBOT_SIDEBAR_LOG': str(root/'watch.log'),
                   'GROKBOT_SIDEBAR_ALERT': str(root/'watch.alert'),
                   'XDG_RUNTIME_DIR': str(root)}
            subprocess.run(['bash', str(HERE/'ensure-sidebar-patches.sh')], env=env,
                           check=True, capture_output=True, text=True, timeout=10)
            return {'events': events.read_text().splitlines(), 'active': state.read_text(),
                    'archive': asar.read_text(), 'alert': (root/'watch.alert').exists()}

    def test_running_app_is_stopped_before_patch_then_restarted(self):
        result = self.exercise(active=True, behavior='changed')
        self.assertEqual(result, {'events':['stop','start'], 'active':'yes', 'archive':'fixed', 'alert':False})

    def test_closed_app_remains_closed(self):
        result = self.exercise(active=False, behavior='changed')
        self.assertEqual(result['events'], [])
        self.assertEqual(result['active'], 'no')

    def test_unchanged_or_unsupported_updates_do_not_interrupt_app(self):
        for behavior in ['unchanged', 'preflight-failure']:
            with self.subTest(behavior=behavior):
                result = self.exercise(active=True, behavior=behavior)
                self.assertEqual(result['events'], [])
                self.assertEqual(result['archive'], 'stock')
                self.assertEqual(result['alert'], behavior=='preflight-failure')

    def test_failed_patch_restores_running_state_and_reports_failure(self):
        result = self.exercise(active=True, behavior='patch-failure')
        self.assertEqual(result['events'], ['stop','start'])
        self.assertTrue(result['alert'])
        self.assertEqual(result['archive'], 'stock')

    def test_concurrent_no_change_still_restores_running_state(self):
        result = self.exercise(active=True, behavior='concurrent-no-change')
        self.assertEqual(result['events'], ['stop','start'])
        self.assertEqual(result['active'], 'yes')


if __name__ == '__main__': unittest.main()
