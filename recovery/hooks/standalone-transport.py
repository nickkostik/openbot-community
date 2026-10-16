"""Bounded caller-preserving transport for the optional standalone dispatch core.

Construction opens no listener and imports no proxy. Platform destinations and
native gateway destinations are fixed; caller credentials never reach the native
gateway, and native credentials never reach the platform. No retries or redirects.
"""
import importlib.util
import json
import os
import pathlib
import re
import stat
import urllib.error
import urllib.request
from typing import NamedTuple


spec = importlib.util.spec_from_file_location('standalone_dispatch',
    pathlib.Path(__file__).with_name('standalone-dispatch.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
PLATFORM = 'https://api2.cursor.sh/aiserver.v1.GrokBotService/'
GATEWAY = 'http://127.0.0.1:1340/api/'
PLATFORM_METHODS = frozenset(('ListGrokBotAgents', 'SendGrokBotUserMessage', 'GetGrokBotSendStatus'))
GATEWAY_METHODS = frozenset(('listAgents', 'promptAcceptanceStatus', 'sendPrompt'))
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
HOP_HEADERS = frozenset(('connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'))


class WireResponse(NamedTuple):
    status: int
    payload: bytes
    headers: tuple


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def parse_json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE:
        raise ValueError('invalid-json-size')
    def pairs(items):
        output = {}
        for name, value in items:
            if name in output:
                raise ValueError('duplicate-json-field')
            output[name] = value
        return output
    def constant(_):
        raise ValueError('non-finite-json-value')
    return json.loads(raw.decode('utf8'), object_pairs_hook=pairs, parse_constant=constant)


def encode_json(body):
    raw = json.dumps(body, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(raw) > MAX_REQUEST:
        raise ValueError('oversized-request')
    return raw


def owned_bytes(path, private=False, limit=MAX_REQUEST):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        entry = os.fstat(fd)
        if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid() or \
                entry.st_nlink != 1 or entry.st_size > limit or \
                (private and entry.st_mode & 0o077):
            raise ValueError('unsafe-owned-file')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('oversized-owned-file')
        return raw
    finally:
        os.close(fd)


def checked_root(root):
    path = pathlib.Path(root)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('sand-data-must-be-an-absolute-nonsymlink-path')
    entry = path.lstat()
    if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != os.getuid() or entry.st_mode & 0o022:
        raise ValueError('sand-data-must-be-owned-and-not-writable-by-others')
    return path


def local_work_allowed(root):
    try:
        root = checked_root(root)
        owned_bytes(root / 'openbot-local-only.enable', limit=4096)
        mode = owned_bytes(root / 'openbot-mode', limit=4096).decode('utf8').strip()
        if mode not in ('custom', 'official'):
            return False
        try:
            pause = parse_json(owned_bytes(root / 'openbot-pause.json'))
        except FileNotFoundError:
            return True
        return isinstance(pause, dict) and type(pause.get('paused')) is bool and pause['paused'] is False
    except (OSError, ValueError, UnicodeError, RecursionError):
        return False


def caller_headers(items):
    headers = {}
    for name, value in items:
        if not isinstance(name, str):
            raise ValueError('invalid-caller-header-name')
        lower = name.lower()
        allowed = lower in ('authorization', 'cookie', 'user-agent', 'traceparent',
                            'tracestate', 'connect-protocol-version') or \
                  lower.startswith(('x-cursor-', 'x-sand-'))
        if not allowed:
            continue
        if not re.fullmatch(r'[a-z0-9-]+', lower) or lower in headers or \
                not isinstance(value, str) or re.search(r'[\r\n\x00]', value):
            raise ValueError('ambiguous-or-invalid-caller-header')
        headers[lower] = value
    if not headers.get('authorization', '').strip() and not headers.get('cookie', '').strip():
        raise ValueError('caller-credential-required')
    headers.update({'content-type': 'application/json', 'accept': 'application/json',
                    'accept-encoding': 'identity'})
    return headers


def response_headers(items):
    connection_tokens = set()
    for name, value in items:
        if name.lower() == 'connection':
            connection_tokens.update(item.strip().lower() for item in value.split(','))
    return tuple((name, value) for name, value in items
                 if name.lower() not in HOP_HEADERS | connection_tokens)


def request_once(opener, url, body, headers):
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        response = opener.open(request, timeout=12)
    except urllib.error.HTTPError as error:
        response = error  # Preserve HTTP failures; only semantic unavailable can hand off.
    with response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('oversized-response')
        return WireResponse(response.status, raw, response_headers(response.headers.items()))


class Transport:
    def __init__(self, root, original_method, original_body, original_raw,
                 platform_opener=None, gateway_opener=None):
        self.root = checked_root(root)
        if original_method not in ('SendGrokBotUserMessage', 'GetGrokBotSendStatus') or \
                not isinstance(original_raw, bytes) or len(original_raw) > MAX_REQUEST or \
                parse_json(original_raw) != original_body:
            raise ValueError('invalid-original-request')
        self.original_method = original_method
        self.original_body = original_body
        self.original_raw = original_raw
        self.platform_opener = platform_opener or urllib.request.build_opener(NoRedirect())
        # Loopback must never expose the gateway token through an environment proxy.
        self.gateway_opener = gateway_opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect())
        self.original_result = None
        self.original_response = None

    def platform(self, method, body, headers):
        if method not in PLATFORM_METHODS:
            raise ValueError('unsupported-platform-method')
        raw = self.original_raw if method == self.original_method and body is self.original_body else encode_json(body)
        response = request_once(self.platform_opener, PLATFORM + method, raw, caller_headers(headers))
        try:
            payload = parse_json(response.payload)
        except (ValueError, UnicodeError, RecursionError):
            payload = response.payload
        result = core.Result(response.status, payload)
        if method == self.original_method:
            self.original_result, self.original_response = result, response
        return result

    def gateway(self, method, body):
        if method not in GATEWAY_METHODS:
            raise ValueError('unsupported-native-method')
        saved = parse_json(owned_bytes(self.root / 'gateway.json', private=True))
        token = saved.get('token') if isinstance(saved, dict) else None
        if not isinstance(token, str) or not token.strip() or re.search(r'[\r\n\x00]', token):
            raise ValueError('invalid-native-credential')
        response = request_once(self.gateway_opener, GATEWAY + method, encode_json(body),
            {'content-type': 'application/json', 'authorization': 'Bearer ' + token,
             'accept-encoding': 'identity'})
        return core.Result(response.status, parse_json(response.payload))

    def wire(self, result):
        if result is self.original_result:
            return self.original_response  # Keep the real upstream bytes and status.
        return WireResponse(result.status, encode_json(result.body),
            (('content-type', 'application/json'), ('cache-control', 'no-store')))
