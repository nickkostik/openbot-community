"""Optional mitmproxy adapter. Disabled by default; never loaded by box startup.

The original app and its HTTPS hostname stay intact. Device trust/routing need
separate authorization and real-device verification. This addon creates no CA,
configures no device, writes no flow capture, and starts no listener itself.
"""
import asyncio
import concurrent.futures
from http import client as http_client
import importlib.util
import ipaddress
import logging
import pathlib
import sqlite3
import ssl

from mitmproxy import ctx, exceptions, http

spec = importlib.util.spec_from_file_location('standalone_transport',
    pathlib.Path(__file__).with_name('standalone-transport.py'))
transport_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport_module)
core = transport_module.core
HOST = 'api2.cursor.sh'
PREFIX = '/aiserver.v1.GrokBotService/'
METHODS = frozenset(('SendGrokBotUserMessage', 'GetGrokBotSendStatus'))
PRIVATE_NETS = tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12',
    '192.168.0.0/16', '100.64.0.0/10', 'fc00::/7'))


def validate_options(options):
    try:
        address = ipaddress.ip_address(options.listen_host)
        if not any(address in network for network in PRIVATE_NETS):
            raise ValueError('listener-must-use-an-explicit-private-address')
        if list(options.mode) != ['regular'] or options.ssl_insecure is not False or \
                options.save_stream_file or options.flow_detail != 0 or \
                options.stickyauth or options.stickycookie:
            raise ValueError('proxy-requires-verified-tls-and-no-flow-capture-or-sticky-credentials')
        folder = transport_module.checked_root(pathlib.Path(options.confdir).expanduser())
        if folder.stat().st_mode & 0o077:
            raise ValueError('proxy-config-directory-must-be-private')
    except (OSError, ValueError, AttributeError) as error:
        raise exceptions.OptionsError('Unsafe standalone proxy options') from error


