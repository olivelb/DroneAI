import {createPlayCanvasResidentBackend} from './gstile/playcanvas-backend.js';
import {GsTileRangeScheduler} from './gstile/range-source.js';
import {decodeGsTileManifest} from './gstile/contracts.js';
const $=id=>document.getElementById(id);
const sub=(a,b)=>a.map((v,i)=>v-b[i]),add=(a,b)=>a.map((v,i)=>v+b[i]),mul=(a,s)=>a.map(v=>v*s);
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0),cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const norm=a=>mul(a,1/Math.hypot(...a));
const rotate=(v,axis,angle)=>add(add(mul(v,Math.cos(angle)),mul(cross(axis,v),Math.sin(angle))),mul(axis,dot(axis,v)*(1-Math.cos(angle))));
const percentile=(a,p)=>[...a].sort((x,y)=>x-y)[Math.floor(p*(a.length-1))];
let backend;
async function run(){
  $('start').disabled=true;const errors=[];
  const handler=e=>errors.push(String(e.reason||e.message));addEventListener('unhandledrejection',handler);addEventListener('error',handler);
  try{
    const native=await(await fetch('/native.json')).json();
    const manifest=decodeGsTileManifest(await(await fetch('/bundle/manifest.json')).json());
    const selected=native.selectedNodes.map(i=>manifest.nodes[i]);
    globalThis.__nativeCut={selectedNodeIds:selected.map(n=>n.id),residentGaussians:native.residentGaussians,
      maximumSelectedErrorPixels:native.maximumSelectedErrorPixels,effectiveMaximumErrorPixels:native.maximumSelectedErrorPixels,
      selectedExactNodes:selected.filter(n=>n.tile).length,selectedProxyNodes:selected.filter(n=>n.lodTile).length,
      selectedFullDepthNodes:0,selectedShallowLeafNodes:0,selectedInternalNodes:selected.filter(n=>n.children).length,
      selectedLeafDepthCounts:[],maximumSelectedProxyScreenRadiusPixels:0,budgetLimited:true,unresolvedMaximumErrorPixels:native.maximumSelectedErrorPixels};
    const scheduler=new GsTileRangeScheduler(4,768*1024**2,0);
    backend=createPlayCanvasResidentBackend({maximumResidentGaussians:native.budget,initialResidentGaussians:native.budget,
      verticalFovDegrees:60,background:[.025,.033,.045],workerAssembly:true});
    $('status').textContent='Initialisation WebGPU...';await backend.initialize($('viewer'));backend.resize(1440,900,1);
    const loadStart=performance.now();
    await backend.loadBundle(location.origin+'/bundle/manifest.json',manifest,scheduler,new AbortController().signal);
    // Native fit pose is recorded separately from the orbit endpoint.
    const eye=native.initialCameraEye,up=native.initialCameraUp,target=native.initialCameraPivot;
    const forward=norm(sub(target,eye)),right=norm(cross(forward,up)),vertical=norm(cross(right,forward)),offset=sub(eye,target);
    const deadline=performance.now()+90000;
    while(true) {
      backend.benchmarkPose(eye,target,up);backend.render(performance.now());
      await backend.benchmarkQueueDone();
      const snap=backend.benchmarkSnapshot();
      if(snap.resident.gaussianCount===native.residentGaussians &&
         !snap.performance.baseOnlyNodes.length && !snap.performance.loadingSh &&
         snap.selection.selectedCount===selected.length) break;
      if(performance.now()>deadline)throw Error("Incomplete fixed cut: "+JSON.stringify(snap.selection));
      await new Promise(r=>setTimeout(r,16));
    }
    const loadMs=performance.now()-loadStart;
    const samples=[];let stats;
    for(let i=0;i<native.frames+32;i++){
      const angle=0;
      const position=add(target,rotate(offset,vertical,angle)),cameraUp=rotate(up,vertical,angle);
      backend.benchmarkPose(position,target,cameraUp);
      const begin=performance.now();stats=backend.render(begin);await backend.benchmarkQueueDone();
      if(i>=32)samples.push({frame:i-32,gpuMs:stats.frameGpuMs,cpuAndQueueMs:performance.now()-begin});
      $('status').textContent='Mesure WebGPU '+Math.max(0,i-31)+' / '+native.frames;
    }
    const values=samples.map(s=>s.gpuMs).filter(v=>Number.isFinite(v)&&v>0);
    const snapshot=backend.benchmarkSnapshot(),adapter=backend.benchmarkInfo();
    const report={schema:'droneai-native-webgpu-comparison-v1',adapter:{vendor:adapter.vendor,architecture:adapter.architecture,device:adapter.device,description:adapter.description},
      nativeReport:native,webgpu:{residentGaussians:stats.residentGaussians,width:1440,height:900,loadMs,gpuMedianMs:percentile(values,.5),gpuP95Ms:percentile(values,.95),samples,snapshot},
      errors,provenance:await(await fetch('/provenance.json')).json()};
    if(stats.residentGaussians!==native.residentGaussians)throw Error('Different resident population');
    if(!values.length)throw Error('No WebGPU timestamp samples');
    $('result').textContent=JSON.stringify(report,null,2);
    backend.benchmarkPose(eye,target,up);backend.render(performance.now());
    report.capture=$('viewer').toDataURL('image/png');
    const response=await fetch('/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(report)});
    if(!response.ok)throw Error('Report save failed');
    $('status').textContent='Termine : WebGPU '+report.webgpu.gpuMedianMs.toFixed(2)+' ms GPU ; natif '+native.gpuMedianMs.toFixed(2)+' ms GPU.';
  }catch(error){$('status').textContent=String(error);await fetch('/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({error:String(error),errors})});}
}
$('start').onclick=run;
