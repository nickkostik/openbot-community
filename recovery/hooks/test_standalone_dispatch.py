import concurrent.futures
import copy
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location('standalone_dispatch',
    pathlib.Path(__file__).with_name('standalone-dispatch.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
AGENT = '00000000-0000-4000-8000-000000000001'
NONCE = '00000000-0000-4000-8000-000000000002'


class Transport:
    def __init__(self):
        self.original = m.refusal()
        self.rows = [{'id': '123', 'agentId': AGENT, 'legacyAgentId': AGENT,
                      'harness': 'box', 'viewerIsOwner': True}]
        self.local_rows = [{'id': AGENT}]
        self.records = {}
        self.calls = []
        self.send_hook = None
        self.lock = threading.Lock()
        self.lookup_override = None

    def platform(self, method, body, headers):
        with self.lock:
            self.calls.append((method, copy.deepcopy(body), copy.deepcopy(headers)))
        if method == 'ListGrokBotAgents':
            return m.Result(200, {'agents': copy.deepcopy(self.rows)})
        if method == 'GetGrokBotSendStatus':
            return m.Result(200, {'status': m.STATUS + 'NOT_FOUND'})
        if isinstance(self.original, Exception):
            raise self.original
        return self.original

    def gateway(self, method, body):
        with self.lock:
            self.calls.append((method, copy.deepcopy(body), None))
        if method == 'listAgents':
            return m.Result(200, copy.deepcopy(self.local_rows))
        if method == 'promptAcceptanceStatus':
            if self.lookup_override is not None:
                return self.lookup_override
            with self.lock:
                record = copy.deepcopy(self.records.get(body['clientNonce']))
            return m.Result(200, {'outcome': 'found', 'record': record} if record else
                            {'outcome': 'unknown-durability'})
        if method == 'sendPrompt':
            if self.send_hook:
                return self.send_hook(body)
            self.accept(body)
            return m.Result(200, {'accepted': True})
        raise AssertionError('Unexpected native method')

    def accept(self, body, status='accepted', code=None):
        with self.lock:
            self.records[body['clientNonce']] = {
                'accountSlot': 'host', 'clientNonce': body['clientNonce'],
                'agentId': body['agentId'], 'inputDigest': m.input_digest(body),
                'status': status, 'acceptedAtMs': 123456789,
                'echoEntryId': 'echo-1', 'rejectionCode': code}

    def count(self, method):
        return sum(call[0] == method for call in self.calls)


class StandaloneDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / 'private' / 'receipts.sqlite3'
        self.store = m.Receipts(self.path)
        self.transport = Transport()
        self.allowed = True
        self.dispatcher = m.Dispatcher(self.transport, self.store,
                                     lambda: self.allowed, now_ms=lambda: 100)
        self.body = {'agentId': '123', 'messageId': NONCE, 'text': 'Hello',
                     'sentAtMs': '123456789', 'source': 'GROK_BOT_CLIENT_SURFACE_MOBILE',
                     'isFork': False, 'attachmentPaths': [], 'attachmentNames': []}
        self.headers = {'Authorization': 'Bearer test-caller', 'User-Agent': 'Original client'}

    def send(self, body=None):
        return self.dispatcher.send(self.body if body is None else body, self.headers)

    def status(self, body=None):
        return self.dispatcher.status({'agentId': '123', 'messageId': NONCE} if body is None else body,
                                      self.headers)

    def assert_no_native_send(self):
        self.assertEqual(self.transport.count('sendPrompt'), 0)

    def test_explicit_refusal_hands_off_once_and_preserves_native_nonce(self):
        result = self.send()
        self.assertEqual(result, m.Result(200, {'dispatched': True,
            'delivery': m.DELIVERY + 'ACCEPTED_BOX'}))
        self.assertEqual(self.transport.count('sendPrompt'), 1)
        request = next(call[1] for call in self.transport.calls if call[0] == 'sendPrompt')
        self.assertEqual(request['clientNonce'], NONCE)
        self.assertEqual(request['agentId'], AGENT)
        self.assertEqual(request['prompt'], 'Hello')
        self.assertTrue(request['directAddressedAcceptance'])

    def test_retries_do_not_repeat_upstream_or_native_dispatch(self):
        self.send()
        for _ in range(5):
            self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'DUPLICATE')
        self.assertEqual(self.transport.count('SendGrokBotUserMessage'), 1)
        self.assertEqual(self.transport.count('sendPrompt'), 1)
        self.assertEqual(self.transport.count('ListGrokBotAgents'), 6)

    def test_accepted_status_contains_actual_echo_and_wire_timestamp(self):
        self.send()
        self.assertEqual(self.status(), m.Result(200, {'status': m.STATUS + 'ACCEPTED',
            'acceptedAtMs': '123456789', 'echoEntryId': 'echo-1'}))

    def test_confirmed_receipt_survives_process_restart_and_native_eviction(self):
        self.send()
        self.transport.records.clear()
        self.dispatcher = m.Dispatcher(self.transport, m.Receipts(self.path), lambda: True)
        self.assertEqual(self.status().body['status'], m.STATUS + 'ACCEPTED')
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'DUPLICATE')
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_crash_after_claim_never_replays_a_receiptless_send(self):
        request = m.native_send(self.body, AGENT)
        self.store.claim(NONCE, '123', AGENT, m.input_digest(request), 1)
        self.dispatcher = m.Dispatcher(self.transport, m.Receipts(self.path), lambda: True)
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.status().body['status'], m.STATUS + 'UNKNOWN_DURABILITY')
        self.assertEqual(self.transport.count('SendGrokBotUserMessage'), 0)
        self.assert_no_native_send()

    def test_platform_accepted_duplicate_or_other_refusal_remains_unchanged(self):
        cases = [m.Result(200, {'delivery': m.DELIVERY + value, 'dispatched': True})
                 for value in ('ACCEPTED_BOX', 'ACCEPTED_TEMPORAL', 'DUPLICATE')]
        cases += [m.refusal(code) for code in ('send/agent-no-access', 'quota/exceeded')]
        cases += [m.Result(429, {'code': 'resource_exhausted'}),
                  m.Result(502, {'code': 'unavailable'})]
        for result in cases:
            with self.subTest(result=result):
                self.transport.original = result
                self.assertIs(self.send(), result)
        self.assert_no_native_send()
        self.assertIsNone(self.store.lookup(NONCE))

    def test_platform_timeout_is_not_a_local_fallback_trigger(self):
        self.transport.original = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.send()
        self.assert_no_native_send()
        self.assertIsNone(self.store.lookup(NONCE))

    def test_ambiguous_refusal_is_never_reinterpreted_as_safe_to_dispatch(self):
        for change in ({'dispatched': True}, {'dispatched': 'false'},
                       {'workflowId': 'in-flight'}, {'futureDispatchHint': True}):
            with self.subTest(change=change):
                self.transport.original = m.Result(200, {**m.refusal().body, **change})
                self.assertIs(self.send(), self.transport.original)
        self.assert_no_native_send()

    def test_real_platform_ownership_harness_and_unique_identity_are_required(self):
        original = copy.deepcopy(self.transport.rows)
        variants = [[], original * 2, [{**original[0], 'harness': 'temporal'}],
                    [original[0], {**original[0], 'id': '456'}],
                    [{**original[0], 'viewerIsOwner': False}],
                    [{**original[0], 'legacyAgentId': 'different'}],
                    [{**original[0], 'agentId': 'not-a-uuid'}]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.transport.rows = rows
                self.assertIs(self.send(), self.transport.original)
        self.assert_no_native_send()

    def test_native_roster_summary_cannot_create_platform_authority(self):
        self.transport.rows[0]['harness'] = 'temporal'
        self.transport.local_rows[0]['harness'] = 'box'
        self.assertIs(self.send(), self.transport.original)
        self.assert_no_native_send()

    def test_local_policy_and_matching_native_target_are_required(self):
        self.allowed = False
        self.assertIs(self.send(), self.transport.original)
        self.allowed = True
        for rows in ([], self.transport.local_rows * 2, [{'id': NONCE}]):
            self.transport.local_rows = rows
            self.assertIs(self.send(), self.transport.original)
        self.assert_no_native_send()

    def test_original_caller_headers_are_reused_for_owner_verification(self):
        self.send()
        platform_calls = [call for call in self.transport.calls if call[2] is not None]
        self.assertTrue(platform_calls)
        self.assertTrue(all(call[2] == self.headers for call in platform_calls))

    def test_prompt_reply_fork_and_attachment_metadata_are_preserved(self):
        body = {**self.body, 'text': 'Hi\n😀 "quoted"', 'richText': '<p>Hi</p>',
            'replyToId': 'original-echo', 'isFork': True,
            'attachmentPaths': ['/shared/input.txt'], 'attachmentNames': ['input.txt'],
            'traceparent': 'trace-marker', 'enterEpochMs': '100', 'composedAtMs': '101'}
        before = copy.deepcopy(body)
        self.send(body)
        request = next(call[1] for call in self.transport.calls if call[0] == 'sendPrompt')
        for name in ('richText', 'replyToId', 'isFork', 'attachmentPaths', 'attachmentNames', 'traceparent'):
            self.assertEqual(request[name], body[name])
        self.assertEqual(request['prompt'], body['text'])
        self.assertEqual(request['enterEpochMs'], 100)
        self.assertEqual(request['composedAtMs'], 101)
        self.assertEqual(body, before)

    def test_unvalidated_fields_sessions_and_surfaces_do_not_silently_disappear(self):
        for change in ({'futureInput': True}, {'sessionId': 'another-session'},
                       {'initiator': 'GROK_BOT_USER_MESSAGE_INITIATOR_FUTURE'},
                       {'source': 'GROK_BOT_CLIENT_SURFACE_DESKTOP'}):
            with self.subTest(change=change):
                self.assertIs(self.send({**self.body, **change}), self.transport.original)
        self.assert_no_native_send()

    def test_inspected_mobile_initiator_and_capability_fields_are_preserved(self):
        values = ('user', 'template_import', 'template_share', 'card_action',
                  'plugin_setup', 'teach_recording')
        for value in values:
            for installed in (True, False):
                body = {**self.body, 'initiator': 'GROK_BOT_USER_MESSAGE_INITIATOR_' + value.upper(),
                        'onePasswordAppInstalled': installed}
                before = copy.deepcopy(body)
                native = m.native_send(body, AGENT)
                self.assertEqual(native['initiator'], value)
                self.assertIs(native['onePasswordAppInstalled'], installed)
                self.assertEqual(body, before)

    def test_invalid_mobile_capability_or_initiator_cannot_dispatch(self):
        for change in ({'onePasswordAppInstalled': 1}, {'onePasswordAppInstalled': 'true'},
                       {'onePasswordAppInstalled': None}, {'initiator': 'user'},
                       {'initiator': 1}, {'initiator': None}):
            with self.subTest(change=change):
                self.assertIs(self.send({**self.body, **change}), self.transport.original)
        self.assert_no_native_send()

    def test_malformed_mobile_input_is_not_locally_dispatched(self):
        for change in ({'messageId': 'bad'}, {'text': None}, {'sentAtMs': 123},
                       {'isFork': 1}, {'attachmentPaths': 'path'},
                       {'attachmentNames': [1]}, {'richText': None}, {'enterEpochMs': '-1'}):
            with self.subTest(change=change):
                self.assertIs(self.send({**self.body, **change}), self.transport.original)
        self.assert_no_native_send()

    def test_reused_nonce_with_changed_content_is_rejected_before_any_send(self):
        self.send()
        for change in ({'text': 'different'}, {'richText': '<p>different</p>'},
                       {'replyToId': 'different'}, {'isFork': True},
                       {'attachmentPaths': ['/different']}, {'attachmentNames': ['different']}):
            with self.subTest(change=change):
                result = self.send({**self.body, **change})
                self.assertEqual(result.body['refusal']['failureCode'], 'send/nonce-digest-mismatch')
        self.assertEqual(self.transport.count('sendPrompt'), 1)
        self.assertEqual(self.transport.count('SendGrokBotUserMessage'), 1)

    def test_existing_native_receipt_is_not_redispatched(self):
        self.transport.accept(m.native_send(self.body, AGENT))
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'DUPLICATE')
        self.assert_no_native_send()

    def test_native_receipt_mismatch_cannot_be_coalesced_into_another_turn(self):
        original = m.native_send(self.body, AGENT)
        self.transport.accept(original)
        for field, value in (('inputDigest', '0' * 64), ('agentId', NONCE),
                             ('clientNonce', AGENT), ('accountSlot', 'another-owner')):
            with self.subTest(field=field):
                self.transport.records[NONCE][field] = value
                result = self.send()
                self.assertEqual(result.body['refusal']['failureCode'], 'send/nonce-digest-mismatch')
                self.assert_no_native_send()
                self.transport.accept(original)

    def test_native_accept_boolean_alone_is_not_reported_as_delivery(self):
        self.transport.send_hook = lambda _: m.Result(200, {'accepted': True})
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.status().body['status'], m.STATUS + 'UNKNOWN_DURABILITY')
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_lost_native_response_is_resolved_through_the_actual_receipt(self):
        def send(body):
            self.transport.accept(body)
            raise TimeoutError()
        self.transport.send_hook = send
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'ACCEPTED_BOX')
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_native_timeout_without_receipt_is_not_replayed(self):
        self.transport.send_hook = mock.Mock(side_effect=TimeoutError())
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_pending_then_accepted_status_requires_native_confirmation(self):
        def send(body):
            self.transport.accept(body, 'pending')
            return m.Result(200, {'accepted': True})
        self.transport.send_hook = send
        self.assertEqual(self.send().status, 503)
        self.assertEqual(self.status().body['status'], m.STATUS + 'PENDING')
        self.transport.records[NONCE]['status'] = 'accepted'
        self.assertEqual(self.status().body['status'], m.STATUS + 'ACCEPTED')
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'DUPLICATE')
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_native_rejection_is_preserved_across_retry_and_status(self):
        def send(body):
            self.transport.accept(body, 'rejected', 'send/agent-no-access')
            return m.Result(500, {'error': 'Rejected'})
        self.transport.send_hook = send
        self.assertEqual(self.send().body['refusal']['failureCode'], 'send/agent-no-access')
        self.assertEqual(self.status().body['status'], m.STATUS + 'REJECTED')
        self.assertEqual(self.status().body['rejectionCode'], 'send/agent-no-access')
        self.transport.records.clear()
        self.assertEqual(self.send().body['refusal']['failureCode'], 'send/agent-no-access')
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_concurrent_terminal_receipt_wins_over_a_stale_pending_read(self):
        request = m.native_send(self.body, AGENT)
        self.store.claim(NONCE, '123', AGENT, m.input_digest(request), 1)
        self.transport.accept(request, 'pending')
        pending = copy.deepcopy(self.transport.records[NONCE])
        terminal = {**pending, 'status': 'accepted'}
        observe = self.store.observe
        def overlapping(claim, record):
            observe(claim, terminal)
            observe(claim, record)
        with mock.patch.object(self.store, 'observe', side_effect=overlapping):
            self.assertEqual(self.status().body['status'], m.STATUS + 'ACCEPTED')
        self.assertEqual(self.store.lookup(NONCE)['status'], 'accepted')
        self.assert_no_native_send()

    def test_unknown_or_malformed_native_status_does_not_authorize_send(self):
        for result in (m.Result(502, {}), m.Result(200, []),
                       m.Result(200, {'outcome': 'future'}),
                       m.Result(200, {'outcome': 'unknown-durability', 'record': {}}),
                       m.Result(200, {'outcome': 'found', 'record': None})):
            with self.subTest(result=result):
                # Reset only the private test store, never a production journal.
                nonce = str(__import__('uuid').uuid4())
                self.transport.lookup_override = result
                self.assertEqual(self.send({**self.body, 'messageId': nonce}).status, 503)
        self.assert_no_native_send()

    def test_malformed_found_receipt_cannot_manufacture_acceptance(self):
        for field, value in (('status', 'future'), ('acceptedAtMs', True),
                             ('acceptedAtMs', -1), ('acceptedAtMs', '123'),
                             ('echoEntryId', 123), ('rejectionCode', [])):
            with self.subTest(field=field, value=value):
                nonce = str(__import__('uuid').uuid4())
                body = {**self.body, 'messageId': nonce}
                self.transport.accept(m.native_send(body, AGENT))
                self.transport.records[nonce][field] = value
                self.assertEqual(self.send(body).status, 503)
                self.assertEqual(self.status({'agentId': '123', 'messageId': nonce}).body['status'],
                                 m.STATUS + 'UNKNOWN_DURABILITY')
        self.assert_no_native_send()

    def test_retained_receipt_cannot_bypass_current_caller_ownership(self):
        self.send()
        self.transport.rows[0]['viewerIsOwner'] = False
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'REFUSED')
        self.assertEqual(self.status().body, {'status': m.STATUS + 'UNKNOWN_DURABILITY'})
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_roster_flipping_to_temporal_does_not_redirect_a_claimed_nonce(self):
        self.send()
        self.transport.rows[0]['harness'] = 'temporal'
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'REFUSED')
        self.assertEqual(self.transport.count('SendGrokBotUserMessage'), 1)
        self.assertEqual(self.transport.count('sendPrompt'), 1)

    def test_status_without_bridge_claim_is_forwarded_unchanged(self):
        self.assertEqual(self.status().body, {'status': m.STATUS + 'NOT_FOUND'})
        self.assertEqual(self.transport.count('GetGrokBotSendStatus'), 1)
        self.assertEqual(self.transport.count('promptAcceptanceStatus'), 0)

    def test_twelve_simultaneous_retries_claim_one_native_send(self):
        def send(body):
            time.sleep(0.05)
            self.transport.accept(body)
            return m.Result(200, {'accepted': True})
        self.transport.send_hook = send
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda _: self.send(), range(12)))
        self.assertTrue(any(result.status == 200 for result in results))
        self.assertEqual(self.transport.count('sendPrompt'), 1)
        self.assertEqual(self.send().body['delivery'], m.DELIVERY + 'DUPLICATE')

    def test_separate_processes_share_one_durable_claim(self):
        code = '''import importlib.util,sys
spec=importlib.util.spec_from_file_location('dispatcher',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
print(int(m.Receipts(sys.argv[2]).claim(sys.argv[3],'123',sys.argv[4],'0'*64,1)))
'''
        args = [sys.executable, '-c', code, str(pathlib.Path(m.__file__).resolve()),
                str(self.path), NONCE, AGENT]
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for _ in range(12)]
        try:
            results = [process.communicate(timeout=10) for process in processes]
            self.assertTrue(all(process.returncode == 0 for process in processes), results)
            self.assertEqual(sum(int(stdout.strip()) for stdout, _ in results), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)

    def test_journal_contains_no_prompt_or_credentials_and_has_private_permissions(self):
        body = {**self.body, 'text': 'private-message-sentinel'}
        self.send(body)
        contents = self.path.read_bytes()
        self.assertNotIn(b'private-message-sentinel', contents)
        self.assertNotIn(b'Bearer test-caller', contents)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_symlink_and_public_journal_are_refused(self):
        linked = self.path.parent / 'linked.sqlite3'
        linked.symlink_to(self.path)
        with self.assertRaises(ValueError):
            m.Receipts(linked)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            m.Receipts(self.path)


if __name__ == '__main__':
    unittest.main()
