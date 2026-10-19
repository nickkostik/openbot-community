"""Publication requests survive telemetry races without sending a user turn."""
import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('publication_reload', pathlib.Path(__file__).with_name('publication-reload.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class PublicationReloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sd = pathlib.Path(temporary.name)
        self.host = self.sd / 'host.cjs'
        self.host.write_bytes(b'owned custom native host')
        os.utime(self.host, (time.time() - 10, time.time() - 10))
        (self.sd / 'openbot-mode').write_text('custom')
        (self.sd / 'openbot-local-only.enable').touch()
        self.current = {'pid': 123, 'startTicks': '456'}
        self.pids = lambda: [self.current['pid']] if self.current else []
        self.busy = False
        self.status = lambda: {'isBusy': self.busy}
        patch = mock.patch.object(m, 'identity', side_effect=lambda pid: dict(self.current) if self.current and pid == self.current['pid'] else None)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(m.os, 'kill')
        self.kill = patch.start(); self.addCleanup(patch.stop)

    def request(self):
        return m.request(self.sd, self.host, self.pids)

    def consume(self):
        return m.consume(self.sd, self.host, self.pids, self.status)

    def test_stale_telemetry_overwrite_and_watcher_restart_cannot_lose_request(self):
        stale = {'pendingHostReload': False}
        (self.sd / 'computer-recovery-state.json').write_text(json.dumps(stale))
        requested = self.request()
        (self.sd / 'computer-recovery-state.json').write_text(json.dumps(stale))
        self.assertEqual(self.consume()['phase'], 'signal-issued')
        self.kill.assert_called_once_with(123, m.signal.SIGTERM)
        # Restarting/reloading this helper has no in-memory acknowledgement state.
        other = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(other)
        with mock.patch.object(other, 'identity', side_effect=m.identity):
            self.assertEqual(other.consume(self.sd, self.host, self.pids, self.status)['phase'], 'waiting-native-replacement')
        self.current = {'pid': 124, 'startTicks': '777'}
        self.assertEqual(self.consume()['phase'], 'acknowledged')
        self.assertEqual(self.consume()['phase'], 'acknowledged')
        self.kill.assert_called_once()
        self.assertEqual(m.read(self.sd)['requestId'], requested['requestId'])
        self.assertEqual((self.sd / m.NAME).stat().st_mode & 0o777, 0o600)

    def test_crash_after_durable_intent_retries_only_after_grace_then_acknowledges(self):
        request = self.request()
        now = time.time()
        # The previous watcher fsynced intent and crashed before calling kill.
        request.update(phase='signal-issued', signalIssuedAtMs=int(now * 1000), signalAttempts=1)
        m.write(self.sd, request)
        restarted = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(restarted)
        with mock.patch.object(restarted, 'identity', side_effect=m.identity):
            self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'waiting-native-replacement')
            self.kill.assert_not_called()
            with mock.patch.object(restarted.time, 'time', return_value=now + 11):
                self.busy = True
                self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'waiting-idle')
                self.kill.assert_not_called()
                self.busy = False
                self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'signal-issued')
                self.assertEqual(m.read(self.sd)['signalAttempts'], 2)
                self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'waiting-native-replacement')
                self.kill.assert_called_once_with(123, m.signal.SIGTERM)
                self.current = {'pid': 124, 'startTicks': '777'}
                self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'acknowledged')
                self.assertEqual(restarted.consume(self.sd, self.host, self.pids, self.status)['phase'], 'acknowledged')
                self.kill.assert_called_once()
        self.assertEqual(m.read(self.sd)['requestId'], request['requestId'])

    def test_busy_missing_idle_evidence_and_official_preserve_request(self):
        requested = self.request()
        self.busy = True
        self.assertEqual(self.consume()['phase'], 'waiting-idle')
        self.status = lambda: {}
        self.assertEqual(self.consume()['phase'], 'waiting-idle')
        (self.sd / 'openbot-mode').write_text('official')
        self.assertEqual(self.consume()['phase'], 'waiting-custom')
        self.assertEqual(m.read(self.sd)['requestId'], requested['requestId'])
        self.kill.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'Custom'):
            self.request()

    def test_mode_or_pid_change_during_idle_probe_prevents_signal(self):
        self.request()
        def change_mode():
            (self.sd / 'openbot-mode').write_text('official')
            return {'isBusy': False}
        self.status = change_mode
        self.assertEqual(self.consume()['phase'], 'waiting-authority')
        self.kill.assert_not_called()
        (self.sd / 'openbot-mode').write_text('custom')
        def recycle_pid():
            self.current['startTicks'] = '999'
            return {'isBusy': False}
        self.status = recycle_pid
        self.assertEqual(self.consume()['phase'], 'waiting-authority')
        self.kill.assert_not_called()

    def test_source_changes_and_recent_writes_wait_without_bounce(self):
        self.request()
        self.host.write_bytes(b'new custom host')
        self.assertEqual(self.consume()['phase'], 'waiting-request-refresh')
        self.request()
        self.assertEqual(self.consume()['phase'], 'waiting-stable-source')
        os.utime(self.host, (time.time() - 10, time.time() - 10))
        def change_source():
            self.host.write_bytes(b'another host')
            return {'isBusy': False}
        self.status = change_source
        self.assertEqual(self.consume()['phase'], 'waiting-authority')
        self.kill.assert_not_called()

    def test_newer_request_is_not_erased_by_previous_signal_or_ack(self):
        first = self.request()
        # This models a newer atomic request arriving immediately after the
        # consumer's final signal; the consumer performs no stale cleanup.
        def newer_request(*_):
            newer = {**m.read(self.sd), 'requestId': '00000000-0000-4000-8000-000000000002', 'phase': 'pending'}
            m.write(self.sd, newer)
        self.kill.side_effect = newer_request
        self.consume()
        second = m.read(self.sd)
        self.assertNotEqual(second['requestId'], first['requestId'])
        self.current = {'pid': 124, 'startTicks': '777'}
        result = self.consume()
        self.assertEqual(result['requestId'], second['requestId'])
        self.assertEqual(m.read(self.sd)['requestId'], second['requestId'])
        self.assertEqual(self.kill.call_count, 1)

    def test_signal_failure_remains_pending_and_source_is_not_changed(self):
        self.request()
        before = self.host.read_bytes()
        self.kill.side_effect = ProcessLookupError()
        with self.assertRaises(ProcessLookupError):
            self.consume()
        self.assertEqual(m.read(self.sd)['phase'], 'pending')
        self.assertEqual(self.host.read_bytes(), before)

    def test_unsafe_marker_and_lock_are_refused(self):
        foreign = self.sd / 'foreign'; foreign.write_text('untouched')
        marker = self.sd / m.NAME; marker.symlink_to(foreign)
        with self.assertRaises(OSError): self.request()
        marker.unlink()
        lock = self.sd / (m.NAME + '.lock'); lock.unlink(); lock.symlink_to(foreign)
        with self.assertRaises(OSError): self.request()
        self.assertEqual(foreign.read_text(), 'untouched')
        self.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
