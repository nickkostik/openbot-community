import argparse
import collections
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
import urllib.parse

SD = pathlib.Path('/home/box/sand-data')
HOST_CAPABILITIES = frozenset(('orderedReplicasV1', 'sendAcceptanceV1', 'voiceSettingsV1',
                               'botTemplateJsonShareV1', 'botTemplateVisibilityV1'))
RUNTIME_CAPABILITIES = ('durableIdentityEnabled', 'durableIdentityWritesEnabled',
                        'temporalCreationEnabled', 'agentMessagingEnabled', 'serverRoomsEnabled')


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """An authenticated diagnostic must never carry credentials to a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_no_redirect(request, timeout):
    proxies = urllib.request.getproxies()
    # Keep an operator's proxy and no_proxy policy. The Computer's sanctioned
    # egress listener is a fallback only; direct Internet access can be absent.
    if not any(proxies.get(kind) for kind in ('http', 'https', 'all')):
        address = os.environ.get('SAND_EGRESS_TUNNEL_PROXY_ADDR')
        if address:
            proxies = {**proxies, 'https': egress_proxy_url(address)}
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies), NoRedirectHandler()).open(request, timeout=timeout)


def egress_proxy_url(value):
    """Accept only the provided plaintext loopback CONNECT listener."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('invalid-egress-proxy')
    if any(c in value for c in ('\\', '?', '#')):
        raise ValueError('invalid-egress-proxy')
    try:
        parts = urllib.parse.urlsplit(value if '://' in value else 'http://' + value)
        port = parts.port
    except ValueError:
        raise ValueError('invalid-egress-proxy') from None
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', '::1', 'localhost')
            or parts.username is not None or parts.password is not None
            or port is None or not 1 <= port <= 65535 or parts.path not in ('', '/')
            or parts.netloc.lower() not in (parts.hostname + ':' + str(port), '[::1]:' + str(port))):
        raise ValueError('invalid-egress-proxy')
    return urllib.parse.urlunsplit(('http', parts.netloc, '', '', ''))


def client_version(bundle, app_version=None, namespace='prod'):
    """Match the packaged host's HTTP version, not its deployment Git hash."""
    if namespace not in ('prod', 'dev', 'lab'):
        raise RuntimeError('Box namespace must be prod, dev, or lab')
    match = re.fullmatch(r'(\d+\.\d+\.\d+)(?:-.+)?', (app_version or '').strip())
    if not match:
        function = re.search(r'function sandClientBaseVersionOf\([^)]*\)\s*\{(.*?)\n\}', bundle, re.S)
        stamped = re.search(r'stampedVersionBaseOf\(\s*true\s*\?\s*[\"\']([^\"\']+)[\"\']', function.group(1)) if function else None
        match = re.fullmatch(r'(\d+\.\d+\.\d+)(?:-.+)?', stamped.group(1)) if stamped else None
    if not match:
        raise RuntimeError('Cannot resolve the packaged host HTTP client version safely')
    return match.group(1) + ('' if namespace == 'prod' else '-' + namespace)


def headers(namespace='prod'):
    credential = None
    for proc in pathlib.Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().split(b'\0')
            if b'/opt/sand/sand-host/host-main.cjs' not in cmd:
                continue
            env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
            credential = env.get(b'SAND_INFERENCE_RENEWAL_CREDENTIAL', b'').decode()
            if credential:
                break
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            pass
    if not credential:
        raise RuntimeError('Running host renewal credential missing')
    bundle = pathlib.Path('/opt/sand/sand-host/host-main.cjs').read_text()
    version = client_version(bundle, env.get(b'SAND_CLIENT_APP_VERSION', b'').decode(), namespace)
    base = {'content-type': 'application/json', 'x-cursor-client-type': 'sand',
            'x-cursor-client-source': 'sand-desktop', 'x-cursor-client-version': version,
            'x-sand-box-namespace': namespace, 'x-cursor-client-os': 'linux'}
    req = urllib.request.Request('https://api2.cursor.sh/sand-box/inference-credential',
                                 data=json.dumps({'credential': credential}).encode(), headers=base)
    with open_no_redirect(req, timeout=30) as response:
        token = json.load(response)['accessToken']
    return {**base, 'Authorization': 'Bearer ' + token, 'connect-protocol-version': '1',
            'x-sand-box-namespace': namespace}


def rpc(method, body, h):
    req = urllib.request.Request('https://api2.cursor.sh/aiserver.v1.GrokBotService/' + method,
                                 data=json.dumps(body).encode(), headers=h)
    try:
        with open_no_redirect(req, timeout=40) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {'http': error.code, 'error': error.read(500).decode()}


