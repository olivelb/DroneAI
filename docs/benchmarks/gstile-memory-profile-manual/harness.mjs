import {cameraState,interpolatePose,numericDelta} from './camera.mjs';
import {cadenceSummary} from './controls.mjs';
import {memoryProfile} from './profile.mjs';
const protocol=await (await fetch('/protocol.json')).json();
const provenance=await (await fetch('/provenance.json')).json();
const index=Number(new URLSearchParams(location.search).get('index')??0);
const spec=protocol.runs[index];
if(!spec)throw Error('Invalid passage index');
const {arm,label}=spec;
const cacheProfile=memoryProfile(protocol,arm);
const {createPlayCanvasResidentBackend}=await import(`./${arm}/gstile/playcanvas-backend.js`);
const {decodeGsTileViewerDescriptor}=await import(`./${arm}/gstile/descriptor.js`);
const {GsTileRangeScheduler,gstileMemoryCacheBytes}=await import(`./${arm}/gstile/range-source.js`);
const {IndexedDbGsTilePersistentCache}=await import(`./${arm}/gstile/persistent-range-cache.js`);
const $=id=>document.getElementById(id),status=text=>{$('status').textContent=text;};
const now=()=>performance.now(),delay=ms=>new Promise(r=>setTimeout(r,Math.max(0,ms)));
const assert=(ok,message)=>{if(!ok)throw Error(message);};
assert(gstileMemoryCacheBytes(cacheProfile.profile)===cacheProfile.bytes,'Production cache profile differs from protocol');
let backend,scheduler,descriptor,statistics,animation,memoryTimer,running=false,pendingWrites=0;
const result={schema:protocol.schema,arm,label,index,protocol,cacheProfile,provenanceCommit:provenance.arms[arm],
  engineSourceHash:provenance.playcanvasSourceTree,timeOrigin:performance.timeOrigin,userAgent:navigator.userAgent,
  phases:[],commits:[],frames:[],inputs:[],longTasks:[],memory:[],visibility:[],errors:[],controls:[]};
