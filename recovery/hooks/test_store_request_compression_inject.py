import importlib.util
import pathlib
import shutil
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('store_compression', pathlib.Path(__file__).with_name('store-request-compression-inject.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class StoreCompressionTests(unittest.TestCase):
    def fixture(self):
        return '''function createSafeConnectTransport(options2) {
  const httpClient = createNodeHttpClient({ httpVersion: options2.httpVersion });
  return createTransport({
    baseUrl: options2.baseUrl,
    httpClient: (request5) => httpClient(request5.signal !== undefined ? request5 : { ...request5, signal: new AbortController().signal }),
    useBinaryFormat: options2.useBinaryFormat ?? true,
    interceptors: options2.interceptors,
SAFE
  return createSafeConnectTransport({
    baseUrl: options2.backend.backendUrl,
    interceptors: [createSandRpcTracingInterceptor(), createSandInferenceInterceptor(options2), recoverInstalledRequests],
BACKEND
  return createClient(service, createSandBackendTransport(options2));
}
FACTORY
'''.replace('SAFE', m.SAFE).replace('BACKEND', m.BACKEND).replace('FACTORY', m.FACTORY)

    def test_patch_is_complete_and_idempotent(self):
        patched = m.patch(self.fixture())
        self.assertEqual(m.patch(patched), patched)

    def test_changed_native_transport_is_not_partially_patched(self):
        for anchor in [m.SAFE, m.BACKEND, m.FACTORY]:
            with self.assertRaisesRegex(RuntimeError, 'missing or ambiguous'):
                m.patch(self.fixture().replace(anchor, 'unknown upstream contract'))

    def test_partial_or_duplicate_stamp_is_rejected(self):
        patched = m.patch(self.fixture())
        for source in [m.MARKER, patched.replace(m.FACTORY_UPDATED, m.FACTORY), patched + m.MARKER]:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                m.patch(source)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for native factory behavior')
    def test_only_store_client_selects_gzip_with_a_large_request_threshold(self):
        source = '''const assert=require('node:assert/strict');
const gzip=require('node:zlib').gzipSync, gunzip=require('node:zlib').gunzipSync;
const compressionGzip={name:'gzip',compress:gzip}, compressionBrotli={name:'br'};
const GrokBotService={};const recovered={};
function createNodeHttpClient(options){return request=>request;}
function createTransport(options){return options;}
function createPromiseClient(service,transport){return transport;}
function createClient(service,transport){return transport;}
function createSandRpcTracingInterceptor(){return recovered;}
function createSandInferenceInterceptor(options){return options;}
const recoverInstalledRequests={};
function validateReadWriteMaxBytes(read,write,min){return {readMaxBytes:read,writeMaxBytes:write,compressMinBytes:min??1024};}
function sandBoxStoreTransportOptions(deps){return deps;}
const deps={backend:{backendUrl:'http://127.0.0.1:1'},getAccessToken:()=> 'synthetic'};
''' + m.patch(self.fixture()) + '''
const store=createSandBoxStoreServiceClient(deps),stock=createSandBackendTransport(deps);
assert.equal(store.sendCompression,compressionGzip);assert.equal(store.compressMinBytes,1048576);
assert.equal(stock.sendCompression,null);assert.equal(stock.compressMinBytes,1024);
assert.deepEqual(store.acceptCompression,stock.acceptCompression);
assert.equal(store.baseUrl,stock.baseUrl);assert.equal(store.useBinaryFormat,true);
assert.equal(store.interceptors[1].getAccessToken,deps.getAccessToken);
assert.equal(store.interceptors[0],recovered);assert.equal(store.interceptors[2],recoverInstalledRequests);
const bytes=Buffer.alloc(2*1024*1024,42);assert.deepEqual(gunzip(store.sendCompression.compress(bytes)),bytes);
const signal=new AbortController().signal;assert.equal(store.httpClient({signal}).signal,signal);
assert.ok(store.httpClient({}).signal instanceof AbortSignal);
'''
        result = subprocess.run(['node', '-e', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