def gateway_status_url(value):
    """Validate the provider-owned gateway before attaching either credential."""
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('invalid-gateway-url')
    if any(c in value for c in ('\\', '?', '#')):
        raise ValueError('invalid-gateway-url')
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname or ''
        port = parts.port
    except ValueError:
        raise ValueError('invalid-gateway-url') from None
    if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
            or port not in (None, 443) or not parts.netloc
            or parts.netloc.lower() not in (host, host + ':443')
            or len(host) > 253
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', host)
            or not (host == 'cursorvm.com' or host.endswith('.cursorvm.com'))
            or not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*/?', parts.path)
            or any(segment in ('.', '..') for segment in parts.path.split('/'))):
        raise ValueError('invalid-gateway-url')
    return urllib.parse.urlunsplit(('https', parts.netloc, parts.path.rstrip('/') + '/api/getHostStatus', '', ''))


def public_gateway_status(h):
    """Check the public box transport, without sending or changing an agent."""
    report = {'status': 'error', 'nativeSendVerified': False}
    try:
        ensured = rpc('EnsureSandBox', {'wake': False}, h)
    except (OSError, ValueError):
        return {**report, 'error': 'ensure-request-failed'}
    if not isinstance(ensured, dict):
        return {**report, 'error': 'invalid-ensure-response'}
    if 'http' in ensured:
        code = ensured['http']
        return {**report, 'error': 'ensure-http-error',
                **({'httpStatus': code} if type(code) is int else {})}
    try:
        url = gateway_status_url(ensured.get('gatewayUrl'))
    except ValueError:
        return {**report, 'error': 'invalid-gateway-url'}
    credentials = [ensured.get(name) for name in ('gatewayToken', 'networkToken')]
    if any(not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token)
           for token in credentials):
        return {**report, 'error': 'missing-or-invalid-gateway-credentials'}
    request = urllib.request.Request(url, data=b'{}', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + credentials[0],
        'x-anyrun-network-token': credentials[1],
    }, method='POST')
    try:
        with open_no_redirect(request, timeout=40) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return {**report, 'error': 'invalid-gateway-response'}
            status = json.loads(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return {**report, 'error': 'gateway-redirect-refused' if 300 <= code < 400 else 'gateway-http-error',
                'httpStatus': code}
    except OSError:
        return {**report, 'error': 'gateway-transport-error'}
    except (ValueError, UnicodeError):
        return {**report, 'error': 'invalid-gateway-response'}
    if not isinstance(status, dict) or not isinstance(status.get('capabilities'), list):
        return {**report, 'error': 'invalid-gateway-response'}
    version = status.get('hostVersion')
    if version is not None and (not isinstance(version, str)
                               or not re.fullmatch(r'(?:[0-9a-f]{7,40}|\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)', version)):
        return {**report, 'error': 'invalid-gateway-response'}
    return {'status': 'ok', 'httpStatus': 200, 'hostVersion': version,
            'capabilities': sorted({value for value in status['capabilities']
                                    if isinstance(value, str) and value in HOST_CAPABILITIES}),
            'nativeSendVerified': False}


def box_run_state(value):
    """Normalize either documented enum spelling without printing unknown values."""
    if not isinstance(value, str):
        return None
    prefix = 'SAND_BOX_RUN_STATE_'
    state = value[len(prefix):] if value.startswith(prefix) else value
    return state if state in ('UNSPECIFIED', 'ABSENT', 'HIBERNATED', 'RUNNING', 'STARTING') else None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only box routing diagnostics. Transport health does not prove native message delivery.')
    parser.add_argument('--public-gateway', action='store_true',
                        help='Check EnsureSandBox(wake=false) and public gateway host status; do not send an agent message.')
    args = parser.parse_args(argv)
    try:
        h = headers()
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({'status': 'error', 'error': 'authentication-unavailable'}))
        return 1
    if args.public_gateway:
        report = public_gateway_status(h)
        print(json.dumps(report))
        return 0 if report['status'] == 'ok' else 1
    rows = rpc('ListGrokBotAgents', {}, h).get('agents', [])
    print(json.dumps({'namespace': h['x-sand-box-namespace'],
                      'clientVersion': h['x-cursor-client-version'],
                      'rosterCount': len(rows),
                      'harnesses': dict(collections.Counter(x.get('harness') if x.get('harness') in ('box', 'temporal') else 'unknown' for x in rows))}))
    runtime = rpc('GetGrokBotRuntimeCapabilities', {}, h).get('capabilities', {})
    print('capabilities', json.dumps({name: runtime[name] for name in RUNTIME_CAPABILITIES
                                     if type(runtime.get(name)) is bool}))
    state = rpc('GetSandBoxRunState', {}, h)
    safe_state = {name: state[name] for name in ('imageUpdateAvailable', 'egressTunnelOwnership')
                  if type(state.get(name)) is bool}
    canonical_state = box_run_state(state.get('state'))
    if canonical_state is not None:
        safe_state['state'] = canonical_state
    print('boxRunState', json.dumps(safe_state))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
