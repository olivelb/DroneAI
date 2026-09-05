// Generate an isolated browser harness from the checked-out production renderer.
import {readFile,writeFile,mkdir,cp,readdir} from 'node:fs/promises';
import {resolve,dirname,relative} from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const repo=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const output=resolve(process.argv[2]);
const ts=(await import(repo+'/app4-dashboard/frontend/node_modules/typescript/lib/typescript.js')).default;
const base=repo+'/app4-dashboard/frontend/app/lib';
const hash=x=>createHash('sha256').update(x).digest('hex');
const provenance={inputs:{},instrumentation:'Fixed native-selected LOD cut, direct benchmark camera, GPU telemetry. Production shaders and decode unchanged.'};
await mkdir(output,{recursive:true});
const files=['contract-decoder.ts',...(await readdir(base+'/gstile')).filter(x=>x.endsWith('.ts')&&!/\.(test|bench)\.ts$/.test(x)).map(x=>'gstile/'+x)];
for(const file of files){
  const original=await readFile(base+'/'+file,'utf8');let source=original;
  if(file==='gstile/lod-selection.ts'){
    source=source.replace('export const selectGsTileLod =','const productionSelectGsTileLod =');
    source+='\nexport const selectGsTileLod = () => globalThis.__nativeCut;\n';
  }
  if(file==='gstile/playcanvas-backend.ts'){
    const needle='  setCamera(camera: GaussianCameraState) {';
    if(!source.includes(needle))throw Error('Production instrumentation anchor missing');
    source=source.replace(needle,`  benchmarkPose(eye: number[], target: number[], up: number[]) {
      const pc=this.#pc!; const camera=this.#camera!;
      camera.setPosition(...eye); camera.lookAt(new pc.Vec3(...target),new pc.Vec3(...up));
      this.#cameraDirty=false;this.#updateOpacityCameraUniform();this.#requestRender();
    }
    benchmarkQueueDone() { return (this.#app!.graphicsDevice as any).wgpu.queue.onSubmittedWorkDone(); }
    benchmarkInfo() { return (this.#app!.graphicsDevice as any).gpuAdapter.info; }
    benchmarkSnapshot() { return this.#debugSnapshot(); }
`+needle);
  }
  let compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
    .replace(/(from\s+['"])(\.{1,2}\/[^'"]+)(['"])/g,(_,a,b,c)=>a+b+'.js'+c)
    .replace(/(new URL\(['"]\.\/[^'"]+)\.ts(['"])/g,'$1.js$2');
  const target=output+'/'+file.replace(/\.ts$/,'.js');await mkdir(dirname(target),{recursive:true});await writeFile(target,compiled);
  provenance.inputs[file]={original:hash(original),compiled:hash(compiled)};
}
await cp(repo+'/app4-dashboard/frontend/node_modules/playcanvas/build/playcanvas/src',output+'/engine-source',{recursive:true});
await cp(repo+'/app4-dashboard/frontend/node_modules/playcanvas/build/playcanvas/modules',output+'/modules',{recursive:true});
const here=dirname(fileURLToPath(import.meta.url));
await cp(here+'/webgpu.html',output+'/index.html');await cp(here+'/webgpu.mjs',output+'/webgpu.mjs');
await writeFile(output+'/provenance.json',JSON.stringify(provenance,null,2));
console.log('Browser harness generated:',output);