class Bridge:
    def __init__(self, transport_factory=transport_module.Transport):
        self.transport_factory = transport_factory
        self.enabled = False
        self.root = None
        self.receipts = None
        self.tasks = set()
        self.executor = None

    def load(self, loader):
        loader.add_option('openbot_standalone_enabled', bool, False,
            'Explicitly enable the optional standalone dispatch adapter')
        loader.add_option('openbot_sand_data', str, '',
            'Owned absolute sand-data path for native gateway and durable receipts')

    def configure(self, updates):
        # Recheck global proxy security settings even when our own options did
        # not change. A later TLS/capture option must not weaken the bridge.
        if not ctx.options.openbot_standalone_enabled:
            self.enabled = False
            return
        validate_options(ctx.options)
        try:
            root = transport_module.checked_root(ctx.options.openbot_sand_data)
            if self.tasks and root != self.root:
                raise ValueError('cannot-switch-journal-with-deliveries-in-flight')
            if root != self.root:
                receipts = core.Receipts(root / '.openbot-standalone-dispatch' / 'receipts.sqlite3')
                self.root, self.receipts = root, receipts
            if self.executor is None:
                self.executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=12, thread_name_prefix='openbot-standalone')
        except (OSError, ValueError, sqlite3.Error) as error:
            raise exceptions.OptionsError('Standalone dispatch storage unavailable') from error
        self.enabled = True

    def done(self):
        self.enabled = False
        if self.executor is not None:
            # Let already-admitted workers finish their durable receipt. Do
            # not block the proxy event loop or cancel a native dispatch.
            self.executor.shutdown(wait=False, cancel_futures=False)
            self.executor = None

    def http_connect(self, flow):
        if self.enabled and (flow.request.host != HOST or flow.request.port != 443):
            flow.response = http.Response.make(403, b'Private proxy destination refused')

    def requestheaders(self, flow):
        if not self.enabled:
            return
        if flow.request.host != HOST or flow.request.scheme != 'https' or flow.request.port != 443:
            flow.response = http.Response.make(403, b'Private proxy destination refused')
            return
        method = self.method(flow)
        if method is None:
            return
        lengths = flow.request.headers.get_all('content-length')
        if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or
                        int(lengths[0]) > transport_module.MAX_REQUEST):
            flow.response = http.Response.make(413, b'Standalone request too large')

    def method(self, flow):
        if flow.request.method != 'POST' or not flow.request.path.startswith(PREFIX):
            return None
        value = flow.request.path[len(PREFIX):]
        return value if value in METHODS else None

    def dispatch(self, method, body, raw, headers, root, receipts):
        try:
            transport = self.transport_factory(root, method, body, raw)
            dispatcher = core.Dispatcher(transport, receipts,
                lambda: self.enabled and transport_module.local_work_allowed(root))
            if method == 'SendGrokBotUserMessage':
                # Check the real roster before even trying a platform send.
                # Stale Temporal targets after migration/reset must not consume
                # a cloud turn while this local-only adapter is enabled.
                if not self.enabled or not transport_module.local_work_allowed(root) or \
                        dispatcher.target(body.get('agentId'), headers) is None:
                    result = core.refusal()
                else:
                    result = dispatcher.send(body, headers)
            else:
                result = dispatcher.status(body, headers)
            return transport.wire(result)
        except (OSError, TimeoutError, ValueError, UnicodeError, RecursionError,
                http_client.HTTPException, ssl.SSLError, sqlite3.Error):
            return transport_module.WireResponse(503,
                transport_module.encode_json({'code': 'unavailable',
                    'message': 'Delivery has no confirmed receipt'}),
                (('content-type', 'application/json'), ('cache-control', 'no-store')))

    def finished(self, task):
        self.tasks.discard(task)
        # Retrieve exceptions without printing requests or credentials. A
        # cancelled client doesn't cancel an already-running native handoff.
        if not task.cancelled():
            task.exception()

    def unavailable_response(self):
        return http.Response.make(503,
            b'{"code":"unavailable","message":"Delivery has no confirmed receipt"}',
            {'content-type': 'application/json', 'cache-control': 'no-store'})

    async def request(self, flow):
        if not self.enabled or flow.response is not None or flow.request.host != HOST or \
                flow.request.scheme != 'https' or flow.request.port != 443:
            return
        method = self.method(flow)
        content_type = flow.request.headers.get('content-type', '').split(';')[0].strip().lower()
        if method is None:
            return
        if content_type != 'application/json' or \
                flow.request.headers.get('content-encoding', 'identity').lower() != 'identity':
            if method == 'SendGrokBotUserMessage':
                flow.response = http.Response.make(415, b'Unsupported standalone send encoding')
            return
        raw = flow.request.raw_content
        if raw is None or len(raw) > transport_module.MAX_REQUEST:
            flow.response = http.Response.make(413, b'Standalone request too large')
            return
        try:
            body = transport_module.parse_json(raw)
            headers = tuple(flow.request.headers.items(multi=True))
            transport_module.caller_headers(headers)
            if not isinstance(body, dict):
                raise ValueError('invalid-send-object')
        except (ValueError, UnicodeError, RecursionError):
            if method == 'SendGrokBotUserMessage':
                flow.response = http.Response.make(400, b'Invalid standalone send request')
            return
        if len(self.tasks) >= 12:
            flow.response = http.Response.make(503, b'{"code":"unavailable","message":"Private delivery busy"}',
                {'content-type': 'application/json', 'cache-control': 'no-store'})
            return
        # Reserve a failure response before admission. An addon/rendering bug
        # after native acceptance must never fall through to a second platform
        # send. The status endpoint can recover the durable native receipt.
        flow.response = self.unavailable_response()
        try:
            # Other addons or client-test workers may occupy the event loop's
            # default executor. Dispatch needs its own bounded pool so those
            # callers cannot prevent the work they are waiting for.
            task = asyncio.get_running_loop().run_in_executor(self.executor,
                self.dispatch, method, body, raw, headers, self.root, self.receipts)
            self.tasks.add(task)
            task.add_done_callback(self.finished)
            response = await asyncio.shield(task)
            headers = http.Headers((name.encode('latin-1'), value.encode('latin-1'))
                                   for name, value in response.headers)
            flow.response = http.Response.make(response.status, response.payload, headers)
        except Exception as error:
            logging.error('Private dispatch response failed (%s)', type(error).__name__)
            # Never log the flow or exception text: it may contain credentials.
            flow.response = self.unavailable_response()


addons = [Bridge()]
