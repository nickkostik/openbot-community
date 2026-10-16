"use strict";
// Exercise the installed native codec, auth interceptor and transport against
// a synthetic loopback store. This never commits to a real backup service.
const fs=require('node:fs'),crypto=require('node:crypto'),Module=require('node:module'),path=require('node:path'),http=require('node:http'),zlib=require('node:zlib'),assert=require('node:assert/strict');
const [candidate,expected,host]=process.argv.slice(2);
if(!path.isAbsolute(candidate||'')||!/^[0-9a-f]{64}$/.test(expected||'')||!path.isAbsolute(host||''))throw new Error('Expected owned source, digest and native module location');
const fd=fs.openSync(candidate,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let raw;
try{const info=fs.fstatSync(fd);if(!info.isFile()||info.uid!==process.getuid()||info.size>48*1024*1024)throw new Error('Untrusted native candidate');raw=fs.readFileSync(fd);}finally{fs.closeSync(fd);}
if(crypto.createHash('sha256').update(raw).digest('hex')!==expected)throw new Error('Native candidate changed');
let source=raw.toString('utf8');const start=source.indexOf('bootSandHost(runHostMain).catch('),end=source.indexOf('/*! Bundled license information:',start);
if(start<0||end<0)throw new Error('Native boot contract changed');
source=source.slice(0,start)+'module.exports={storeClient:createSandBoxStoreServiceClient,stockTransport:createSandBackendTransport,createPromiseClient,GrokBotService,request:CommitSandBoxStoreManifestRequest,response:CommitSandBoxStoreManifestResponse};\n'+source.slice(end);
const mod=new Module(candidate);mod.filename=path.join(path.dirname(host),'openbot-store-offline-check.cjs');mod.paths=Module._nodeModulePaths(path.dirname(host));mod._compile(source,mod.filename);const x=mod.exports;
async function run(){
 const records=[];let forceFailure=false;
 const server=http.createServer(async(req,res)=>{
  try{
   const chunks=[];for await(const chunk of req)chunks.push(chunk);const wire=Buffer.concat(chunks);assert.ok(wire.length<4*1024*1024);
   assert.equal(req.headers.authorization,'Bearer synthetic-current-token');
   if(req.url==='/aiserver.v1.DashboardService/GetUserPrivacyMode'){res.writeHead(200,{'content-type':'application/proto'});res.end(Buffer.alloc(0));return;}
   assert.equal(req.url,'/aiserver.v1.GrokBotService/CommitSandBoxStoreManifest');
   const decoded=req.headers['content-encoding']==='gzip'?zlib.gunzipSync(wire):wire;
   const message=x.request.fromBinary(decoded);assert.equal(message.baseEtag,'synthetic-etag');
   records.push({gzip:req.headers['content-encoding']==='gzip',wireBytes:wire.length,manifest:Buffer.from(message.manifest)});
   if(forceFailure){res.writeHead(503,{'content-type':'application/json'});res.end('{"code":"unavailable","message":"synthetic refusal"}');return;}
   res.writeHead(200,{'content-type':'application/proto'});res.end(Buffer.from(new x.response({status:1,etag:'synthetic-new-etag'}).toBinary()));
  }catch(e){res.writeHead(500,{'content-type':'application/json'});res.end('{"code":"internal","message":"synthetic check failed"}');server.failure=e;}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const deps={backend:{backendUrl:'http://127.0.0.1:'+server.address().port},getAccessToken:async()=> 'synthetic-current-token',getMachineId:()=> 'synthetic-machine',getTeamId:()=>undefined};
  const client=x.storeClient(deps),large=Buffer.alloc(2*1024*1024,42),small=Buffer.from('synthetic manifest');
  const response=await client.commitSandBoxStoreManifest({manifest:large,baseEtag:'synthetic-etag'});assert.equal(response.status,1);assert.equal(response.etag,'synthetic-new-etag');
  await client.commitSandBoxStoreManifest({manifest:small,baseEtag:'synthetic-etag'});
  const stock=x.createPromiseClient(x.GrokBotService,x.stockTransport(deps));await stock.commitSandBoxStoreManifest({manifest:large,baseEtag:'synthetic-etag'});
  forceFailure=true;await assert.rejects(client.commitSandBoxStoreManifest({manifest:large,baseEtag:'synthetic-etag'}));
  if(server.failure)throw server.failure;
  assert.equal(records.length,4);assert.deepEqual(records.map(r=>r.gzip),[true,false,false,true]);
  assert.deepEqual(records.map(r=>r.manifest),[large,small,large,large]);assert.ok(records[0].wireBytes<large.length/20);
  console.log(JSON.stringify({nativeCodecVerified:true,authPreserved:true,manifestAndEtagPreserved:true,largeStoreCompressed:true,smallStoreUncompressed:true,stockTransportUnchanged:true,rejectionPropagated:true}));
 }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
run().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});
