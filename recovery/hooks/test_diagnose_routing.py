import importlib.util
import contextlib
import http.server
import io
import json
import os
import pathlib
import threading
import urllib.error
import urllib.request
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('routing', pathlib.Path(__file__).with_name('diagnose-routing.py'))
routing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routing)

BUNDLE = '''function sandClientBaseVersionOf(clientAppVersion) {
  return stampedVersionBaseOf(clientAppVersion) ?? stampedVersionBaseOf(
    true ? "0.65.0-pre.6" : void 0
  ) ?? SAND_CLIENT_FALLBACK_BASE_VERSION;
}'''


class ClientMetadataTests(unittest.TestCase):
    def test_packaged_version_is_normalized(self):
        self.assertEqual(routing.client_version(BUNDLE), '0.65.0')

    def test_running_host_override_wins(self):
        self.assertEqual(routing.client_version(BUNDLE, ' 0.67.2-pre.3 '), '0.67.2')

    def test_deployment_hash_is_not_an_http_version(self):
        self.assertEqual(routing.client_version(BUNDLE, '7f4798a'), '0.65.0')

    def test_updated_bundles_do_not_keep_the_old_version(self):
        self.assertEqual(routing.client_version(BUNDLE.replace('0.65.0-pre.6', '0.68.1')), '0.68.1')

    def test_namespace_suffixes_match_the_host(self):
        self.assertEqual(routing.client_version(BUNDLE, namespace='dev'), '0.65.0-dev')
        self.assertEqual(routing.client_version(BUNDLE, namespace='lab'), '0.65.0-lab')

    def test_grok_is_not_a_valid_box_namespace(self):
        with self.assertRaises(RuntimeError):
            routing.client_version(BUNDLE, namespace='grok')

    def test_unknown_bundle_format_fails_without_guessing(self):
        with self.assertRaises(RuntimeError):
            routing.client_version('different implementation')

    def test_other_embedded_versions_are_not_used(self):
        with self.assertRaises(RuntimeError):
            routing.client_version('const unrelated = "0.65.0";')


class BoxRunStateTests(unittest.TestCase):
    def test_short_and_full_wire_enum_names_share_canonical_codes(self):
        for state in ('UNSPECIFIED', 'ABSENT', 'HIBERNATED', 'RUNNING', 'STARTING'):
            for spelling in (state, 'SAND_BOX_RUN_STATE_' + state):
                with self.subTest(spelling=spelling):
                    self.assertEqual(routing.box_run_state(spelling), state)

    def test_unknown_malformed_or_non_string_states_are_not_printable(self):
        for state in (None, 3, True, {}, [], '', 'running', ' RUNNING', 'RUNNING ',
                      'SAND_BOX_RUN_STATE_UNKNOWN', 'SAND_BOX_RUN_STATE_RUNNING-private-tenant',
                      'SAND_BOX_RUN_STATE_SAND_BOX_RUN_STATE_RUNNING', 'private-tenant'):
            with self.subTest(state=state):
                self.assertIsNone(routing.box_run_state(state))

    def test_default_cli_normalizes_real_wire_response(self):
        responses = [{'agents': []}, {'capabilities': {}},
                     {'state': 'SAND_BOX_RUN_STATE_RUNNING', 'egressTunnelOwnership': True}]
        headers = {'x-sand-box-namespace': 'prod', 'x-cursor-client-version': '0.65.0'}
        with mock.patch.object(routing, 'headers', return_value=headers), \
                mock.patch.object(routing, 'rpc', side_effect=responses), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(routing.main([]), 0)
        label, body = output.getvalue().splitlines()[-1].split(' ', 1)
        self.assertEqual(label, 'boxRunState')
        self.assertEqual(json.loads(body), {'state': 'RUNNING', 'egressTunnelOwnership': True})

    def test_default_cli_omits_unknown_state_and_keeps_valid_boolean_metadata(self):
        responses = [{'agents': []}, {'capabilities': {}},
                     {'state': 'private-tenant', 'imageUpdateAvailable': False}]
        headers = {'x-sand-box-namespace': 'prod', 'x-cursor-client-version': '0.65.0'}
        with mock.patch.object(routing, 'headers', return_value=headers), \
                mock.patch.object(routing, 'rpc', side_effect=responses), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(routing.main([]), 0)
        body = output.getvalue().splitlines()[-1].split(' ', 1)[1]
        self.assertEqual(json.loads(body), {'imageUpdateAvailable': False})
        self.assertNotIn('private-tenant', output.getvalue())


