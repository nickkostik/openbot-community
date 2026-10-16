"""Serve the existing box UI through its authenticated execution transport.

The connector runs on an independent host. Public traffic can reach only the
box's existing port 9280; execution credentials remain in the private driver.
"""
import argparse
import base64
import importlib.util
import json
import os
import pathlib
import re
import stat
import threading
import time
import uuid
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


def clean_headers(headers):
    connection = next((v for k, v in headers.items() if k.lower() == 'connection'), '')
    removed = HOP_HEADERS | {v.strip().lower() for v in connection.split(',')}
    return {k: v for k, v in headers.items() if k.lower() not in removed and
            k.lower() != 'content-length' and '\n' not in k + v and '\r' not in k + v}


def checked_path(path):
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or \
            len(path) > 8192 or any(c in path for c in '\r\n\0'):
        raise ValueError('Invalid panel path')
    return path


class PanelTransport:
    def __init__(self, driver):
        self.driver = driver
        self.renew_lock = threading.Lock()
        self.renewed_at = 0

    def ready(self):
        with self.renew_lock:
            if time.monotonic() - self.renewed_at > 180:
                self.driver.renew()
                self.renewed_at = time.monotonic()

    def run(self, source):
        try:
            result = self.driver.run('/usr/bin/python3', ['-c', source], timeout=45)
        except urllib.error.HTTPError as error:
            # A replaced pod can reject the old execution identity. These
            # refusals happen before execution, so refreshing is safe even for
            # a save. Never retry an uncertain timeout/reset or an app error.
            if error.code not in {401, 403, 404}:
                raise
            with self.renew_lock:
                self.driver.renew()
                self.renewed_at = time.monotonic()
            result = self.driver.run('/usr/bin/python3', ['-c', source], timeout=45)
        if result.get('exitCode') != 0:
            raise RuntimeError('Panel transport command failed')
        return result.get('stdout', '')

    def request(self, method, path, headers, body):
        if method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
            raise ValueError('Unsupported panel method')
        checked_path(path)
        if len(body) > MAX_BODY:
            raise ValueError('Panel request exceeds its bound')
        self.ready()
        headers = clean_headers(headers)
        if sum(len(k) + len(v) for k, v in headers.items()) > 32768:
            raise ValueError('Panel headers exceed their bound')
        payload = base64.b64encode(body).decode()
        staged = None
        # NativeExec limits one argument. Large imports use an owned temporary
        # file, never shell interpolation or an arbitrary destination path.
        if len(payload) > 48000:
            staged = '/home/box/sand-data/.openbot-panel-relay/' + uuid.uuid4().hex
            try:
                for offset in range(0, len(body), 32000):
                    chunk = base64.b64encode(body[offset:offset + 32000]).decode()
                    self.run("import os,pathlib,stat,base64; p=pathlib.Path(" + repr(staged) + "); "
                             "p.parent.mkdir(mode=0o700,exist_ok=True); i=p.parent.lstat(); "
                             "assert stat.S_ISDIR(i.st_mode) and i.st_uid==os.getuid()==1000; "
                             "fd=os.open(p,os.O_WRONLY|os.O_NOFOLLOW|" +
                             ('os.O_CREAT|os.O_EXCL' if offset == 0 else 'os.O_APPEND') +
                             ",0o600); i=os.fstat(fd); assert stat.S_ISREG(i.st_mode) and i.st_uid==os.getuid(); "
                             "os.write(fd,base64.b64decode(" + repr(chunk) + ")); os.close(fd)")
            except BaseException:
                self.remove(staged)
                raise
        source = '''import base64,json,urllib.request,urllib.error,pathlib,os,stat
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):return None
body=BODY
req=urllib.request.Request('http://127.0.0.1:9280'+PATH,data=body if METHOD not in ['GET','HEAD'] else None,headers=HEADERS,method=METHOD)
try:r=urllib.request.build_opener(NoRedirect).open(req,timeout=30)
except urllib.error.HTTPError as e:r=e
with r:
 data=r.read(LIMIT+1)
 if len(data)>LIMIT:raise RuntimeError('Panel response exceeds its bound')
 print(json.dumps({'status':r.code,'headers':dict(r.headers),'cookies':r.headers.get_all('Set-Cookie') or [],'body':base64.b64encode(data).decode()}))
'''
        if staged:
            body_source = ("pathlib.Path(" + repr(staged) + ").read_bytes()")
            source = source.replace('body=BODY',
                'p=pathlib.Path(' + repr(staged) + '); i=p.lstat(); '
                'assert stat.S_ISREG(i.st_mode) and i.st_uid==os.getuid()==1000\nbody=BODY')
        else:
            body_source = 'base64.b64decode(' + repr(payload) + ')'
        values = {'PATH':repr(path), 'METHOD':repr(method), 'HEADERS':repr(headers),
                  'LIMIT':str(MAX_RESPONSE), 'BODY':body_source}
        source = re.sub(r'\b(?:PATH|METHOD|HEADERS|LIMIT|BODY)\b',
                        lambda match: values[match.group()], source)
        try:
            result = json.loads(self.run(source))
            raw = base64.b64decode(result['body'], validate=True)
            if len(raw) > MAX_RESPONSE or not isinstance(result['status'], int) or \
                    not 100 <= result['status'] <= 599:
                raise RuntimeError('Invalid panel transport response')
            response_headers = clean_headers(result['headers'])
            cookies = result.get('cookies', [])
            if len(cookies) > 1 and all(isinstance(value, str) and
                                      '\r' not in value and '\n' not in value for value in cookies):
                response_headers = {k:v for k,v in response_headers.items() if k.lower() != 'set-cookie'}
                response_headers['Set-Cookie'] = cookies
            if method == 'HEAD':
                original_length = next((v for k, v in result['headers'].items()
                                        if k.lower() == 'content-length'), None)
                if original_length is not None and str(original_length).isdigit():
                    response_headers['Content-Length'] = str(original_length)
            return result['status'], response_headers, raw
        finally:
            if staged:
                self.remove(staged)

    def remove(self, staged):
        try:
            self.run('import pathlib;p=pathlib.Path(' + repr(staged) + ');p.unlink(missing_ok=True)')
        except Exception:
            # A subsequent request does not reuse this unguessable private path.
            pass


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # URLs, request bodies and credentials never enter service logs.

    def body(self):
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            chunks = []; size = 0
            while True:
                line = self.rfile.readline(128)
                if not line.endswith(b'\r\n'):
                    raise ValueError('Invalid chunk header')
                count = int(line.strip().split(b';', 1)[0], 16)
                if count < 0 or size + count > MAX_BODY:
                    raise ValueError('Panel request exceeds its bound')
                if not count:
                    for _ in range(64):
                        trailer = self.rfile.readline(8192)
                        if trailer == b'\r\n':
                            return b''.join(chunks)
                    raise ValueError('Invalid chunk trailers')
                raw = self.rfile.read(count)
                if len(raw) != count or self.rfile.read(2) != b'\r\n':
                    raise ValueError('Incomplete panel body')
                chunks.append(raw); size += count
        size = int(self.headers.get('Content-Length', '0'))
        if size < 0 or size > MAX_BODY:
            raise ValueError('Panel request exceeds its bound')
        result = self.rfile.read(size)
        if len(result) != size:
            raise ValueError('Incomplete panel body')
        return result

    def forward(self):
        self.connection.settimeout(50)
        acquired = False
        try:
            body = self.body()
            acquired = self.server.lanes.acquire(timeout=10)
            if not acquired:
                self.send_error(503, 'Panel relay is busy'); return
            status, headers, raw = self.server.transport.request(
                self.command, self.path, dict(self.headers.items()), body)
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() not in {'server', 'date', 'content-length'}:
                    for item in value if isinstance(value, list) else [value]:
                        self.send_header(key, item)
            length = headers.get('Content-Length', str(len(raw))) if self.command == 'HEAD' else str(len(raw))
            self.send_header('Content-Length', length)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
        except ValueError:
            self.close_connection = True
            self.send_error(400, 'Invalid panel request')
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.send_error(502, 'Box panel transport unavailable')
        finally:
            if acquired:
                self.server.lanes.release()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = forward


def driver_from(path, directory):
    path = pathlib.Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError('Untrusted private execution driver')
    directory = pathlib.Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Untrusted private transport directory')
    spec = importlib.util.spec_from_file_location('panel_private_transport', path)
    driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
    driver.ROOT = directory

    def atomic_json(path, value):
        temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(value, out); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    driver.private_json = atomic_json
    return driver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--driver', required=True)
    parser.add_argument('--private-state', required=True)
    parser.add_argument('--port', type=int, default=9289)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PanelHandler)
    server.daemon_threads = True
    server.transport = PanelTransport(driver_from(args.driver, args.private_state))
    server.lanes = threading.BoundedSemaphore(12)
    server.serve_forever()


if __name__ == '__main__':
    main()
