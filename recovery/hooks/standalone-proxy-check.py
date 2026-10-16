"""Real loopback TLS/HTTP proxy tests with synthetic platform/native services.

Run with the optional pinned mitmproxy dependency. No public send, real account,
phone configuration or production listener is used. These tests do not prove
installed iOS/Android trust, delivery, or twelve real bots under load.
"""
import asyncio
import concurrent.futures
import email.message
from http import client as http_client
import importlib.util
import json
import pathlib
import ssl
import tempfile
import threading
import types
import unittest
import uuid

from mitmproxy import exceptions, http, options
from mitmproxy.tools.dump import DumpMaster

spec = importlib.util.spec_from_file_location('bridge', pathlib.Path(__file__).with_name('standalone-dispatch-addon.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
AGENT = '00000000-0000-4000-8000-82be455ee571'
WIRE = '1234'


class Response:
    status = 200
    def __init__(self, body):
        self.raw = (' ' + json.dumps(body) + '\n').encode()
        self.headers = email.message.Message()
        self.headers.add_header('content-type', 'application/json')
    def read(self, limit):
        return self.raw[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.receipts = {}
        self.native_sends = 0
        self.platform_sends = 0
        self.native_token_seen = []
        self.caller_seen = []
        self.harness = 'box'
        self.owned = True
        self.failure = 'send/agent-unavailable'
        self.lose_receipt = False
        self.bad_wire = False
        self.send_entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
    def response(self, request):
        body = json.loads(request.data)
        method = request.full_url.rsplit('/', 1)[1]
        authorization = request.get_header('Authorization')
        if request.full_url.startswith(m.transport_module.PLATFORM):
            self.caller_seen.append(authorization)
            assert authorization == 'Bearer synthetic-caller'
            if method == 'ListGrokBotAgents':
                return Response({'agents': [{'id': WIRE, 'agentId': AGENT, 'legacyAgentId': AGENT,
                    'harness': self.harness, 'viewerIsOwner': self.owned}]})
            if method == 'SendGrokBotUserMessage':
                with self.lock:
                    self.platform_sends += 1
                return Response({'dispatched': False, 'delivery': m.core.DELIVERY + 'REFUSED',
                                 'refusal': {'failureCode': self.failure, 'message': 'Unavailable'}})
            return Response({'status': m.core.STATUS + 'NOT_FOUND'})
        assert request.full_url.startswith(m.transport_module.GATEWAY)
        self.native_token_seen.append(authorization)
        assert authorization == 'Bearer synthetic-native'
        if method == 'listAgents':
            return Response([{'id': AGENT}])
        if method == 'promptAcceptanceStatus':
            with self.lock:
                record = self.receipts.get(body['clientNonce'])
            return Response({'outcome': 'found', 'record': record} if record else {'outcome': 'not-found'})
        if method == 'sendPrompt':
            with self.lock:
                self.native_sends += 1
            self.send_entered.set()
            assert self.release.wait(8), 'Synthetic native dispatch was not released'
            if not self.lose_receipt:
                with self.lock:
                    self.receipts[body['clientNonce']] = {'accountSlot': 'host', 'agentId': AGENT,
                        'clientNonce': body['clientNonce'], 'inputDigest': m.core.input_digest(body),
                        'status': 'accepted', 'acceptedAtMs': 123456789, 'echoEntryId': 'native-echo'}
            return Response({'accepted': True})
        raise AssertionError('Unvalidated destination')


class Opener:
    def __init__(self, state):
        self.state = state
    def open(self, request, timeout):
        return self.state.response(request)


class ProxyChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.sand = self.root / 'sand-data'
        self.sand.mkdir(mode=0o700)
        (self.sand / 'openbot-mode').write_text('custom')
        (self.sand / 'openbot-local-only.enable').write_text('')
        (self.sand / 'gateway.json').write_text('{"token":"synthetic-native"}')
        (self.sand / 'gateway.json').chmod(0o600)
        self.cert_dir = self.root / 'proxy-ca'
        self.cert_dir.mkdir(mode=0o700)
        self.state = State()
        def factory(root, method, body, raw):
            transport = m.transport_module.Transport(root, method, body, raw,
                platform_opener=Opener(self.state), gateway_opener=Opener(self.state))
            if self.state.bad_wire:
                wire = transport.wire
                transport.wire = lambda result: wire(result)._replace(headers=(('x-bad', '\u2603'),))
            return transport
        self.bridge = m.Bridge(factory)
        self.master = DumpMaster(options.Options(listen_host='127.0.0.1', listen_port=0,
            confdir=str(self.cert_dir)), with_termlog=False)
        self.master.addons.add(self.bridge)
        self.master.options.update(connection_strategy='lazy', upstream_cert=False,
            flow_detail=0, openbot_standalone_enabled=True, openbot_sand_data=str(self.sand))
        self.runner = asyncio.create_task(self.master.run())
        for _ in range(200):
            addresses = self.master.addons.get('proxyserver').listen_addrs()
            if addresses:
                self.port = addresses[0][1]
                break
            if self.runner.done():
                await self.runner
                raise AssertionError('Proxy failed to start')
            await asyncio.sleep(0.01)
        else:
            raise AssertionError('Loopback proxy did not start')
        self.context = ssl.create_default_context(cafile=str(self.cert_dir / 'mitmproxy-ca-cert.pem'))

    async def asyncTearDown(self):
        self.state.release.set()
        if self.bridge.tasks:
            await asyncio.gather(*self.bridge.tasks, return_exceptions=True)
        await self.master.addons.get('proxyserver').servers.update(())
        self.master.shutdown()
        await self.runner
        self.directory.cleanup()

    def body(self, nonce=None):
        return {'agentId': WIRE, 'messageId': nonce or str(uuid.uuid4()), 'text': 'TLS probe café',
            'sentAtMs': '123456789', 'isFork': False, 'source': 'GROK_BOT_CLIENT_SURFACE_MOBILE',
            'attachmentPaths': [], 'attachmentNames': [], 'onePasswordAppInstalled': True,
            'initiator': 'GROK_BOT_USER_MESSAGE_INITIATOR_USER'}

    def client(self, method, body, context=None, host=m.HOST):
        # Close explicitly even when CONNECT/TLS fails; no environment proxy
        # rules or network allowlists can bypass this loopback connection.
        connection = http_client.HTTPSConnection('127.0.0.1', self.port,
            context=context or self.context, timeout=10)
        connection.set_tunnel(host, 443)
        try:
            connection.request('POST', m.PREFIX + method, json.dumps(body).encode(),
                {'host': host, 'authorization': 'Bearer synthetic-caller',
                 'content-type': 'application/json', 'x-cursor-client-source': 'sand-mobile'})
            with connection.getresponse() as response:
                return response.status, response.read()
        finally:
            connection.close()

    async def send(self, body):
        status, raw = await asyncio.to_thread(self.client, 'SendGrokBotUserMessage', body)
        return status, json.loads(raw)

    async def test_tls_native_acceptance_retry_and_status(self):
        body = self.body()
        status, sent = await self.send(body)
        self.assertEqual((status, sent['delivery']), (200, m.core.DELIVERY + 'ACCEPTED_BOX'))
        _, retry = await self.send(body)
        self.assertEqual(retry['delivery'], m.core.DELIVERY + 'DUPLICATE')
        status, raw = await asyncio.to_thread(self.client, 'GetGrokBotSendStatus',
            {'agentId': WIRE, 'messageId': body['messageId']})
        self.assertEqual((status, json.loads(raw)), (200, {'status': m.core.STATUS + 'ACCEPTED',
            'acceptedAtMs': '123456789', 'echoEntryId': 'native-echo'}))
        self.assertEqual(self.state.native_sends, 1)
        self.assertEqual(self.state.platform_sends, 1)
        self.assertEqual(set(self.state.caller_seen), {'Bearer synthetic-caller'})
        self.assertEqual(set(self.state.native_token_seen), {'Bearer synthetic-native'})

    async def test_tls_twelve_simultaneous_retries_dispatch_once(self):
        body = self.body()
        results = await asyncio.gather(*(self.send(body) for _ in range(12)))
        self.assertTrue(any(result[1].get('delivery') == m.core.DELIVERY + 'ACCEPTED_BOX' for result in results))
        self.assertEqual(self.state.native_sends, 1)
        _, retry = await self.send(body)
        self.assertEqual(retry['delivery'], m.core.DELIVERY + 'DUPLICATE')

    async def test_client_default_executor_cannot_starve_dispatch(self):
        # A small CI machine has fewer default executor threads than twelve
        # clients. Blocking clients must not share dispatch's worker capacity.
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=2))
        body = self.body()
        results = await asyncio.gather(*(self.send(body) for _ in range(12)))
        self.assertTrue(any(result[1].get('delivery') == m.core.DELIVERY + 'ACCEPTED_BOX'
                            for result in results))
        self.assertEqual(self.state.native_sends, 1)
        # Concurrent clients may reach the refused platform send before the
        # durable native claim exists. Native admission must remain singular;
        # this test checks executor starvation, not singleflight upstream I/O.
        self.assertGreaterEqual(self.state.platform_sends, 1)
        self.assertLessEqual(self.state.platform_sends, 12)

    async def test_uncertain_native_dispatch_does_not_replay(self):
        self.state.lose_receipt = True
        body = self.body()
        self.assertEqual((await self.send(body))[0], 503)
        self.assertEqual((await self.send(body))[0], 503)
        status, raw = await asyncio.to_thread(self.client, 'GetGrokBotSendStatus',
            {'agentId': WIRE, 'messageId': body['messageId']})
        self.assertEqual((status, json.loads(raw)['status']), (200, m.core.STATUS + 'UNKNOWN_DURABILITY'))
        self.assertEqual(self.state.native_sends, 1)
        self.assertEqual(self.state.platform_sends, 1)

    async def test_original_quota_failure_is_preserved(self):
        self.state.failure = 'send/quota-exhausted'
        status, raw = await asyncio.to_thread(self.client, 'SendGrokBotUserMessage', self.body())
        expected = Response({'dispatched': False, 'delivery': m.core.DELIVERY + 'REFUSED',
            'refusal': {'failureCode': self.state.failure, 'message': 'Unavailable'}}).raw
        self.assertEqual((status, raw), (200, expected))
        self.assertEqual(self.state.native_sends, 0)

    async def test_temporal_and_unowned_rows_never_hand_off(self):
        self.state.harness = 'temporal'
        _, result = await self.send(self.body())
        self.assertEqual(result['delivery'], m.core.DELIVERY + 'REFUSED')
        self.state.harness, self.state.owned = 'box', False
        _, result = await self.send(self.body())
        self.assertEqual(result['delivery'], m.core.DELIVERY + 'REFUSED')
        self.assertEqual(self.state.native_sends, 0)
        self.assertEqual(self.state.platform_sends, 0)

    async def test_pause_blocks_new_handoff(self):
        (self.sand / 'openbot-pause.json').write_text('{"paused":true}')
        _, result = await self.send(self.body())
        self.assertEqual(result['delivery'], m.core.DELIVERY + 'REFUSED')
        self.assertEqual(self.state.native_sends, 0)
        self.assertEqual(self.state.platform_sends, 0)

    async def test_unsupported_send_encoding_never_enters_platform(self):
        flow = types.SimpleNamespace(request=http.Request.make('POST',
            'https://' + m.HOST + m.PREFIX + 'SendGrokBotUserMessage', b'proto',
            {'authorization': 'Bearer synthetic-caller', 'content-type': 'application/proto'}), response=None)
        await self.bridge.request(flow)
        self.assertEqual(flow.response.status_code, 415)
        self.assertEqual(self.state.platform_sends, 0)
        self.assertEqual(self.state.native_sends, 0)

    async def test_client_without_trusted_ca_is_rejected(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await asyncio.to_thread(self.client, 'SendGrokBotUserMessage', self.body(), ssl.create_default_context())
        self.assertEqual(self.state.native_sends, 0)
        self.assertEqual(self.state.platform_sends, 0)

    async def test_foreign_connect_destination_is_rejected(self):
        with self.assertRaises(OSError):
            await asyncio.to_thread(self.client, 'SendGrokBotUserMessage', self.body(), None, 'attacker.invalid')
        self.assertEqual(self.state.native_sends, 0)

    async def test_cancelled_client_leaves_inflight_worker_counted(self):
        self.state.release.clear()
        body = self.body()
        flow = types.SimpleNamespace(request=http.Request.make('POST',
            'https://' + m.HOST + m.PREFIX + 'SendGrokBotUserMessage', json.dumps(body).encode(),
            {'authorization': 'Bearer synthetic-caller', 'content-type': 'application/json'}), response=None)
        handler = asyncio.create_task(self.bridge.request(flow))
        for _ in range(100):
            if self.state.send_entered.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.state.send_entered.is_set())
        handler.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await handler
        self.assertEqual(len(self.bridge.tasks), 1)
        self.state.release.set()
        await asyncio.gather(*self.bridge.tasks)
        await asyncio.sleep(0)
        self.assertEqual(len(self.bridge.tasks), 0)
        _, retry = await self.send(body)
        self.assertEqual(retry['delivery'], m.core.DELIVERY + 'DUPLICATE')
        self.assertEqual(self.state.native_sends, 1)

    async def test_shutdown_finishes_already_admitted_receipt(self):
        self.state.release.clear()
        body = self.body()
        flow = types.SimpleNamespace(request=http.Request.make('POST',
            'https://' + m.HOST + m.PREFIX + 'SendGrokBotUserMessage', json.dumps(body).encode(),
            {'authorization': 'Bearer synthetic-caller', 'content-type': 'application/json'}), response=None)
        handler = asyncio.create_task(self.bridge.request(flow))
        for _ in range(100):
            if self.state.send_entered.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.state.send_entered.is_set())
        self.bridge.done()
        self.assertFalse(self.bridge.enabled)
        self.assertIsNone(self.bridge.executor)
        self.assertEqual(len(self.bridge.tasks), 1)
        self.state.release.set()
        await handler
        self.assertEqual(json.loads(flow.response.raw_content)['delivery'],
                         m.core.DELIVERY + 'ACCEPTED_BOX')
        receipt = self.bridge.receipts.lookup(body['messageId'])
        self.assertEqual(receipt['status'], 'accepted')
        self.assertEqual(receipt['accepted_at_ms'], 123456789)
        self.assertEqual(self.state.native_sends, 1)

    async def test_response_encoding_error_never_falls_through_after_native_send(self):
        self.state.bad_wire = True
        body = self.body()
        self.assertEqual((await self.send(body))[0], 503)
        self.assertEqual(self.state.native_sends, 1)
        self.assertEqual(self.state.platform_sends, 1)
        self.state.bad_wire = False
        _, retry = await self.send(body)
        self.assertEqual(retry['delivery'], m.core.DELIVERY + 'DUPLICATE')
        self.assertEqual(self.state.native_sends, 1)
        self.assertEqual(self.state.platform_sends, 1)


class OptionsChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.values = dict(listen_host='127.0.0.1', mode=['regular'], ssl_insecure=False,
            save_stream_file=None, flow_detail=0, stickyauth=None, stickycookie=None,
            confdir=self.directory.name)

    def test_default_import_is_inactive(self):
        self.assertFalse(m.addons[0].enabled)
        self.assertIsNone(m.addons[0].receipts)
        self.assertIsNone(m.addons[0].executor)

    def test_private_options_and_bind_addresses(self):
        for host in ('127.0.0.1', '::1', '192.168.1.1', '100.64.0.3', 'fd7a:115c:a1e0::1'):
            m.validate_options(types.SimpleNamespace(**{**self.values, 'listen_host': host}))

    def test_public_binds_tls_bypass_capture_and_sticky_auth_are_rejected(self):
        for change in ({'listen_host': '0.0.0.0'}, {'listen_host': '8.8.8.8'},
                       {'listen_host': '::'}, {'ssl_insecure': True}, {'save_stream_file': 'flows'},
                       {'flow_detail': 4}, {'stickyauth': '.*'}, {'stickycookie': '.*'},
                       {'mode': ['transparent']}):
            with self.subTest(change=change), self.assertRaises(exceptions.OptionsError):
                m.validate_options(types.SimpleNamespace(**{**self.values, **change}))

    def test_public_config_directory_is_rejected(self):
        pathlib.Path(self.directory.name).chmod(0o755)
        with self.assertRaises(exceptions.OptionsError):
            m.validate_options(types.SimpleNamespace(**self.values))


if __name__ == '__main__':
    unittest.main(verbosity=2)