class PublicGatewayTests(unittest.TestCase):
    def setUp(self):
        self.ensured = {'gatewayUrl': 'https://private-pod.cursorvm.com',
                        'gatewayToken': 'gateway-secret', 'networkToken': 'network-secret',
                        'tenantId': 'private-tenant', 'podId': 'private-pod'}
        self.host = {'hostVersion': '7f4798a', 'capabilities': ['sendAcceptanceV1', 'orderedReplicasV1'],
                     'tenantId': 'private-tenant', 'token': 'response-secret'}

    def check(self, ensured=None, host=None, error=None):
        with mock.patch.object(routing, 'rpc', return_value=self.ensured if ensured is None else ensured) as rpc:
            with mock.patch.object(routing, 'open_no_redirect') as opened:
                if error:
                    opened.side_effect = error
                else:
                    opened.return_value = io.BytesIO(json.dumps(self.host if host is None else host).encode())
                result = routing.public_gateway_status({'Authorization': 'Bearer platform-secret'})
        return result, rpc, opened

    def test_check_uses_both_gateway_headers_and_does_not_send(self):
        result, rpc, opened = self.check()
        rpc.assert_called_once_with('EnsureSandBox', {'wake': False}, {'Authorization': 'Bearer platform-secret'})
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'https://private-pod.cursorvm.com/api/getHostStatus')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, b'{}')
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers['authorization'], 'Bearer gateway-secret')
        self.assertEqual(headers['x-anyrun-network-token'], 'network-secret')
        self.assertNotIn('platform-secret', str(headers))
        self.assertEqual(result, {'status': 'ok', 'httpStatus': 200, 'hostVersion': '7f4798a',
                                  'capabilities': ['orderedReplicasV1', 'sendAcceptanceV1'],
                                  'nativeSendVerified': False})

    def test_success_output_does_not_include_secrets_or_private_ids(self):
        self.host['capabilities'].extend(['private-tenant', 'gateway-secret', {'token': 'network-secret'}])
        result, _, _ = self.check()
        output = json.dumps(result)
        for value in ('gateway-secret', 'network-secret', 'platform-secret', 'response-secret',
                      'private-tenant', 'private-pod', 'cursorvm.com'):
            self.assertNotIn(value, output)

    def test_base_path_and_explicit_tls_port_are_preserved(self):
        self.assertEqual(routing.gateway_status_url('https://gw--pod.cursorvm.com:443/gateway/'),
                         'https://gw--pod.cursorvm.com:443/gateway/api/getHostStatus')

    def test_untrusted_or_malformed_urls_never_receive_credentials(self):
        urls = [None, '', 'http://pod.cursorvm.com', 'https://evil.com',
                'https://cursorvm.com.evil.com', 'https://evilcursorvm.com',
                'https://user:password@pod.cursorvm.com', 'https://pod.cursorvm.com:80',
                'https://pod.cursorvm.com:invalid', 'https://pod.cursorvm.com:',
                'https://pod.cursorvm.com:0443', 'https://pod.cursorvm.com?secret=token',
                'https://pod.cursorvm.com?', 'https://pod.cursorvm.com#fragment',
                'https://pod.cursorvm.com#', 'https://pod.cursorvm.com\n.evil.com',
                'https://pod.cursorvm.com\\@evil.com', 'https://pod.cursorvm.com/%2e%2e',
                'https://pod.cursorvm.com/../secret', 'https://pod..cursorvm.com',
                'https://pód.cursorvm.com', 'https://[::1]', '//pod.cursorvm.com']
        for url in urls:
            with self.subTest(url=url):
                result, _, opened = self.check(ensured={**self.ensured, 'gatewayUrl': url})
                self.assertEqual(result['error'], 'invalid-gateway-url')
                opened.assert_not_called()

    def test_missing_or_header_injection_credentials_never_call_gateway(self):
        for name in ('gatewayToken', 'networkToken'):
            for value in (None, '', 123, '\r\nAuthorization: bad', 'contains space', 'non-ascii-ß'):
                with self.subTest(name=name, value=value):
                    result, _, opened = self.check(ensured={**self.ensured, name: value})
                    self.assertEqual(result['error'], 'missing-or-invalid-gateway-credentials')
                    opened.assert_not_called()

    def test_ensure_http_errors_do_not_echo_the_response(self):
        result, _, opened = self.check(ensured={'http': 403, 'error': 'gateway-secret private-tenant'})
        self.assertEqual(result, {'status': 'error', 'nativeSendVerified': False,
                                  'error': 'ensure-http-error', 'httpStatus': 403})
        opened.assert_not_called()

    def test_invalid_ensure_response_does_not_contact_gateway(self):
        result, _, opened = self.check(ensured=['gateway-secret'])
        self.assertEqual(result['error'], 'invalid-ensure-response')
        opened.assert_not_called()

    def test_ensure_transport_errors_do_not_echo_exception(self):
        with mock.patch.object(routing, 'rpc', side_effect=urllib.error.URLError('network-secret private-tenant')):
            result = routing.public_gateway_status({})
        self.assertEqual(result['error'], 'ensure-request-failed')
        self.assertNotIn('secret', json.dumps(result))

    def test_gateway_redirect_and_http_error_bodies_are_not_printed(self):
        for code in (301, 302, 303, 307, 308, 401, 404, 503):
            with self.subTest(code=code):
                error = urllib.error.HTTPError('https://private-pod.cursorvm.com', code,
                                               'private-tenant gateway-secret', {}, io.BytesIO(b'network-secret'))
                result, _, _ = self.check(error=error)
                self.assertEqual(result['error'], 'gateway-redirect-refused' if code < 400 else 'gateway-http-error')
                self.assertEqual(result['httpStatus'], code)
                self.assertNotIn('secret', json.dumps(result))

    def test_gateway_transport_errors_are_sanitized(self):
        result, _, _ = self.check(error=urllib.error.URLError('network-secret private-tenant'))
        self.assertEqual(result['error'], 'gateway-transport-error')
        self.assertNotIn('secret', json.dumps(result))

    def test_invalid_gateway_bodies_are_rejected_without_echo(self):
        for raw in (b'<html>network-secret</html>', b'\xff', b'x' * (1024 * 1024 + 1)):
            with self.subTest(size=len(raw)):
                with mock.patch.object(routing, 'rpc', return_value=self.ensured), \
                        mock.patch.object(routing, 'open_no_redirect', return_value=io.BytesIO(raw)):
                    result = routing.public_gateway_status({})
                self.assertEqual(result['error'], 'invalid-gateway-response')
                self.assertNotIn('secret', json.dumps(result))

    def test_invalid_host_status_shape_or_identifier_is_rejected(self):
        for status in ([], {}, {'capabilities': {}}, {'capabilities': [], 'hostVersion': 'private-tenant'},
                       {'capabilities': [], 'hostVersion': 123}):
            with self.subTest(status=status):
                result, _, _ = self.check(host=status)
                self.assertEqual(result['error'], 'invalid-gateway-response')

    def test_null_and_semantic_versions_are_valid(self):
        for version in (None, '0.65.0', '0.65.0-pre.6'):
            with self.subTest(version=version):
                result, _, _ = self.check(host={**self.host, 'hostVersion': version})
                self.assertEqual(result['status'], 'ok')
                self.assertEqual(result['hostVersion'], version)

    def test_public_cli_outputs_only_sanitized_report(self):
        report = {'status': 'ok', 'httpStatus': 200, 'hostVersion': '7f4798a',
                  'capabilities': ['sendAcceptanceV1'], 'nativeSendVerified': False}
        with mock.patch.object(routing, 'headers', return_value={'Authorization': 'Bearer platform-secret'}), \
                mock.patch.object(routing, 'public_gateway_status', return_value=report) as checked, \
                mock.patch.object(routing, 'rpc') as rpc, contextlib.redirect_stdout(io.StringIO()) as output:
            exit_code = routing.main(['--public-gateway'])
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), report)
        checked.assert_called_once_with({'Authorization': 'Bearer platform-secret'})
        rpc.assert_not_called()

    def test_public_cli_returns_failure_exit_code(self):
        with mock.patch.object(routing, 'headers', return_value={}), \
                mock.patch.object(routing, 'public_gateway_status', return_value={'status': 'error'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(routing.main(['--public-gateway']), 1)

    def test_authentication_errors_do_not_expose_exception_or_credentials(self):
        with mock.patch.object(routing, 'headers', side_effect=RuntimeError('network-secret')), \
                mock.patch.object(routing, 'rpc') as rpc, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(routing.main(['--public-gateway']), 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'error', 'error': 'authentication-unavailable'})
        rpc.assert_not_called()

    def test_default_cli_only_calls_original_three_read_only_methods(self):
        responses = [ {'agents': [{'harness': 'box', 'agentId': 'private-agent-id'}]},
                      {'capabilities': {'temporalCreationEnabled': True, 'gatewayToken': 'gateway-secret'}},
                      {'state': 'RUNNING', 'tenantId': 'private-tenant', 'egressTunnelOwnership': True}]
        headers = {'x-sand-box-namespace': 'prod', 'x-cursor-client-version': '0.65.0'}
        with mock.patch.object(routing, 'headers', return_value=headers), \
                mock.patch.object(routing, 'rpc', side_effect=responses) as rpc, \
                mock.patch.object(routing, 'public_gateway_status') as checked, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(routing.main([]), 0)
        self.assertEqual(rpc.call_args_list, [mock.call('ListGrokBotAgents', {}, headers),
                                            mock.call('GetGrokBotRuntimeCapabilities', {}, headers),
                                            mock.call('GetSandBoxRunState', {}, headers)])
        checked.assert_not_called()
        self.assertNotIn('private-', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertIn('RUNNING', output.getvalue())


class RedirectProtectionTests(unittest.TestCase):
    def test_authenticated_request_does_not_follow_any_http_redirect(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers.get('Authorization'), self.headers.get('x-anyrun-network-token')))
                self.send_response(int(self.path.strip('/')))
                self.send_header('Location', '/target')
                self.end_headers()

            def do_GET(self):
                received.append((self.path, self.headers.get('Authorization'), self.headers.get('x-anyrun-network-token')))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for code in (301, 302, 303, 307, 308):
                request = urllib.request.Request('http://127.0.0.1:%d/%d' % (server.server_port, code),
                                                 data=b'{}', headers={'Authorization': 'Bearer gateway-secret',
                                                                     'x-anyrun-network-token': 'network-secret'})
                with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as raised:
                    routing.open_no_redirect(request, timeout=2)
                self.assertEqual(raised.exception.code, code)
                raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual([path for path, _, _ in received], ['/301', '/302', '/303', '/307', '/308'])


class EgressProxyTests(unittest.TestCase):
    def test_provided_loopback_addresses_are_normalized(self):
        for address in ('127.0.0.1:8791', 'http://127.0.0.1:8791', 'http://localhost:8791/',
                        '[::1]:8791', 'http://[::1]:8791'):
            with self.subTest(address=address):
                self.assertEqual(routing.egress_proxy_url(address),
                                 'http://' + address.removeprefix('http://').rstrip('/'))

    def test_external_credentialed_malformed_and_non_http_proxies_are_refused(self):
        for address in ('', 'https://127.0.0.1:8791', 'http://evil.invalid:8791',
                        'http://127.0.0.1.evil.invalid:8791', 'http://0.0.0.0:8791',
                        'http://127.0.0.1', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
                        'http://user:secret@127.0.0.1:8791', 'http://127.0.0.1:8791/path',
                        'http://127.0.0.1:8791?query', 'http://127.0.0.1:8791#fragment',
                        'http://127.0.0.1:8791\\evil', ' http://127.0.0.1:8791',
                        'http://127.0.0.1:8791\n'):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, '^invalid-egress-proxy$'):
                routing.egress_proxy_url(address)

    def call(self, proxies, address):
        request = urllib.request.Request('https://api2.cursor.sh/health')
        with mock.patch.object(routing.urllib.request, 'getproxies', return_value=proxies), \
                mock.patch.dict(os.environ, {'SAND_EGRESS_TUNNEL_PROXY_ADDR': address}), \
                mock.patch.object(routing.urllib.request, 'build_opener') as opened:
            routing.open_no_redirect(request, timeout=7)
        args = opened.call_args.args
        self.assertIsInstance(args[0], urllib.request.ProxyHandler)
        self.assertIsInstance(args[1], routing.NoRedirectHandler)
        opened.return_value.open.assert_called_once_with(request, timeout=7)
        return args[0].proxies

    def test_https_uses_provided_proxy_when_no_operator_proxy_is_set(self):
        self.assertEqual(self.call({}, '127.0.0.1:8791'), {'https': 'http://127.0.0.1:8791'})

    def test_existing_operator_proxy_wins_without_reading_invalid_fallback(self):
        for scheme in ('http', 'https', 'all'):
            proxies = {scheme: 'http://operator-proxy.invalid:3128', 'no': 'localhost'}
            self.assertEqual(self.call(proxies, 'invalid-secret-proxy'), proxies)

    def test_no_proxy_exclusions_survive_fallback(self):
        self.assertEqual(self.call({'no': 'localhost,.internal'}, '127.0.0.1:8791'),
                         {'no': 'localhost,.internal', 'https': 'http://127.0.0.1:8791'})

    def test_absent_provided_proxy_keeps_direct_behavior(self):
        self.assertEqual(self.call({}, ''), {})


if __name__ == '__main__':
    unittest.main()
