// Reuse the transpiler, keeping the actual production LOD selector.
import {readFile,writeFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const here=dirname(fileURLToPath(import.meta.url));
const output=resolve(process.argv[2]);
let prepare=await readFile(here+'/prepare-webgpu.mjs','utf8');
prepare=prepare.replace("if(file==='gstile/lod-selection.ts'){","if(false){");
prepare=prepare.replace('this.#cameraDirty=false;this.#updateOpacityCameraUniform();this.#requestRender();',
 'this.#cameraDirty=false;this.#updateOpacityCameraUniform();this.#recordLodCameraMotion(performance.now());this.#scheduleLodUpdate();this.#requestRender();');
prepare=prepare.replace("instrumentation:'Fixed native-selected LOD cut, direct benchmark camera, GPU telemetry. Production shaders and decode unchanged.'",
 "instrumentation:'Production streaming and LOD selection; direct camera trajectory and GPU telemetry.'");
prepare=prepare.replace("const repo=resolve(dirname(fileURLToPath(import.meta.url)),'../..');",
  "const repo="+JSON.stringify(resolve(here,'../..'))+";");
prepare=prepare.replace("const here=dirname(fileURLToPath(import.meta.url));","const here="+JSON.stringify(here)+";");
const run=spawnSync(process.execPath,['--input-type=module','-',output],{input:prepare,stdio:['pipe','inherit','inherit']});
if(run.status!==0)throw Error('Harness build failed');
const html=(await readFile(output+'/index.html','utf8')).replace('Même coupe, caméra, résolution et trajectoire que le passage natif.',
  'Streaming réel : zooms, déplacements rapides, puis stabilisation des SH.').replace('comparaison WebGPU / natif','transitions LOD WebGPU');
await writeFile(output+'/index.html',html);
await writeFile(output+'/webgpu.mjs',await readFile(here+'/streaming.mjs'));
