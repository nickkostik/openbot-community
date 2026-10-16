import email.message
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error


spec = importlib.util.spec_from_file_location('transport', pathlib.Path(__file__).with_name('standalone-transport.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Response:
    def __init__(self, status=200, raw=b'{}', headers=(('content-type', 'application/json'),)):
        self.status, self.raw = status, raw
        self.headers = email.message.Message()
        for name, value in headers:
            self.headers.add_header(name, value)
    def read(self, limit):
        return self.raw[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error = response or Response(), error
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class TestTransport(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.write('gateway.json', json.dumps({'token': 'native-test-token'}), private=True)
        self.write('openbot-local-only.enable', '')
        self.write('openbot-mode', 'custom')
        self.body = {'agentId': '123', 'messageId': '00000000-0000-4000-8000-189e1abe3f33', 'text': 'hi'}
        self.raw = b'{ "agentId":"123", "messageId":"00000000-0000-4000-8000-189e1abe3f33", "text":"hi" }'
        self.headers = (('Authorization', 'Bearer caller-test-token'), ('X-Cursor-Client-Source', 'sand-mobile'),
                        ('X-Cursor-Client-App-Version', '1.14.0'), ('Host', 'attacker.invalid'),
                        ('Proxy-Authorization', 'never-forward'), ('Content-Length', '999'))
    def tearDown(self):
        self.directory.cleanup()
    def write(self, name, value, private=False):
        path = self.root / name
        path.write_text(value)
        path.chmod(0o600 if private else 0o644)
    def transport(self, platform=None, gateway=None):
        return m.Transport(self.root, 'SendGrokBotUserMessage', self.body, self.raw,
                           platform or Opener(), gateway or Opener())

    def test_original_send_bytes_and_caller_headers_are_preserved(self):
        opener = Opener()
        transport = self.transport(platform=opener)
        transport.platform('SendGrokBotUserMessage', self.body, self.headers)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, m.PLATFORM + 'SendGrokBotUserMessage')
        self.assertEqual(request.data, self.raw)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers['authorization'], 'Bearer caller-test-token')
        self.assertEqual(headers['x-cursor-client-app-version'], '1.14.0')
        self.assertNotIn('host', headers)
        self.assertNotIn('proxy-authorization', headers)
        self.assertNotIn('native-test-token', str(headers))
        self.assertEqual(headers['accept-encoding'], 'identity')
        self.assertEqual(timeout, 12)

    def test_native_credentials_never_include_caller_credentials(self):
        opener = Opener(Response(raw=b'[]'))
        transport = self.transport(gateway=opener)
        self.assertEqual(transport.gateway('listAgents', {}).body, [])
        request, _ = opener.requests[0]
        self.assertEqual(request.full_url, m.GATEWAY + 'listAgents')
        self.assertEqual(request.get_header('Authorization'), 'Bearer native-test-token')
        self.assertNotIn('caller-test-token', str(request.header_items()))
        self.assertNotIn('X-cursor-client-source', dict(request.header_items()))

    def test_platform_destination_cannot_be_overridden_by_method(self):
        opener = Opener()
        for method in ('https://attacker.invalid', '../SendGrokBotUserMessage', 'sendPrompt'):
            with self.assertRaises(ValueError):
                self.transport(platform=opener).platform(method, {}, self.headers)
        self.assertEqual(opener.requests, [])

    def test_native_destination_cannot_be_overridden_by_method(self):
        opener = Opener()
        for method in ('ListGrokBotAgents', '../sendPrompt', 'http://attacker.invalid'):
            with self.assertRaises(ValueError):
                self.transport(gateway=opener).gateway(method, {})
        self.assertEqual(opener.requests, [])

    def test_platform_redirect_is_not_replayed(self):
        error = urllib.error.HTTPError(m.PLATFORM, 302, 'Found',
                                      {'Location': 'https://attacker.invalid'}, None)
        error.read = lambda _limit: b'redirect'
        opener = Opener(error=error)
        result = self.transport(platform=opener).platform('SendGrokBotUserMessage', self.body, self.headers)
        self.assertEqual(result.status, 302)
        self.assertEqual(len(opener.requests), 1)
        self.assertIsNone(m.NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://attacker.invalid'))

    def test_transport_error_does_not_retry(self):
        opener = Opener(error=urllib.error.URLError('connection lost'))
        with self.assertRaises(urllib.error.URLError):
            self.transport(platform=opener).platform('SendGrokBotUserMessage', self.body, self.headers)
        self.assertEqual(len(opener.requests), 1)

    def test_oversized_response_is_rejected(self):
        opener = Opener(Response(raw=b'x' * (m.MAX_RESPONSE + 1)))
        with self.assertRaises(ValueError):
            self.transport(platform=opener).platform('ListGrokBotAgents', {}, self.headers)

    def test_original_non_json_error_is_forwarded_byte_for_byte(self):
        raw = b'<html>upstream error</html>'
        response = Response(502, raw, (('Content-Type', 'text/html'), ('Connection', 'x-private'),
                                      ('X-Private', 'remove'), ('X-Request-Id', 'keep'), ('Content-Length', '0')))
        transport = self.transport(platform=Opener(response))
        result = transport.platform('SendGrokBotUserMessage', self.body, self.headers)
        transport.platform('ListGrokBotAgents', {}, self.headers)
        wire = transport.wire(result)
        self.assertEqual((wire.status, wire.payload), (502, raw))
        self.assertEqual(wire.headers, (('Content-Type', 'text/html'), ('X-Request-Id', 'keep')))

    def test_new_receipt_response_is_json_and_not_cacheable(self):
        wire = self.transport().wire(m.core.Result(200, {'delivery': 'accepted'}))
        self.assertEqual(json.loads(wire.payload), {'delivery': 'accepted'})
        self.assertEqual(dict(wire.headers)['cache-control'], 'no-store')

    def test_current_native_token_is_loaded_for_each_call(self):
        opener = Opener()
        transport = self.transport(gateway=opener)
        transport.gateway('promptAcceptanceStatus', {})
        self.write('gateway.json', '{"token":"rotated-native-token"}', private=True)
        transport.gateway('promptAcceptanceStatus', {})
        self.assertEqual(opener.requests[0][0].get_header('Authorization'), 'Bearer native-test-token')
        self.assertEqual(opener.requests[1][0].get_header('Authorization'), 'Bearer rotated-native-token')

    def test_public_native_credential_file_is_refused(self):
        (self.root / 'gateway.json').chmod(0o644)
        with self.assertRaises(ValueError):
            self.transport().gateway('listAgents', {})

    def test_symlink_native_credentials_are_refused(self):
        original = self.root / 'gateway.json'
        original.rename(self.root / 'actual.json')
        original.symlink_to(self.root / 'actual.json')
        with self.assertRaises(OSError):
            self.transport().gateway('listAgents', {})

    def test_invalid_native_token_is_refused_without_request(self):
        for token in ('', '  ', 'a\r\nb'):
            self.write('gateway.json', json.dumps({'token': token}), private=True)
            opener = Opener()
            with self.assertRaises(ValueError):
                self.transport(gateway=opener).gateway('listAgents', {})
            self.assertEqual(opener.requests, [])

    def test_ambiguous_caller_credentials_are_refused(self):
        for headers in ((('Authorization', 'one'), ('authorization', 'two')),
                        (('Authorization', 'a\r\nb'),), (('X-Cursor-Client-Version', 'one'),),
                        (('Authorization', 'one'), ('x-cursor-\r\n-header', 'two'))):
            with self.assertRaises(ValueError):
                m.caller_headers(headers)

    def test_cookie_auth_is_preserved_only_for_the_platform(self):
        self.assertEqual(m.caller_headers((('Cookie', 'caller-cookie'),))['cookie'], 'caller-cookie')

    def test_json_duplicate_fields_and_nonfinite_numbers_are_rejected(self):
        for raw in (b'{"text":"one","text":"two"}', b'{"a":{"b":1,"b":2}}',
                    b'{"value":NaN}', b'{"value":Infinity}', b'\xff'):
            with self.assertRaises((ValueError, UnicodeError)):
                m.parse_json(raw)
        with self.assertRaises(ValueError):
            m.encode_json({'x': float('nan')})

    def test_oversized_request_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            m.encode_json({'text': 'x' * m.MAX_REQUEST})

    def test_original_wire_and_parsed_message_must_match(self):
        with self.assertRaises(ValueError):
            m.Transport(self.root, 'SendGrokBotUserMessage', self.body, b'{"text":"different"}')

    def test_fifo_native_credentials_are_rejected_without_blocking(self):
        (self.root / 'gateway.json').unlink()
        os.mkfifo(self.root / 'gateway.json', 0o600)
        with self.assertRaises(ValueError):
            self.transport().gateway('listAgents', {})

    def test_local_work_requires_owned_marker_and_known_mode(self):
        self.assertTrue(m.local_work_allowed(self.root))
        self.write('openbot-mode', 'official')
        self.assertTrue(m.local_work_allowed(self.root))
        self.write('openbot-mode', 'invalid')
        self.assertFalse(m.local_work_allowed(self.root))
        self.write('openbot-mode', 'custom')
        (self.root / 'openbot-local-only.enable').unlink()
        self.assertFalse(m.local_work_allowed(self.root))

    def test_global_pause_and_malformed_pause_block_new_local_work(self):
        for value in ('{"paused":true}', '{"paused":"false"}', '{', 'null', '{}'):
            self.write('openbot-pause.json', value)
            self.assertFalse(m.local_work_allowed(self.root))
        self.write('openbot-pause.json', '{"paused":false}')
        self.assertTrue(m.local_work_allowed(self.root))

    def test_symlink_marker_does_not_enable_local_work(self):
        (self.root / 'openbot-local-only.enable').unlink()
        (self.root / 'openbot-local-only.enable').symlink_to(self.root / 'openbot-mode')
        self.assertFalse(m.local_work_allowed(self.root))

    def test_writable_root_is_refused(self):
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            self.transport()

    def test_symlink_root_is_refused(self):
        link = self.root / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            m.checked_root(link)


if __name__ == '__main__':
    unittest.main()
