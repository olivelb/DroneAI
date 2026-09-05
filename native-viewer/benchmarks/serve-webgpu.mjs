import http from 'node:http';
import {readFile,writeFile,stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {resolve,extname,sep} from 'node:path';
const [rootArg,bundleArg,nativeArg,reportArg]=process.argv.slice(2);
const root=resolve(rootArg),bundle=resolve(bundleArg);
const manifest=JSON.parse(await readFile(resolve(bundle,'manifest.json'),'utf8'));
const packs=new Map(manifest.packs.map(p=>['/bundle/'+p.path,resolve(bundle,p.path)]));
for(const p of manifest.packs)delete p.encodings; // same canonical Q96 bytes, no transport comparison
const server=http.createServer(async(req,res)=>{
  try{
    const pathname=new URL(req.url,'http://localhost').pathname;
    if(pathname==='/results'&&req.method==='POST'){
      let text='';for await(const chunk of req){text+=chunk;if(text.length>16*1024**2)throw Error('Report too large');}
      const parsed=JSON.parse(text);await writeFile(reportArg,JSON.stringify(parsed,null,2));res.end('saved');return;
    }
    res.setHeader('Cross-Origin-Opener-Policy','same-origin');res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
    if(pathname==='/bundle/manifest.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(manifest));return;}
    if(pathname==='/native.json'){res.setHeader('Content-Type','application/json');res.end(await readFile(nativeArg));return;}
    let file=packs.get(pathname)||resolve(root,'.'+(pathname==='/'?'/index.html':pathname));
    if(!packs.has(pathname)&&!file.startsWith(root+sep)){res.writeHead(403);res.end();return;}
    const size=(await stat(file)).size;
    const mime={'.js':'text/javascript','.mjs':'text/javascript','.html':'text/html','.json':'application/json','.wasm':'application/wasm'};
    res.setHeader('Content-Type',mime[extname(file)]||'application/octet-stream');
    if(req.headers.range){
      const m=/^bytes=(\d+)-(\d+)$/.exec(req.headers.range);if(!m)throw Error('Invalid range');
      const start=Number(m[1]),end=Number(m[2]);if(start>end||end>=size)throw Error('Out of bounds range');
      res.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${size}`,'Content-Length':end-start+1,'Accept-Ranges':'bytes'});
      createReadStream(file,{start,end}).pipe(res);
    }else{res.setHeader('Content-Length',size);createReadStream(file).pipe(res);}
  } catch (error) {
    console.error('[GSTile benchmark] Request failed:', error);
    if (res.headersSent) { res.destroy(); return; }
    res.writeHead(500, {'Content-Type': 'text/plain; charset=utf-8'});
    res.end('Internal server error');
  }
});
server.listen(8768,'127.0.0.1',()=>console.log('GSTile comparison: http://127.0.0.1:8768/?dev=1'));
