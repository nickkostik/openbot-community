"""Native Watch activity metadata validation with bounded, offline framing.

Synthetic snapshots test the protocol reader, not observed cloud quiescence.
The opener is always local and opaque credentials are fixture values only.
"""
import importlib.util
import json
import pathlib
import struct
import types
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('return_source_activity', pathlib.Path(__file__).with_name('return-source-activity.py'))
A = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(A)
ONE = '00000000-0000-4000-8000-000000000001'
TWO = '00000000-0000-4000-8000-000000000002'
NOW = 1000


def live(aid=ONE, session='', **values):
    return {'agentId': aid, 'sessionId': session, 'updatedAtMs': str(NOW - 100),
            'staleAfterMs': '30000', **values}


def observation(counts):
    return {'checkedSources': len(counts), 'scope': 'known-default-session', 'knownSessionId': '',
            'observedNotRunning': True, 'admissionFenced': False, 'sampledAtMs': NOW, 'sessionCounts': counts}


def snapshot(*rows, **values):
    return {'agentState': {'snapshot': True, 'live': list(rows), **values}}


def frame(value, flags=0):
    raw = json.dumps(value, separators=(',', ':')).encode()
    return struct.pack('!BI', flags, len(raw)) + raw


class IdleSnapshotTests(unittest.TestCase):
    def test_present_typed_default_rows_use_native_false_defaults(self):
        value = snapshot(live(), live(TWO, isRunning=False,
            isComposingMessage=False, isRetrying=False, hasRunningSubagents=False))
        self.assertEqual(A.default_snapshot(value, {ONE, TWO}, NOW), {ONE: 1, TWO: 1})

    def test_any_known_nondefault_session_refuses_even_if_observed_not_running(self):
        with self.assertRaises(A.ActivityUnverified):
            A.default_snapshot(snapshot(live(), live(session='routine')), {ONE}, NOW)

    def test_non_snapshot_messages_do_not_assert_idle(self):
        for value in ({}, None, {'agentState': {}}, {'agentState': {'snapshot': False}},
                      {'agentState': {'snapshot': 1}}):
            with self.subTest(value=value):
                self.assertIsNone(A.default_snapshot(value, {ONE}, NOW))

    def test_absent_watch_overlay_only_reports_zero_for_status_companion_to_check(self):
        self.assertEqual(A.default_snapshot(snapshot(), {ONE}, NOW), {ONE: 0})
        self.assertEqual(A.default_snapshot(snapshot(live(TWO)), {ONE}, NOW), {ONE: 0})
        with self.assertRaises(A.ActivityUnverified):
            A.default_snapshot(snapshot(live(), live()), {ONE}, NOW)
        self.assertEqual(A.default_snapshot(snapshot(live()), {ONE, TWO}, NOW), {ONE: 1, TWO: 0})

    def test_malformed_live_rows_or_unknown_sessions_refuse(self):
        for value in (snapshot(live(), live=None), snapshot(None), snapshot(live(session=1)),
                      snapshot(live(session='line\nbreak')), snapshot(live(session='x' * 1025))):
            with self.subTest(value=value), self.assertRaises(A.ActivityUnverified):
                A.default_snapshot(value, {ONE}, NOW)

    def test_missing_and_non_integer_timestamps_refuse(self):
        for key in ('updatedAtMs', 'staleAfterMs'):
            for bad in (None, True, 1.5, '1.5', '-1', '١', {}, ''):
                value = live(); value[key] = bad
                with self.subTest(key=key, bad=bad), self.assertRaises(A.ActivityUnverified):
                    A.default_snapshot(snapshot(value), {ONE}, NOW)
            if key == 'updatedAtMs':
                value = live(); value.pop(key)
                with self.subTest(missing=key), self.assertRaises(A.ActivityUnverified):
                    A.default_snapshot(snapshot(value), {ONE}, NOW)

    def test_stale_future_or_unbounded_freshness_refuses(self):
        # Use cases invalid under either duration or absolute expiry semantics;
        # the native staleAfterMs interpretation is verified separately.
        for values in ({'updatedAtMs': '0', 'staleAfterMs': str(NOW)},
                       {'updatedAtMs': str(NOW + 1)},
                       {'staleAfterMs': str(2 ** 64)}):
            with self.subTest(values=values), self.assertRaises(A.ActivityUnverified):
                A.default_snapshot(snapshot(live(**values)), {ONE}, NOW)

    def test_present_live_row_uses_confirmed_native_default_stale_duration(self):
        for value in ({key: item for key, item in live().items() if key != 'staleAfterMs'},
                      live(staleAfterMs=0), live(staleAfterMs='0')):
            with self.subTest(value=value):
                self.assertEqual(A.default_snapshot(snapshot(value), {ONE}, NOW), {ONE: 1})
                self.assertEqual(A.default_snapshot(snapshot(value), {ONE}, NOW + 89899), {ONE: 1})
                with self.assertRaises(A.ActivityUnverified):
                    A.default_snapshot(snapshot(value), {ONE}, NOW + 89900)

    def test_running_retrying_composing_or_subagents_refuse(self):
        for key in ('isRunning', 'isComposingMessage', 'isRetrying', 'hasRunningSubagents'):
            for value in (True, None, 0, 'false'):
                with self.subTest(key=key, value=value), self.assertRaises(A.ActivityUnverified):
                    A.default_snapshot(snapshot(live(**{key: value})), {ONE}, NOW)

    def test_pending_activity_or_handoff_never_counts_as_idle(self):
        for key in ('activity', 'awaiting', 'boxHandoffRequestId', 'boxHandoffInstruction',
                    'activeGroupMemberId', 'surfaceTaskId'):
            with self.subTest(key=key), self.assertRaises(A.ActivityUnverified):
                A.default_snapshot(snapshot(live(**{key: 'pending'})), {ONE}, NOW)
        for key in ('activity', 'awaiting'):
            for value in ('', None, {}):
                with self.subTest(key=key, value=value), self.assertRaises(A.ActivityUnverified):
                    A.default_snapshot(snapshot(live(**{key: value})), {ONE}, NOW)
        self.assertEqual(A.default_snapshot(snapshot(live(boxHandoffRequestId='')), {ONE}, NOW), {ONE: 1})

    def test_unrequested_agents_do_not_supply_or_replace_requested_evidence(self):
        unrelated = {'agentId': TWO, 'isRunning': True}
        self.assertEqual(A.default_snapshot(snapshot(unrelated, live()), {ONE}, NOW), {ONE: 1})


