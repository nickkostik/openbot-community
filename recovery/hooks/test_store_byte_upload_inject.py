import importlib.util
import pathlib
import shutil
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('store_byte_upload', pathlib.Path(__file__).with_name('store-byte-upload-inject.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class StoreByteUploadTests(unittest.TestCase):
    def fixture(self):
        # Independent minimal interface fixture; vendor bundles stay private.
        return m.CLASS + '''
  async putContentAddressed(key, bytes, signal) {
    const instruction = this.instruction;
OLD
    return putOnce();
  }
  async resolveMutableBaseline(key) { return this.baseline; }
  async putMutable(key, bytes, signal) {
    const instruction = this.instruction;
OLD
    return putOnce();
  }
  async putFromFile(key, srcPath, sha, size) { return 'native-file-path'; }
};
'''.replace('OLD', m.OLD)

    def run_node(self, body, timeout=12):
        if not shutil.which('node'): self.skipTest('Node is required')
        script = '''const assert=require('node:assert/strict');
const http=require('node:http'),{once}=require('node:events'),{createHash}=require('node:crypto');
const hash=b=>createHash('sha256').update(b).digest('hex');
''' + m.HELPER + '\n(async()=>{\n' + body + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_patch_scopes_both_byte_methods_and_is_idempotent(self):
        source = self.fixture(); patched = m.patch(source)
        self.assertEqual(m.patch(patched), patched)
        self.assertEqual(patched.count(m.NEW), 2)
        self.assertIn("return 'native-file-path'", patched)
        self.assertEqual(patched.replace(m.HELPER, '').replace(m.NEW, m.OLD), source)

    def test_changed_or_partial_or_ambiguous_contract_refuses(self):
        source = self.fixture(); patched = m.patch(source)
        for bad in (source.replace(m.OLD, 'unknown body', 1), source + source,
                    patched + m.MARKER, patched.replace(m.NEW, m.OLD, 1),
                    patched.replace(m.HELPER, m.MARKER), source + 'openbotStoreUploadBody',
                    patched + 'function openbotStoreUploadBody() {}'):
            with self.assertRaises(RuntimeError): m.patch(bad)

    def test_blob_snapshots_only_the_view_bytes_and_can_replay(self):
        self.run_node('''const full=Uint8Array.from([99,1,2,255,88]);
const view=full.subarray(1,4),body=openbotStoreUploadBody(view);
assert(body instanceof Blob);assert.equal(body.size,3);assert.equal(body.type,'');
full.fill(42);const expected=Buffer.from([1,2,255]);
assert.deepEqual(Buffer.from(await body.arrayBuffer()),expected);
assert.deepEqual(Buffer.from(await new Response(body.stream()).arrayBuffer()),expected);
assert.deepEqual(Buffer.from(await new Response(body.stream()).arrayBuffer()),expected);''')

    def test_unusual_inputs_retain_native_buffer_coercion(self):
        self.run_node('''for(const input of ['text',[1,2,255],new ArrayBuffer(4)]){
const actual=openbotStoreUploadBody(input);assert(Buffer.isBuffer(actual));assert.deepEqual(actual,Buffer.from(input));}
if(typeof SharedArrayBuffer!=='undefined'){
const shared=new Uint8Array(new SharedArrayBuffer(4));shared.set([1,2,3,4]);
assert.deepEqual(openbotStoreUploadBody(shared),Buffer.from(shared));}''')

    def test_large_blob_stream_keeps_transport_chunks_bounded(self):
        self.run_node('''const input=Buffer.alloc(2*1024*1024+3,171),body=openbotStoreUploadBody(input);
const chunks=[];for await(const chunk of body.stream()){
 assert(chunk.byteLength<=65536,'oversized transport chunk');chunks.push(Buffer.from(chunk));}
assert.deepEqual(Buffer.concat(chunks),input);''')

    def test_http_bytes_length_headers_and_redirect_replay_are_preserved(self):
        self.run_node('''const requests=[];
const server=http.createServer(async(req,res)=>{const chunks=[];for await(const b of req)chunks.push(b);
requests.push({url:req.url,method:req.method,headers:req.headers,body:Buffer.concat(chunks)});
if(req.url==='/redirect'){res.writeHead(307,{location:'/final'});res.end();}
else {res.writeHead(200,{etag:'"wire-etag"'});res.end();}});
server.listen(0,'127.0.0.1');await once(server,'listening');
const base='http://127.0.0.1:'+server.address().port;
try{for(const bytes of [new Uint8Array(),new Uint8Array([0,255,1]),Buffer.alloc(1024*1024,171)]){
 const response=await fetch(base+'/redirect',{method:'PUT',headers:{'X-Signed-Fixture':'preserve'},body:openbotStoreUploadBody(bytes)});
 assert.equal(response.status,200);assert.equal(response.headers.get('etag'),'"wire-etag"');await response.arrayBuffer();
 const pair=requests.splice(0);assert.equal(pair.length,2);
 for(const r of pair){assert.equal(r.method,'PUT');assert.equal(r.headers['content-length'],String(bytes.byteLength));
 assert.equal(r.headers['transfer-encoding'],undefined);assert.equal(r.headers['content-type'],undefined);
 assert.equal(r.headers['x-signed-fixture'],'preserve');assert.equal(hash(r.body),hash(bytes));}
}}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}''')

    def test_abort_during_stalled_upload_still_rejects(self):
        self.run_node('''let accepted;const ready=new Promise(r=>accepted=r);
const server=http.createServer((req,res)=>{req.pause();accepted();});
server.listen(0,'127.0.0.1');await once(server,'listening');
const ac=new AbortController();
try{const request=fetch('http://127.0.0.1:'+server.address().port,{method:'PUT',body:openbotStoreUploadBody(Buffer.alloc(8*1024*1024)),signal:ac.signal});
 const rejected=assert.rejects(request,e=>e.name==='AbortError');await ready;ac.abort();await rejected;
}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}''')

    def test_stalled_receiver_does_not_queue_an_entire_blob_on_the_client_socket(self):
        self.run_node('''let accepted;const ready=new Promise(r=>accepted=r);
const server=http.createServer((req,res)=>{req.pause();accepted();});
server.listen(0,'127.0.0.1');await once(server,'listening');const port=server.address().port;
let maximum=0;const timer=setInterval(()=>{for(const h of process._getActiveHandles())
 if(h.constructor?.name==='Socket'&&h.remotePort===port)maximum=Math.max(maximum,h.writableLength||0);},2);
const ac=new AbortController(),size=16*1024*1024;
try{const request=fetch('http://127.0.0.1:'+port,{method:'PUT',body:openbotStoreUploadBody(Buffer.alloc(size,3)),signal:ac.signal});
 const rejected=assert.rejects(request,e=>e.name==='AbortError');await ready;await new Promise(r=>setTimeout(r,200));
 ac.abort();await rejected;assert(maximum>0,'fixture never observed client backpressure');
 assert(maximum<size/4,'socket queued '+maximum+' bytes');
}finally{clearInterval(timer);server.closeAllConnections();await new Promise(r=>server.close(r));}''')


if __name__ == '__main__': unittest.main()