const visibility=event=>result.visibility.push({at:now(),event,visibility:document.visibilityState,focus:document.hasFocus()});
document.addEventListener('visibilitychange',()=>{if(running)visibility('visibility');});
addEventListener('focus',()=>{if(running)visibility('focus');});
addEventListener('blur',()=>{if(running)visibility('blur');});
addEventListener('error',event=>result.errors.push({at:now(),message:event.message}));
addEventListener('unhandledrejection',event=>result.errors.push({at:now(),message:String(event.reason)}));
const consoleError=console.error.bind(console);
console.error=(...args)=>{result.errors.push({at:now(),message:args.map(String).join(' ')});consoleError(...args);};
document.addEventListener('gstile-benchmark-commit',event=>result.commits.push(event.detail));
document.addEventListener('gstile-benchmark-gpu-error',event=>result.errors.push({at:now(),message:`GPU: ${event.detail}`}));
if(PerformanceObserver.supportedEntryTypes.includes('longtask'))new PerformanceObserver(list=>{
  for(const e of list.getEntries())result.longTasks.push({at:e.startTime,duration:e.duration});
}).observe({type:'longtask',buffered:true});
function foreground(){
  assert(document.visibilityState==='visible'&&document.hasFocus()&&
    result.visibility.every(e=>e.visibility==='visible'&&e.focus),'Foreground lost; no rerun');
}
function sampleMemory(){
  if(!backend||!scheduler)return;
  const s=scheduler.statistics();
  result.memory.push({at:now(),scheduler:s,engine:backend.benchmarkMemory(),pendingWrites});
  if(s.cacheBytes>cacheProfile.bytes)result.errors.push({at:now(),message:'RAM cache cap exceeded'});
  if(s.activeNetwork>6||s.activePersistent>2||s.cacheProtectedBytes>cacheProfile.bytes*.75)result.errors.push({at:now(),message:'Pool/protection cap exceeded'});
}
function frame(timestamp){
  const at=now();statistics=backend.render(timestamp);
  result.frames.push({at,renderMs:now()-at,state:statistics.lodState,pending:statistics.pendingNodes,
    resident:statistics.residentGaussians,target:statistics.targetGaussians});
  animation=requestAnimationFrame(frame);
}
async function idle(name){
  foreground();status(`Passage ${index+1}/6 — contrôle ${name}`);
  const start=now(),samples=[];let raf;
  const tick=()=>{samples.push({at:now()-start,focus:document.hasFocus(),visibility:document.visibilityState});raf=requestAnimationFrame(tick);};
  raf=requestAnimationFrame(tick);await delay(protocol.cadence.durationMs+20);cancelAnimationFrame(raf);
  const durationMs=now()-start,summary=cadenceSummary(samples,durationMs,protocol.cadence);
  result.controls.push({name,start,durationMs,samples,...summary});
  foreground();assert(summary.healthy,`Idle cadence ${name} failed`);
}
async function settle(){
  const start=now();let quiet=null,last=result.commits.length,lastWrites=-1;
  while(now()-start<protocol.timeoutMs){
    await delay(50);foreground();
    assert(!result.errors.length&&statistics?.lodState!=='error','Runtime error');
    const s=scheduler.statistics();
    const ready=now()-start>250&&statistics&&statistics.pendingNodes===0&&statistics.lodState!=='refining'&&
      statistics.residentGaussians===statistics.targetGaussians&&statistics.selectedNodes===statistics.targetNodes&&s.active===0&&s.queued===0&&pendingWrites===0;
    if(!ready||result.commits.length!==last||s.persistentWrites!==lastWrites){quiet=null;last=result.commits.length;lastWrites=s.persistentWrites;}
    else if(quiet===null)quiet=now();
    else if(now()-quiet>=protocol.settleQuietMs){await backend.benchmarkQueueDone();sampleMemory();return;}
  }
  throw Error('Settle timeout');
}
async function phase(name,from,to){
  const start=now(),before=scheduler.statistics(),firstCommit=result.commits.length,firstFrame=result.frames.length;
  status(`Passage ${index+1}/6 — ${name}`);
  for(let i=0;i<=protocol.steps;i++){
    const scheduled=start+protocol.durationMs*i/protocol.steps;await delay(scheduled-now());foreground();
    const applied=now(),pose=interpolatePose(protocol[from],protocol[to],i/protocol.steps);
    backend.setCamera(cameraState(pose,descriptor.recommendedView,protocol));
    result.inputs.push({phase:name,index:i,scheduled,applied,pose});
  }
  const inputEnd=now();await settle();const settled=now(),snapshot=backend.benchmarkSnapshot();
  const commits=result.commits.slice(firstCommit),lastCommit=commits.at(-1)?.at??inputEnd;
  const after=scheduler.statistics();
  result.phases.push({name,start,inputEnd,settled,firstCommit,commitCount:commits.length,firstFrame,frameCount:result.frames.length-firstFrame,
    readyAt:Math.max(inputEnd,lastCommit),afterInputMs:Math.max(0,lastCommit-inputEnd),networkBefore:before,networkAfter:after,networkDelta:numericDelta(before,after),snapshot});
  $('summary').textContent=JSON.stringify(result.phases.map(p=>({name:p.name,afterInputMs:p.afterInputMs,network:p.networkDelta.networkBytes,diskHits:p.networkDelta.persistentCacheHits,ramHits:p.networkDelta.cacheHits})),null,2);
}
async function save(){
  result.finishedAt=new Date().toISOString();$('result').textContent=JSON.stringify(result);
  const response=await fetch('/results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});
  assert(response.ok,`Save HTTP ${response.status}`);
  const receipt=await response.json();assert(receipt.saved===label,'Wrong receipt');
}
async function run(){
  if(running)return;running=true;$('start').disabled=true;result.startedAt=new Date().toISOString();visibility('start');
  try{
    foreground();
    const prior=await (await fetch('/progress')).json();
    assert(prior.next===index,'Already run or out of sequence');
    const dbName=`${protocol.databasePrefix}${index}`;
    assert(!(await indexedDB.databases()).some(d=>d.name===dbName),'Database already exists; preserve failed attempt');
    result.persistentDatabase=dbName;
    await idle('avant');
    descriptor=decodeGsTileViewerDescriptor(await (await fetch(`/descriptor.json?run=${label}`)).json());result.bundleId=descriptor.bundleId;
    const persistent=new IndexedDbGsTilePersistentCache(protocol.persistentCacheBytes);
    const write=persistent.write.bind(persistent);
    persistent.write=(...args)=>{pendingWrites+=1;return write(...args).finally(()=>{pendingWrites-=1;});};
    scheduler=new GsTileRangeScheduler(6,gstileMemoryCacheBytes(cacheProfile.profile),300,persistent);
    backend=createPlayCanvasResidentBackend({gpuAssembly:'merged',transformPrecision:'packed',verticalFovDegrees:protocol.fov,opacityMode:'directional',workerAssembly:true,recycleDecodeInput:true});
    await backend.initialize($('viewer'));backend.resize(protocol.width,protocol.height,protocol.devicePixelRatio);
    result.adapter=backend.benchmarkInfo();memoryTimer=setInterval(sampleMemory,100);
    status(`Passage ${index+1}/6 — chargement initial`);
    await backend.loadBundle(location.href,descriptor.manifest,scheduler,new AbortController().signal,descriptor.packUrls,descriptor.recommendedView);
    animation=requestAnimationFrame(frame);await settle();result.initial=backend.benchmarkSnapshot();
    status(`Passage ${index+1}/6 — préparation porte`);
    backend.setCamera(cameraState(protocol.door,descriptor.recommendedView,protocol));await settle();result.doorStart=backend.benchmarkSnapshot();
    await phase('first-door-facade','door','facade');
    await phase('return-facade-door','facade','door');
    await phase('revisit-door-facade','door','facade');
    cancelAnimationFrame(animation);clearInterval(memoryTimer);await backend.benchmarkQueueDone();
    await idle('après');assert(!result.errors.length,'Runtime errors');
    const s=scheduler.statistics();assert(!s.networkRetries&&!s.zstdFallbacks&&!s.persistentErrors&&s.zstdResponses>0,'Transport/cache fallback or errors');
    result.complete=true;await save();backend.dispose();backend=null;
    if(index+1<protocol.runs.length)location.href=`/?index=${index+1}&auto=1`;
    else status('COMPARAISON TERMINÉE — six passages enregistrés');
  }catch(error){
    cancelAnimationFrame(animation);clearInterval(memoryTimer);result.failure=String(error);
    status(`ÉCHEC — ${error}`);
    try{await save();}catch(saveError){status(`ÉCHEC — ${error}; sauvegarde: ${saveError}`);}
    try{backend?.dispose();backend=null;}catch(disposeError){consoleError(disposeError);}
  }
}
addEventListener('beforeunload',()=>{cancelAnimationFrame(animation);clearInterval(memoryTimer);backend?.dispose();});
status(`Prêt — passage ${index+1}/6 ${label} — cache ${cacheProfile.bytes/1024**2} Mio. Cliquez pour lancer.`);
$('start').addEventListener('click',run);
if(new URLSearchParams(location.search).get('auto')==='1')await run();