class StatusSampleTests(unittest.TestCase):
    def test_present_typed_status_uses_proto_false_default_without_claiming_a_fence(self):
        for row in ({'agentId': ONE}, {'agentId': ONE, 'isRunning': False},
                    {'agentId': ONE, 'isRunning': False, 'live': live()}):
            with self.subTest(row=row):
                self.assertIsNone(A.status_sample({'statuses': [row]}, {ONE}, NOW))

    def test_missing_duplicate_or_wrong_status_identity_refuses(self):
        for value in ({}, {'statuses': []}, {'statuses': None}, {'statuses': [None]},
                      {'statuses': [{'agentId': TWO}]}, {'statuses': [{'agentId': ONE}, {'agentId': ONE}]}):
            with self.subTest(value=value), self.assertRaises(A.ActivityUnverified):
                A.status_sample(value, {ONE}, NOW)

    def test_running_or_malformed_proto_bool_refuses(self):
        for value in (True, None, 0, 'false'):
            with self.subTest(value=value), self.assertRaises(A.ActivityUnverified):
                A.status_sample({'statuses': [{'agentId': ONE, 'isRunning': value}]}, {ONE}, NOW)

    def test_present_live_container_requires_same_owner_fresh_default_and_no_pending_fields(self):
        for value in ({}, None, live(TWO), live(session='routine'), live(isRunning=True),
                      live(updatedAtMs=str(NOW - 30000)), live(activity={}), live(awaiting={}),
                      live(boxHandoffRequestId='pending'), live(activeGroupMemberId='pending')):
            with self.subTest(value=value), self.assertRaises(A.ActivityUnverified):
                A.status_sample({'statuses': [{'agentId': ONE, 'live': value}]}, {ONE}, NOW)



