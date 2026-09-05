import {createPlayCanvasResidentBackend} from './gstile/playcanvas-backend.js';
import {GsTileRangeScheduler} from './gstile/range-source.js';
import {decodeGsTileManifest} from './gstile/contracts.js';
const $=id=>document.getElementById(id);
const percentile=(a,p)=>[...a].sort((x,y)=>x-y)[Math.floor(p*(a.length-1))];
let backend;
$('start').onclick=async()=>{
 $('start').disabled=true;const errors=[],logs=[];
 const onError=e=>errors.push(String(e.reason||e.message));addEventListener('error',onError);addEventListener('unhandledrejection',onError);
 const oldError=console.error;console.error=(...a)=>{errors.push(a.map(String).join(' '));oldError(...a);};
 try{
  const manifest=decodeGsTileManifest(await(await fetch('/bundle/manifest.json')).json());
  const root=manifest.nodes.find(n=>n.id===manifest.root);
  const center=root.bounds.min.map((x,i)=>(x+root.bounds.max[i])*.5-manifest.coordinateFrame.origin[i]);
  const radius=Math.hypot(...root.bounds.min.map((x,i)=>(root.bounds.max[i]-x)*.5));
  const budget=Number(new URLSearchParams(location.search).get('budget')||250000);
  if(!Number.isInteger(budget)||budget<16384||budget>8000000)throw Error('Invalid benchmark budget');
  const scheduler=new GsTileRangeScheduler(4,384*1024**2,0);
  backend=createPlayCanvasResidentBackend({maximumResidentGaussians:budget,initialResidentGaussians:budget,verticalFovDegrees:60,workerAssembly:true});
  await backend.initialize($('viewer'));backend.resize(1440,900,1);
  globalThis.streamingBackend=backend;
  const begin=performance.now();
  await backend.loadBundle(location.origin+'/bundle/manifest.json',manifest,scheduler,new AbortController().signal);
  const firstImageMs=performance.now()-begin,samples=[],commits=[];let previous='',stats,lastFrame=performance.now(),stoppedAt=0;
  for(let i=0;;i++){
    await new Promise(requestAnimationFrame);
    if(i<360){
      const angle=.9*Math.sin(i*.04),distance=radius*(1.6+.9*Math.sin(i*.08));
      const target=[center[0]+radius*.25*Math.sin(i*.06),center[1],center[2]];
      backend.benchmarkPose([target[0]+Math.sin(angle)*distance,target[1]-Math.cos(angle)*distance,target[2]+distance*.25],target,[0,0,1]);
    }
    const t=performance.now();stats=backend.render(t);
    if(i===360)stoppedAt=t;
    const snapshot=backend.benchmarkSnapshot();
    const cut=JSON.stringify({ids:snapshot.selection.selectedNodeIds,base:snapshot.performance.baseOnlyNodes});
    samples.push({frame:i,frameGapMs:t-lastFrame,cpuMs:performance.now()-t,gpuMs:stats.frameGpuMs,...stats});lastFrame=t;
    if(cut!==previous){commits.push({frame:i,cut,snapshot});previous=cut;}
    $('status').textContent='Streaming '+i+'/720 — '+stats.residentGaussians+' splats — '+stats.lodState;
    const sameCut=JSON.stringify([...snapshot.selection.selectedNodeIds].sort())===
      JSON.stringify([...snapshot.selection.targetNodeIds].sort());
    if(i>=720&&sameCut&&!snapshot.performance.baseOnlyNodes.length&&
       ['steady','budget-limited'].includes(stats.lodState)&&scheduler.statistics().active===0)break;
    if(stoppedAt&&performance.now()-stoppedAt>90000)throw Error('LOD/SH did not converge after motion stopped');

  }
  await backend.benchmarkQueueDone();
  const cpu=samples.map(s=>s.cpuMs),gpu=samples.map(s=>s.frameGpuMs).filter(x=>Number.isFinite(x)&&x>0);
  const report={schema:'droneai-webgpu-streaming-v1',bundleId:manifest.bundleId,budget,converged:true,firstImageMs,
    cpuP95Ms:percentile(cpu,.95),cpuMaximumMs:percentile(cpu,1),gpuP95Ms:percentile(gpu,.95), frameGapP95Ms:percentile(samples.map(s=>s.frameGapMs),.95),frameGapMaximumMs:percentile(samples.map(s=>s.frameGapMs),1),
    samples,commits,snapshot:backend.benchmarkSnapshot(),errors,range:scheduler.statistics(),
    provenance:await(await fetch('/provenance.json')).json()};
  await fetch('/results',{method:'POST',body:JSON.stringify(report)});
  $('status').textContent='Terminé — erreurs: '+errors.length+' — '+stats.lodState;
  $('result').textContent=JSON.stringify({...report,samples:report.samples.length,commits:report.commits.length,provenance:'saved'},null,2);
 }catch(error){await fetch('/results',{method:'POST',body:JSON.stringify({error:String(error),errors})});$('status').textContent=String(error);}
};
