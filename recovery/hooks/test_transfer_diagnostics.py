"""The optional advisor cannot dispatch, exfiltrate context, or confer proof."""
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('transfer_diagnostics', pathlib.Path(__file__).with_name('transfer-diagnostics.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        (self.root / 'openbot-mode').write_text('custom')
        self.plan = {'agents': {'*': {'providerId': 'opencode-2', 'modelId': 'muse'}}, 'catalog': {
            'providers': [{'id': 'opencode-2', 'origin': 'https://opencode.ai/zen/go/v1'}],
            'models': [{'id': 'opencode-2:muse', 'providerId': 'opencode-2', 'slug': 'muse', 'reasoningLevels': ['default', 'low', 'medium', 'high', 'xhigh'], 'activeReasoning': 'xhigh'}]}}
        self.save_plan()
        self.state = {'jobId': '14238183-e7f8-48a2-96ea-994a641cf49b', 'phase': 'review-needed',
            'refusalPhase': 'waiting-for-publication', 'diagnosticCode': 'preflight-refused',
            'counts': {'planned': 34, 'copied': 34, 'registered-box': 34, 'active': 0},
            'privateError': 'secret-key customer@example.test /home/private', 'history': ['private conversation']}
        self.save_state()
        patch = mock.patch.object(m, 'owned_hop', return_value=True)
        self.owned = patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(m, 'request', return_value=self.response())
        self.request = patch.start(); self.addCleanup(patch.stop)

    def save_plan(self):
        (self.root / 'openbot-plan.json').write_text(json.dumps(self.plan))

    def save_state(self):
        (self.root / m.STATE).write_text(json.dumps(self.state))

    @staticmethod
    def response(content=None, **extra):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': content or '{"adviceCode":"publication-backlog"}', **extra}}]}

    def test_only_allowlisted_facts_leave_and_advice_has_no_execution_authority(self):
        result = m.run(self.root, now=1000)
        model, job, facts, effort = self.request.call_args.args
        self.assertEqual(effort, 'low')
        self.assertEqual(json.loads((self.root / 'openbot-plan.json').read_text()), self.plan)
        self.assertEqual(model, 'opencode-2:muse')
        self.assertEqual(set(facts), {'phase', 'diagnosticCode', 'counts', 'countsKnown', 'publicationVerified', 'liveHistoryMerged', 'sdkContextsMerged'})
        self.assertNotIn('secret-key', json.dumps(facts))
        self.assertNotIn(job, json.dumps(facts))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['advice'], m.ADVICE['publication-backlog'])
        self.assertFalse(result['nativeAppDeliveryVerified'])
        self.assertEqual(json.loads((self.root / m.STATE).read_text()), self.state)
        self.assertEqual((self.root / m.NAME).stat().st_mode & 0o777, 0o600)

    def test_restart_and_same_refusal_never_repeat_billable_call(self):
        m.run(self.root, now=1000)
        m.run(self.root, now=1000 + 24 * 60 * 60 * 1000)
        self.assertEqual(self.request.call_count, 1)

    def test_changed_facts_obey_backoff_then_allow_one_call(self):
        m.run(self.root, now=1000)
        self.state['counts']['copied'] = 33
        self.save_state()
        m.run(self.root, now=1001)
        self.assertEqual(self.request.call_count, 1)
        m.run(self.root, now=1000 + m.BACKOFF_MS)
        self.assertEqual(self.request.call_count, 2)

    def test_a_b_a_and_module_restart_never_rebill_a(self):
        m.run(self.root, now=1000)
        self.state['counts']['copied'] = 33
        self.save_state()
        m.run(self.root, now=1000+m.BACKOFF_MS)
        second = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(second)
        self.state['counts']['copied'] = 34
        self.save_state()
        with mock.patch.object(second, 'owned_hop', return_value=True), mock.patch.object(second, 'request') as request:
            second.run(self.root, now=1000+2*m.BACKOFF_MS)
            request.assert_not_called()
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(len(json.loads((self.root/m.NAME).read_text())['seenFingerprints']), 2)

    def test_sixteen_calls_exhaust_job_without_eviction_and_new_job_resets_budget(self):
        for index in range(m.MAX_JOB_CALLS):
            self.state['counts']['copied'] = index
            self.save_state()
            m.run(self.root, now=1000+index*m.BACKOFF_MS)
        self.assertEqual(self.request.call_count, m.MAX_JOB_CALLS)
        saved = json.loads((self.root/m.NAME).read_text())
        self.assertEqual(len(saved['seenFingerprints']), m.MAX_JOB_CALLS)
        self.assertLessEqual((self.root/m.NAME).stat().st_size, 4096)
        self.state['counts']['copied'] = 99
        self.save_state()
        result = m.run(self.root, now=1000+m.MAX_JOB_CALLS*m.BACKOFF_MS)
        self.assertEqual(result['reason'], 'diagnostic-budget-exhausted')
        self.assertEqual(self.request.call_count, m.MAX_JOB_CALLS)
        self.assertEqual(json.loads((self.root/m.NAME).read_text())['seenFingerprints'], saved['seenFingerprints'])
        self.state['jobId'] = '24238183-e7f8-48a2-96ea-994a641cf49b'
        self.save_state()
        m.run(self.root, now=1000+(m.MAX_JOB_CALLS+1)*m.BACKOFF_MS)
        self.assertEqual(self.request.call_count, m.MAX_JOB_CALLS+1)
        self.assertEqual(len(json.loads((self.root/m.NAME).read_text())['seenFingerprints']), 1)

    def test_legacy_fingerprint_and_preflight_failure_preserve_existing_receipts(self):
        m.run(self.root, now=1000)
        saved = json.loads((self.root/m.NAME).read_text())
        saved.pop('seenFingerprints')
        m.write(self.root, saved)
        self.state['counts']['copied'] = 33
        self.save_state()
        with mock.patch.object(m, 'owned_hop', side_effect=m.Refusal('hop-not-owned')):
            m.run(self.root, now=1000+m.BACKOFF_MS)
        preserved = json.loads((self.root/m.NAME).read_text())
        self.assertEqual(preserved['seenFingerprints'], [saved['fingerprint']])
        self.state['counts']['copied'] = 34
        self.save_state()
        m.run(self.root, now=1000+2*m.BACKOFF_MS)
        self.assertEqual(self.request.call_count, 1)

    def test_invalid_ledger_fails_closed_without_call_or_receipt_eviction(self):
        m.run(self.root, now=1000)
        saved = json.loads((self.root/m.NAME).read_text())
        saved['seenFingerprints'] = ['bad-hash']
        m.write(self.root, saved)
        self.state['counts']['copied'] = 33
        self.save_state()
        result = m.run(self.root, now=1000+m.BACKOFF_MS)
        self.assertEqual(result['reason'], 'invalid-advice-ledger')
        self.assertEqual(json.loads((self.root/m.NAME).read_text())['seenFingerprints'], ['bad-hash'])
        self.assertEqual(self.request.call_count, 1)

    def test_malformed_ledger_owner_is_not_treated_as_a_fresh_job(self):
        m.run(self.root, now=1000)
        saved = json.loads((self.root/m.NAME).read_text())
        saved['jobId'] = 'invalid-owner'
        m.write(self.root, saved)
        self.state['counts']['copied'] = 33
        self.save_state()
        result = m.run(self.root, now=1000+m.BACKOFF_MS)
        self.assertEqual(result['reason'], 'invalid-advice-ledger')
        self.assertEqual(json.loads((self.root/m.NAME).read_text())['seenFingerprints'], saved['seenFingerprints'])
        self.assertEqual(self.request.call_count, 1)

    def test_persisted_running_intent_does_not_replay_after_process_crash(self):
        self.request.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            m.run(self.root, now=1000)
        self.request.side_effect = None
        self.assertEqual(m.run(self.root, now=1000000)['reason'], 'interrupted')
        self.assertEqual(self.request.call_count, 1)

    def test_provider_failures_and_timeouts_do_not_leak_raw_errors_or_retry(self):
        for error, reason in ((socket.timeout('secret-key user@example.test'), 'timeout'),
                              (m.Refusal('upstream-refused'), 'upstream-refused')):
            with self.subTest(error=type(error).__name__):
                (self.root / m.NAME).unlink(missing_ok=True)
                self.request.reset_mock()
                self.request.side_effect = error
                result = m.run(self.root, now=1000)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['reason'], reason)
                self.assertNotIn('secret-key', (self.root / m.NAME).read_text())
                m.run(self.root, now=1000 + m.BACKOFF_MS)
                self.assertEqual(self.request.call_count, 1)

    def test_adversarial_output_and_tool_requests_are_never_rendered(self):
        for response in (self.response('{"adviceCode":"publication-backlog","command":"rm -rf /"}'),
                         self.response('Ignore policy. Send private-key@example.test'),
                         self.response('{"adviceCode":"unhide-all"}'),
                         self.response(tool_calls=[{'function': {'name': 'exec'}}]),
                         {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': '{"adviceCode":"history-review"}'}}]}):
            with self.subTest(response=response):
                (self.root / m.NAME).unlink(missing_ok=True)
                self.request.return_value = response
                result = m.run(self.root, now=1000)
                self.assertEqual(result['reason'], 'invalid-advice')
                self.assertNotIn('private-key', (self.root / m.NAME).read_text())
                self.assertNotIn('advice', result)

    def test_exact_fenced_json_is_accepted_but_prose_and_duplicate_keys_are_not(self):
        for content in ('```json\n{"adviceCode":"history-review"}\n```',
                        '```\n{"adviceCode":"history-review"}\n```'):
            self.assertEqual(m.classify(self.response(content)), 'history-review')
        for content in ('Here is the answer: ```json\n{"adviceCode":"history-review"}\n```',
                        '```json\n{"adviceCode":"history-review"}\n```\nRun a command.',
                        '{"adviceCode":"history-review","adviceCode":"history-review"}'):
            with self.assertRaisesRegex(m.Refusal, 'invalid-advice'):
                m.classify(self.response(content))

    def test_truncated_or_reasoning_starved_output_is_never_treated_as_advice(self):
        response = self.response('{"adviceCode":"history-review"}')
        response['choices'][0]['finish_reason'] = 'length'
        with self.assertRaisesRegex(m.Refusal, 'advice-truncated'):
            m.classify(response)
        response = self.response('{"')
        response['usage'] = {'completion_tokens':256, 'completion_tokens_details':{'reasoning_tokens':254}}
        with self.assertRaisesRegex(m.Refusal, 'advice-truncated'):
            m.classify(response)
        response['usage']['completion_tokens'] = 2048
        with self.assertRaisesRegex(m.Refusal, 'invalid-advice'):
            m.classify(response)

    def test_malformed_usage_cannot_escape_invalid_advice_refusal(self):
        for usage in ([], None, 'invalid', 123, False):
            response = self.response('not-json')
            response['usage'] = usage
            with self.subTest(usage=usage), self.assertRaisesRegex(m.Refusal, 'invalid-advice'):
                m.classify(response)

    def test_explicit_low_survives_saved_xhigh_mapping_and_responses_conversion(self):
        payload = pathlib.Path(__file__).resolve().parents[2] / 'payload'
        script = """
const maps = require(process.argv[1]);
const converters = require(process.argv[2]);
const body = {model:'muse-test', messages:[], reasoning_effort:'low', max_tokens:256};
const parameters = [{id:'effort', value:'xhigh'}];
maps.applyProviderReasoningControls(body, {modelId:'muse-test', baseUrl:'https://opencode.ai/zen/go/v1', parameters});
console.log(JSON.stringify({wire:converters.chatToResponses(body), savedParameters:parameters}));
"""
        result = subprocess.run(['node', '-e', script, str(payload/'provider-maps.cjs'),
                                 str(payload/'protocol-converters.cjs')], check=True,
                                capture_output=True, text=True, timeout=5)
        value = json.loads(result.stdout)
        self.assertEqual(value['wire']['reasoning'], {'effort':'low'})
        self.assertEqual(value['wire']['max_output_tokens'], 256)
        self.assertEqual(value['savedParameters'], [{'id':'effort', 'value':'xhigh'}])

    def test_lowest_declared_diagnostic_effort_never_rewrites_selected_xhigh(self):
        original = (self.root/'openbot-plan.json').read_bytes()
        self.assertEqual(m.diagnostic_effort(self.root, 'opencode-2:muse'), 'low')
        self.assertEqual((self.root/'openbot-plan.json').read_bytes(), original)
        self.plan['catalog']['models'][0]['reasoningLevels'] = ['default', 'none', 'low', 'xhigh']
        self.save_plan()
        self.assertEqual(m.diagnostic_effort(self.root, 'opencode-2:muse'), 'none')
        self.plan['catalog']['models'][0]['reasoningLevels'] = ['default', 'high', 'xhigh']
        self.save_plan()
        self.assertEqual(m.diagnostic_effort(self.root, 'opencode-2:muse'), 'high')
        self.plan['catalog']['models'][0]['reasoningLevels'] = ['default']
        self.save_plan()
        with self.assertRaisesRegex(m.Refusal, 'unsupported-diagnostic-effort'):
            m.diagnostic_effort(self.root, 'opencode-2:muse')

    def test_official_optout_and_unapproved_provider_never_call(self):
        (self.root / 'openbot-mode').write_text('official')
        self.assertEqual(m.run(self.root, now=1000)['reason'], 'official-mode')
        (self.root / 'openbot-mode').write_text('custom')
        marker = self.root / 'openbot-transfer-diagnostics.disable'
        marker.touch()
        self.assertEqual(m.run(self.root, now=1000)['reason'], 'opted-out')
        marker.unlink()
        for origin in ('https://n5.example/v1', 'http://127.0.0.1:9465/v1', 'https://opencode.ai/zen/v1', 'https://opencode.ai/zen/go/v1?key=secret'):
            self.plan['catalog']['providers'][0]['origin'] = origin
            self.save_plan()
            self.assertEqual(m.run(self.root, now=1000)['reason'], 'unsupported-selected-route')
        self.request.assert_not_called()

    def test_missing_or_ambiguous_effort_never_makes_a_model_call(self):
        for levels in (None, ['default'], ['low','low']):
            (self.root/m.NAME).unlink(missing_ok=True)
            if levels is None:
                self.plan['catalog']['models'][0].pop('reasoningLevels', None)
            else:
                self.plan['catalog']['models'][0]['reasoningLevels'] = levels
            self.save_plan()
            self.assertIn(m.run(self.root, now=1000)['status'], ('disabled', 'failed'))
        self.request.assert_not_called()
        self.plan['catalog']['models'].append(dict(self.plan['catalog']['models'][0]))
        self.save_plan()
        with self.assertRaisesRegex(m.Refusal, 'invalid-route'):
            m.diagnostic_effort(self.root, 'opencode-2:muse')

    def test_route_or_job_change_during_response_discards_advice(self):
        def change(*args):
            self.state['counts']['active'] = 1
            self.save_state()
            return self.response()
        self.request.side_effect = change
        result = m.run(self.root, now=1000)
        self.assertEqual(result['reason'], 'state-changed')
        self.assertNotIn('advice', result)

    def test_known_late_history_refusal_can_request_review_but_unknown_phase_cannot(self):
        self.state['refusalPhase'] = 'preserving-late-history'
        self.state['diagnosticCode'] = 'history-conflict'
        self.save_state()
        result = m.run(self.root, now=1000)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(self.request.call_args.args[2]['phase'], 'preserving-late-history')
        self.assertEqual(self.request.call_count, 1)
        self.state['refusalPhase'] = 'preserving-unknown-private-history'
        self.save_state()
        result = m.run(self.root, now=1000+m.BACKOFF_MS)
        self.assertEqual(result['reason'], 'no-refusal')
        self.assertEqual(self.request.call_count, 1)
        self.assertEqual(len(json.loads((self.root/m.NAME).read_text())['seenFingerprints']), 1)

    def test_untrusted_state_and_invalid_enums_never_call(self):
        for field, value in (('refusalPhase', 'user@example.test'), ('diagnosticCode', 'execute-secret'), ('jobId', 'secret-key')):
            original = self.state[field]
            self.state[field] = value
            self.save_state()
            self.assertIn(m.run(self.root, now=1000)['status'], ('failed', 'disabled'))
            self.state[field] = original
        self.save_state()
        (self.root / m.STATE).unlink()
        (self.root / m.STATE).symlink_to(self.root / 'openbot-plan.json')
        self.assertEqual(m.run(self.root, now=1000)['status'], 'failed')
        self.request.assert_not_called()

    def test_previous_job_advice_does_not_bleed_into_next_job(self):
        m.run(self.root, now=1000)
        self.state['jobId'] = '24238183-e7f8-48a2-96ea-994a641cf49b'
        self.save_state()
        self.assertEqual(m.status(self.root)['reason'], 'not-requested')
        self.assertNotIn('advice', m.status(self.root))

    def test_status_filters_tampered_free_text(self):
        m.write(self.root, {'jobId': self.state['jobId'], 'status': 'available', 'adviceCode': 'history-review', 'advice': '<script>secret</script>',
                           'reason': 'secret-key', 'nativeAppDeliveryVerified': True})
        result = m.status(self.root)
        self.assertEqual(result['advice'], m.ADVICE['history-review'])
        self.assertFalse(result['nativeAppDeliveryVerified'])
        self.assertNotIn('reason', result)

    def test_missing_owned_listener_is_refused(self):
        self.owned.side_effect = m.Refusal('hop-not-owned')
        self.assertEqual(m.run(self.root, now=1000)['reason'], 'hop-not-owned')
        self.request.assert_not_called()


class HopOwnershipTests(unittest.TestCase):
    def test_exact_owned_listener_and_canonical_entrypoint_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            proc = root / 'proc'
            process = proc / '123'
            (process / 'fd').mkdir(parents=True)
            (process / 'net').mkdir()
            repo = root / 'openbot'
            repo.mkdir()
            (process / 'cwd').symlink_to(repo)
            (root / 'openbot-ui.pid').write_text('123')
            (process / 'cmdline').write_bytes(b'/exec-daemon/node\0--experimental-strip-types\0src/ui/server.ts\0')
            (process / 'fd/3').symlink_to('socket:[888]')
            table = process / 'net/tcp'
            table.write_text('header\n0: 0100007F:2440 00000000:0000 0A 0 0 0 0 0 888\n')
            self.assertTrue(m.owned_hop(root, proc, repo))
            table.write_text('header\n0: 00000000:2440 00000000:0000 0A 0 0 0 0 0 888\n')
            with self.assertRaises(m.Refusal):
                m.owned_hop(root, proc, repo)
            table.write_text('header\n0: 0100007F:2440 00000000:0000 0A 0 0 0 0 0 999\n')
            with self.assertRaises(m.Refusal):
                m.owned_hop(root, proc, repo)


class HopRequestTests(unittest.TestCase):
    def run_request(self, chunks, status=200):
        connection = mock.MagicMock()
        response = connection.getresponse.return_value
        response.status = status
        response.read1.side_effect = chunks
        with mock.patch.object(m.http.client, 'HTTPConnection', return_value=connection) as factory:
            try:
                result = m.request('opencode:muse', '14238183-e7f8-48a2-96ea-994a641cf49b', {'phase': 'waiting-for-publication'}, 'low')
            finally:
                connection.close.assert_called_once()
                factory.assert_called_once_with('127.0.0.1', 9280, timeout=m.TIMEOUT)
        return result, connection

    def test_auxiliary_has_no_tools_history_or_secret_and_only_calls_loopback(self):
        result, connection = self.run_request([b'{"choices":[]}', b''])
        args = connection.request.call_args.args
        self.assertEqual(args[:2], ('POST', '/v1/chat/completions'))
        body = json.loads(args[2])
        self.assertEqual(body['max_tokens'], 256)
        self.assertEqual(body['reasoning_effort'], 'low')
        self.assertTrue(body['openbot_internal_model'])
        self.assertTrue(body['openbot_internal_auxiliary'])
        self.assertNotIn('tools', body)
        self.assertNotIn('Authorization', args[3])
        # The owned hop supplies its installed OpenBot version for Go auxiliary calls.
        self.assertNotIn('User-Agent', args[3])
        self.assertEqual(len(body['messages']), 2)
        self.assertEqual(json.loads(body['messages'][1]['content']), {'phase': 'waiting-for-publication'})

    def test_response_size_is_bounded_and_redirect_is_not_followed(self):
        with self.assertRaisesRegex(m.Refusal, 'response-too-large'):
            self.run_request([b'a' * 4096] * 5)
        with self.assertRaisesRegex(m.Refusal, 'upstream-refused'):
            self.run_request([], status=302)

    def test_total_deadline_stops_slow_body(self):
        connection = mock.MagicMock()
        connection.getresponse.return_value.status = 200
        with mock.patch.object(m.http.client, 'HTTPConnection', return_value=connection), \
             mock.patch.object(m.time, 'monotonic', side_effect=[0, 1, 2, m.TIMEOUT + 1]):
            with self.assertRaisesRegex(m.Refusal, 'timeout'):
                m.request('opencode:muse', '14238183-e7f8-48a2-96ea-994a641cf49b', {}, 'low')
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