class KnownSessionMetadataTests(unittest.TestCase):
    def test_all_typed_watch_session_metadata_refuses_known_nondefault(self):
        for key in('rows','cleared','cursorTooOld','turnFailed'):
            with self.subTest(key=key),self.assertRaisesRegex(A.ActivityUnverified,'known nondefault'):
                A.default_snapshot({key:{'agentId':ONE,'sessionId':'secondary-session'}},{ONE},NOW)
        with self.assertRaisesRegex(A.ActivityUnverified,'known nondefault viewer'):
            A.default_snapshot({'rosterChanged':{'agentId':ONE,'agent':{'viewerSessionId':'secondary'}}},{ONE},NOW)
        with self.assertRaisesRegex(A.ActivityUnverified,'pending computer'):
            A.default_snapshot({'computerActions':{'actions':[{'agentId':ONE,'type':'click'}]}},{ONE},NOW)
        with self.assertRaisesRegex(A.ActivityUnverified,'failed turn'):
            A.default_snapshot({'turnFailed':{'agentId':ONE}},{ONE},NOW)

    def test_default_history_metadata_and_unrelated_source_do_not_claim_global_inventory(self):
        for key in('rows','cleared','cursorTooOld'):
            self.assertIsNone(A.default_snapshot({key:{'agentId':ONE}},{ONE},NOW))
            self.assertIsNone(A.default_snapshot({key:{'agentId':TWO,'sessionId':'secondary'}},{ONE},NOW))


class ChunkedStream:
    def __init__(self, data, chunk=7):
        self.data = data; self.chunk = chunk; self.offset = 0; self.reads = []; self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, size):
        self.reads.append(size)
        length = min(size, self.chunk)
        data = self.data[self.offset:self.offset + length]
        self.offset += len(data)
        return data


