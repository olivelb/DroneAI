import {createServer,request as httpRequest} from 'node:http';
import {readFile,writeFile,mkdir,appendFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,extname} from 'node:path';
import assert from 'node:assert/strict';
const root=process.cwd(),port=3029;
const upstream='http://127.0.0.1:30080/missions/gstile-qualification/gaussians/viewer';
const response=await fetch(upstream,{headers:{'Accept-Encoding':'zstd'},signal:AbortSignal.timeout(15000)});
if(!response.ok)throw Error(`Descriptor HTTP ${response.status}`);
const descriptor=await response.json();
if(descriptor.packs.some(p=>!p.encodings?.zstd?.url))throw Error('Missing Zstd transport');
try{await writeFile('descriptor-original.json',JSON.stringify(descriptor,null,2),{flag:'wx'});}
catch(error){
 if(error.code!=='EEXIST')throw error;
 const frozenDescriptor=JSON.parse(await readFile('descriptor-original.json','utf8'));
 assert.equal(descriptor.bundleId,frozenDescriptor.bundleId,'Restart cannot change bundle');
 assert.deepEqual(descriptor.manifest,frozenDescriptor.manifest,'Restart cannot change manifest');
}
const routes=new Map();
for(const pack of descriptor.packs){
 routes.set(`/data/${pack.id}/identity`,pack.url);
 if(pack.encodings?.zstd?.url)routes.set(`/data/${pack.id}/zstd`,pack.encodings.zstd.url);
}
await mkdir('results',{recursive:true});
const server=createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,`http://127.0.0.1:${port}`);
  res.setHeader('Cache-Control','no-store');
  if(url.pathname==='/progress'){
   await readFile('frozen.json');
   const protocol=JSON.parse(await readFile('protocol.json','utf8'));
   let next=0;const saved=[];
   for(const [index,spec] of protocol.runs.entries()){
    let result;
    try{result=JSON.parse(await readFile(`results/${spec.label}.json`,'utf8'));}
    catch(error){if(error.code==='ENOENT')break;throw error;}
    saved.push({label:spec.label,complete:result.complete===true,failure:result.failure});
    if(!result.complete||result.failure){next=-1;break;}
    next=index+1;
   }
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({next,saved}));return;
  }
  if(url.pathname==='/descriptor.json'){
   const payload=structuredClone(descriptor),run=url.searchParams.get('run')??'calibration';
   for(const pack of payload.packs){
    pack.url=`http://127.0.0.1:${port}/data/${pack.id}/identity?run=${encodeURIComponent(run)}`;
    if(pack.encodings?.zstd?.url)pack.encodings.zstd.url=`http://127.0.0.1:${port}/data/${pack.id}/zstd?run=${encodeURIComponent(run)}`;
   }
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify(payload));return;
  }
  if(routes.has(url.pathname)){
   const start=performance.now();let bytes=0;
   const headers={};for(const key of ['range','accept-encoding'])if(req.headers[key])headers[key]=req.headers[key];
   const remote=httpRequest(routes.get(url.pathname),{headers},up=>{
    res.writeHead(up.statusCode,{...up.headers,'cache-control':'no-store'});
    up.on('data',data=>bytes+=data.length);up.pipe(res);
    up.on('error',error=>res.destroy(error));
   });
   remote.on('error',error=>{if(!res.headersSent)res.writeHead(502);res.end(String(error));});
   res.on('close',()=>{remote.destroy();void appendFile('network.jsonl',JSON.stringify({run:url.searchParams.get('run'),path:url.pathname,bytes,ms:performance.now()-start,complete:res.writableFinished})+'\n');});
   remote.end();return;
  }
  if(url.pathname==='/results'&&req.method==='POST'){
   const chunks=[];let bytes=0;
   for await(const chunk of req){bytes+=chunk.length;if(bytes>80_000_000)throw Error('Result too large');chunks.push(chunk);}
   const body=Buffer.concat(chunks),value=JSON.parse(body);
   if(!/^[a-z0-9-]{1,90}$/.test(value.label))throw Error('Invalid result label');
   await writeFile(`results/${value.label}.json`,body,{flag:'wx'});
   res.setHeader('Content-Type','application/json');res.end(JSON.stringify({saved:value.label,sha256:createHash('sha256').update(body).digest('hex')}));return;
  }
  const path=resolve(root,'.'+(url.pathname==='/'?'/index.html':url.pathname));
  if(!path.startsWith(root+'/'))throw Error('Unsafe path');
  if(url.pathname==='/descriptor-original.json')throw Error('Private descriptor');
  const allowed=['.html','.js','.mjs','.json'];if(!allowed.includes(extname(path)))throw Error('Unsupported file');
  res.setHeader('Content-Type',path.endsWith('.html')?'text/html':path.endsWith('.json')?'application/json':'text/javascript');
  res.end(await readFile(path));
 }catch(error){if(!res.headersSent)res.writeHead(500);res.end(String(error));}
});
server.listen(port,'127.0.0.1',()=>console.log(`Qualification server http://127.0.0.1:${port}`));
