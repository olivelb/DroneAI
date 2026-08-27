import {execFileSync} from 'node:child_process';
import {readFile,writeFile,mkdir,cp,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import ts from '/home/olivier/droneAI/app4-dashboard/frontend/node_modules/typescript/lib/typescript.js';
import {memoryProfile,runtimeDifferences} from './profile.mjs';
const repo='/home/olivier/droneAI';
const prefix='app4-dashboard/frontend/app/lib/';
const protocol=JSON.parse(await readFile('protocol.json','utf8'));
const arms={reference:protocol.reference,candidate:protocol.candidate};
memoryProfile(protocol,'reference');memoryProfile(protocol,'candidate');
const sha=x=>createHash('sha256').update(x).digest('hex');
const provenance={arms,modules:{},instrumentation:'identical camera/commit/queue and logical-memory hooks; unique IndexedDB name per arm; no shader or selection changes'};
const once=(source,needle,replacement)=>{
 if(source.split(needle).length!==2)throw Error(`Expected exactly one match: ${needle}`);
 return source.replace(needle,replacement);
};
for(const [arm,commit] of Object.entries(arms)){
 const files=execFileSync('git',['ls-tree','-r','--name-only',commit,`${prefix}gstile`,`${prefix}contract-decoder.ts`],{cwd:repo,encoding:'utf8'}).trim().split('\n').filter(x=>x.endsWith('.ts')&&!/\.(test|bench)\.ts$/.test(x));
 for(const file of files){
  const original=execFileSync('git',['show',`${commit}:${file}`],{cwd:repo,encoding:'utf8'});
  let source=original;
  if(file.endsWith('/playcanvas-backend.ts')){
   source=once(source,'      generatedAt: new Date().toISOString(),',`      generatedAt: new Date().toISOString(),
      benchmarkCamera: {view: Array.from(this.#camera!.getWorldTransform().clone().invert().data),
        position: [this.#camera!.getPosition().x,this.#camera!.getPosition().y,this.#camera!.getPosition().z], fov: this.#camera!.camera!.fov,
        viewport: [this.#viewportWidth,this.#viewportHeight]},`);
   source=once(source,'      this.#scheduleLodPrefetch(selection.selectedNodeIds);',`      document.dispatchEvent(new CustomEvent('gstile-benchmark-commit', {
        detail: {at: performance.now(), snapshot: this.#debugSnapshot()}
      }));
      this.#scheduleLodPrefetch(selection.selectedNodeIds);`);
   source=once(source,'  setCamera(camera: GaussianCameraState) {',`  benchmarkSnapshot() { return this.#debugSnapshot(); }
  benchmarkInfo() { const i = (this.#app!.graphicsDevice as any).gpuAdapter.info;
    return {vendor:i.vendor, architecture:i.architecture, device:i.device, description:i.description}; }
  benchmarkQueueDone() { return (this.#app!.graphicsDevice as any).wgpu.queue.onSubmittedWorkDone(); }
  benchmarkMemory() { return {...(this.#app!.graphicsDevice as any)._vram,
    decodeInputRetainedBytes: this.#decodeWorkerPool?.retainedInputBytes ?? 0}; }
  setCamera(camera: GaussianCameraState) {`);
   source=once(source,'    this.#canvas = canvas;',`    this.#canvas = canvas;
    (device as any).wgpu.addEventListener('uncapturederror', (event: any) =>
      document.dispatchEvent(new CustomEvent('gstile-benchmark-gpu-error', {detail: event.error.message})));`);
  }
  if(file.endsWith('/persistent-range-cache.ts')) {
   source=once(source,'const DATABASE_NAME = "droneai-gstile-cache";',
    `const DATABASE_NAME = ${JSON.stringify(protocol.databasePrefix)} + (new URLSearchParams(location.search).get("index") ?? "0");`);
  }
  const relative=file.slice(prefix.length),path=`${arm}/${relative}`;
  await mkdir(path.slice(0,path.lastIndexOf('/')),{recursive:true});
  await writeFile(path+'.original',original);
  await writeFile(path,source);
  const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
   .replace(/(from\s+['"])(\.{1,2}\/[^'"]+)(['"])/g,(_,a,b,c)=>a+b+'.js'+c)
   .replace(/(new URL\(['"]\.\/[^'"]+)\.ts(['"])/g,'$1.js$2');
  await writeFile(path.replace(/\.ts$/,'.js'),compiled);
  provenance.modules[`${arm}/${relative}`]={original:sha(original),instrumented:sha(source),compiled:sha(compiled)};
 }
}
const engineRoot=`${repo}/app4-dashboard/frontend/node_modules/playcanvas`;
runtimeDifferences(protocol,provenance);
await cp(`${engineRoot}/build/playcanvas/src`,'engine-source',{recursive:true});
await cp(`${engineRoot}/build/playcanvas/modules`,'modules',{recursive:true});
provenance.engineInputs={};
for(const path of (await readdir('engine-source',{recursive:true})).filter(x=>x.endsWith('.js')).sort()){
 provenance.engineInputs[path]=sha(await readFile(`engine-source/${path}`));
}
for(const path of (await readdir('modules',{recursive:true})).filter(x=>x.endsWith('.js')).sort()){
 provenance.engineInputs[`../modules/${path}`]=sha(await readFile(`modules/${path}`));
}
provenance.playcanvasSourceTree=sha(JSON.stringify(provenance.engineInputs));
provenance.playcanvasVersion=JSON.parse(await readFile(`${engineRoot}/package.json`,'utf8')).version;
await writeFile('provenance.json',JSON.stringify(provenance,null,2));
console.log(JSON.stringify({modules:Object.keys(provenance.modules).length,engine:provenance.playcanvasSourceTree,engineModules:Object.keys(provenance.engineInputs).length}));