class WatchReaderTests(unittest.TestCase):
    def recovery(self, opener):
        return types.SimpleNamespace(require=mock.Mock(), r=types.SimpleNamespace(m=types.SimpleNamespace(
            H={'authorization': 'opaque-fixture-session'}, routing=types.SimpleNamespace(open_no_redirect=opener),
            rpc=mock.Mock(side_effect=lambda method, body: {'statuses': [{'agentId': aid} for aid in body['agentIds']]}))))

    def check(self, data, ids=None, chunk=7, **kwargs):
        self.stream = ChunkedStream(data, chunk)
        self.opener = mock.Mock(return_value=self.stream)
        self.rec = self.recovery(self.opener)
        return A.check(self.rec, {ONE} if ids is None else ids, now=NOW, **kwargs)

    def test_partial_reads_skip_non_snapshot_and_return_only_activity_metadata(self):
        data = frame({'entries': [{'body': 'opaque discarded transcript body'}]}) + frame(snapshot(live()))
        result = self.check(data, chunk=1)
        self.assertEqual(result, observation({ONE: 1}))
        self.assertEqual(self.rec.r.m.rpc.call_args_list, [mock.call('ListGrokBotAgentStatuses', {'agentIds': [ONE]})] * 2)
        self.assertTrue(self.stream.closed)
        self.assertGreater(self.rec.require.call_count, 4)
        request = self.opener.call_args.args[0]
        self.assertEqual(self.opener.call_args.kwargs, {'timeout': 5})
        self.assertEqual(request.full_url, 'https://api2.cursor.sh/aiserver.v1.GrokBotService/WatchGrokBotTranscripts')
        flags, size = struct.unpack('!BI', request.data[:5]); self.assertEqual(flags, 0)
        self.assertEqual(size, len(request.data[5:]))
        self.assertEqual(json.loads(request.data[5:]), {'cursors': [], 'includeUnlistedAgents': True, 'inlineBodyMaxBytes': 0})
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.assertEqual(headers['authorization'], 'opaque-fixture-session')
        self.assertEqual(headers['content-type'], 'application/connect+json')
        self.assertEqual(headers['connect-timeout-ms'], '10000')

    def test_absent_watch_rows_need_present_typed_status_before_and_after(self):
        self.assertEqual(self.check(frame(snapshot())), observation({ONE: 0}))
        self.assertEqual(self.rec.r.m.rpc.call_args_list, [mock.call('ListGrokBotAgentStatuses', {'agentIds': [ONE]})] * 2)
        for before, after in (({'statuses': []}, {'statuses': [{'agentId': ONE}]}),
                              ({'statuses': [{'agentId': ONE}]}, {'statuses': []}),
                              ({'statuses': [{'agentId': ONE}]}, {'statuses': [{'agentId': ONE, 'isRunning': True}]})):
            stream = ChunkedStream(frame(snapshot())); opener = mock.Mock(return_value=stream)
            rec = self.recovery(opener); rec.r.m.rpc.side_effect = [before, after]
            with self.subTest(before=before, after=after), self.assertRaises(A.ActivityUnverified):
                A.check(rec, {ONE}, now=NOW)
            if not before['statuses']:
                opener.assert_not_called()
            else:
                self.assertEqual(rec.r.m.rpc.call_count, 2)

    def test_busy_or_nondefault_watch_row_cannot_be_overridden_by_default_status(self):
        for row in (live(isRunning=True), live(session='routine'), live(activity={}), live(awaiting={})):
            with self.subTest(row=row), self.assertRaises(A.ActivityUnverified):
                self.check(frame(snapshot(row)))

    def test_explicit_opener_is_used_instead_of_configured_transport(self):
        configured = mock.Mock(side_effect=AssertionError('must use supplied test boundary'))
        rec = self.recovery(configured); stream = ChunkedStream(frame(snapshot(live())))
        opener = mock.Mock(return_value=stream)
        self.assertTrue(A.check(rec, {ONE}, opener=opener, now=NOW)['observedNotRunning'])
        configured.assert_not_called(); opener.assert_called_once()

    def test_absent_sources_need_no_adapter_or_network(self):
        rec = types.SimpleNamespace(require=mock.Mock())
        self.assertEqual(A.check(rec, [], now=NOW), observation({}))
        rec.require.assert_not_called()

    def test_authenticated_adapter_is_required_without_opening_stream(self):
        for field in ('routing', 'H'):
            opener = mock.Mock(); rec = self.recovery(opener); setattr(rec.r.m, field, None)
            with self.subTest(field=field), self.assertRaisesRegex(A.ActivityUnverified, 'adapter is unavailable'):
                A.check(rec, [ONE], now=NOW)
            opener.assert_not_called()

    def test_too_many_sources_refuse_before_network(self):
        opener = mock.Mock(); rec = self.recovery(opener)
        ids = {'00000000-0000-4000-8000-' + str(i).zfill(12) for i in range(33)}
        with self.assertRaisesRegex(A.ActivityUnverified, 'source count exceeds bound'):
            A.check(rec, ids, now=NOW)
        opener.assert_not_called()

    def test_compressed_or_unknown_frame_flags_and_oversize_refuse_before_body(self):
        for flags, size in ((1, 1), (3, 1), (4, 1), (0, A.MAX_FRAME + 1)):
            with self.subTest(flags=flags, size=size), self.assertRaisesRegex(A.ActivityUnverified, 'frame bound'):
                self.check(struct.pack('!BI', flags, size), chunk=5)
            self.assertEqual(self.stream.offset, 5)

    def test_total_bytes_and_frame_count_are_bounded(self):
        with mock.patch.object(A, 'MAX_TOTAL', 3), self.assertRaisesRegex(A.ActivityUnverified, 'frame bound'):
            self.check(frame({}) + frame({}))
        with mock.patch.object(A, 'MAX_FRAMES', 2), self.assertRaisesRegex(A.ActivityUnverified, 'snapshot was not received'):
            self.check(frame({}) + frame({}) + frame(snapshot(live())))
        self.assertEqual(self.stream.offset, len(frame({}) + frame({})))

    def test_partial_frame_or_early_trailer_cannot_assert_idle(self):
        for raw in (b'', b'\0\0', struct.pack('!BI', 0, 100) + b'{}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(A.ActivityUnverified, 'ended without settled evidence'):
                self.check(raw)
        with self.assertRaisesRegex(A.ActivityUnverified, 'snapshot was not received'):
            self.check(frame({'error': {'message': 'opaque upstream message'}}, flags=2))

    def test_deadline_is_checked_before_each_read(self):
        with mock.patch.object(A.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(A.ActivityUnverified, 'deadline exceeded'):
                self.check(frame(snapshot(live())))
        self.assertEqual(self.stream.reads, [])

    def test_transport_and_invalid_json_errors_do_not_expose_upstream_content(self):
        opener = mock.Mock(side_effect=RuntimeError('opaque transport details'))
        rec = self.recovery(opener)
        with self.assertRaisesRegex(A.ActivityUnverified, '^Cloud source activity could not be verified$'):
            A.check(rec, [ONE], now=NOW)
        raw = b'not-json-opaque-upstream-body'
        with self.assertRaisesRegex(A.ActivityUnverified, '^Cloud source activity could not be verified$'):
            self.check(struct.pack('!BI', 0, len(raw)) + raw)


if __name__ == '__main__':
    unittest.main()
