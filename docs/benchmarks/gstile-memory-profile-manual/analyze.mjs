import assert from 'node:assert/strict';
import {readFile,writeFile,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {cameraState,interpolatePose} from './camera.mjs';
import {memoryProfile} from './profile.mjs';
import {cadenceSummary} from './controls.mjs';
const read=async p=>JSON.parse(await readFile(p,'utf8'));
const protocol=await read('protocol.json'),provenance=await read('provenance.json'),descriptor=await read('descriptor-original.json');
const sha=x=>createHash('sha256').update(x).digest('hex');
for(const [name,hash] of Object.entries(await read('frozen.json')))assert.equal(sha(await readFile(name)),hash,`frozen ${name}`);
for(const [name,hashes] of Object.entries(provenance.modules)){
  assert.equal(sha(await readFile(name+'.original')),hashes.original);
  assert.equal(sha(await readFile(name)),hashes.instrumented);
  assert.equal(sha(await readFile(name.replace(/\.ts$/,'.js'))),hashes.compiled);
}
for(const [name,expected] of Object.entries(provenance.engineInputs))assert.equal(sha(await readFile(`engine-source/${name}`)),expected);
const median=a=>{const x=[...a].sort((a,b)=>a-b);return x.length%2?x[(x.length-1)/2]:(x[x.length/2-1]+x[x.length/2])/2;};
const close=(a,b,tolerance=2e-5)=>assert(Math.abs(a-b)<=tolerance,`${a} != ${b}`);
const snap=s=>{
  assert.equal(s.loaded.length,1);assert.equal(s.resident.entityCount,1);assert.equal(s.resident.resourceCount,1);
  assert.equal(s.loaded[0].nodeId,'__merged__');assert(s.loaded[0].entityEnabled);
  assert.equal(s.loaded[0].residentCount,s.resident.gaussianCount);
  assert.equal(s.loaded[0].worldActiveSplats,s.resident.gaussianCount);
  assert(s.resident.gaussianCount>0&&s.resident.gaussianCount<=7500000);
  assert.equal(s.loaded[0].resourceStreams.length,11);
  assert.equal(s.performance.assemblyWorkerFailures,0);assert.equal(s.performance.assemblyWorkerDisabled,false);
  assert([null,0].includes(s.performance.lodDecodeWorkerFallbacks));
  assert.deepEqual(s.benchmarkCamera.viewport,[protocol.width,protocol.height]);assert.equal(s.benchmarkCamera.fov,protocol.fov);
};
const sameCut=(a,b)=>{
  assert.deepEqual([...a.selection.selectedNodeIds].sort(),[...b.selection.selectedNodeIds].sort());
  assert.equal(a.resident.gaussianCount,b.resident.gaussianCount);assert.deepEqual(a.benchmarkCamera,b.benchmarkCamera);
};
const files=(await readdir('results')).filter(f=>f.endsWith('.json'));
const runs=new Map(),rows=[],failures=[];
for(const spec of protocol.runs){
  if(!files.includes(spec.label+'.json'))continue;
  const r=await read(`results/${spec.label}.json`);runs.set(r.label,r);
  assert.equal(r.label,spec.label);assert.equal(r.arm,spec.arm);
  assert.equal(r.index,protocol.runs.indexOf(spec));
  const profile=memoryProfile(protocol,r.arm);assert.deepEqual(r.cacheProfile,profile);
  assert.deepEqual(r.protocol,protocol);assert.equal(r.provenanceCommit,provenance.arms[r.arm]);
  assert.equal(r.engineSourceHash,provenance.playcanvasSourceTree);
  if(r.failure||!r.complete){failures.push({label:r.label,failure:r.failure,controls:r.controls.map(({samples,...x})=>x),errors:r.errors});continue;}
  assert.equal(r.persistentDatabase,`${protocol.databasePrefix}${r.index}`);
  assert.equal(r.bundleId,descriptor.bundleId);assert.deepEqual(r.errors,[]);
  assert(r.visibility.every(x=>x.visibility==='visible'&&x.focus));
  assert.equal(r.controls.length,2);assert(r.controls.every(c=>c.healthy));
  for(const control of r.controls)assert(cadenceSummary(control.samples,control.durationMs,protocol.cadence).healthy);
  assert.equal(r.phases.length,3);snap(r.initial);snap(r.doorStart);
  assert(r.memory.length>0);
  for(const m of r.memory){
    assert(m.scheduler.cacheBytes<=profile.bytes);
    assert(m.scheduler.activeNetwork<=6);assert(m.scheduler.activePersistent<=2);
    assert(m.scheduler.cacheProtectedBytes<=profile.bytes*.75);
  }
  for(const p of r.phases){
    snap(p.snapshot);
    const input=r.inputs.filter(i=>i.phase===p.name);assert.equal(input.length,protocol.steps+1);
    const reverse=p.name==='return-facade-door',from=protocol[reverse?'facade':'door'],to=protocol[reverse?'door':'facade'];
    input.forEach((x,i)=>{assert.equal(x.index,i);assert.deepEqual(x.pose,interpolatePose(from,to,i/protocol.steps));close(x.scheduled,p.start+protocol.durationMs*i/protocol.steps);});
    const view=cameraState(to,descriptor.recommendedView,protocol).view;
    p.snapshot.benchmarkCamera.view.forEach((v,i)=>close(v,view[i]));
    const commits=r.commits.slice(p.firstCommit,p.firstCommit+p.commitCount);
    const endpoint=Math.max(p.inputEnd,commits.at(-1)?.at??p.inputEnd);
    close(p.readyAt,endpoint);close(p.afterInputMs,endpoint-p.inputEnd);
    const n=p.networkAfter;assert.equal(n.active,0);assert.equal(n.queued,0);
    assert.equal(n.networkRetries,0);assert.equal(n.zstdFallbacks,0);assert.equal(n.persistentErrors,0);
    assert(n.zstdResponses>0);assert(n.persistentWrites>0);
    const tasks=r.longTasks.filter(t=>t.at>=p.start&&t.at<=endpoint);
    rows.push({label:r.label,arm:r.arm,phase:p.name,cacheLimitBytes:profile.bytes,afterInputMs:p.afterInputMs,
      networkBytes:p.networkDelta.networkBytes,cacheHits:p.networkDelta.cacheHits,cacheMisses:p.networkDelta.cacheMisses,
      persistentHits:p.networkDelta.persistentCacheHits,prefetchBytes:p.networkDelta.prefetchNetworkBytes,
      rejectedPredictions:p.networkDelta.prefetchCacheAdmissionRejections??null,
      longTasks:tasks.length,longTaskMs:tasks.reduce((sum,t)=>sum+t.duration,0),
      cachePeakBytes:Math.max(...r.memory.map(m=>m.scheduler.cacheBytes)),
      engineLogicalPeakBytes:Math.max(...r.memory.map(m=>['tex','vb','ib','ub','sb'].reduce((sum,k)=>sum+(m.engine[k]??0),0))),
      decodeRetainedPeakBytes:Math.max(...r.memory.map(m=>m.engine.decodeInputRetainedBytes)),
      workerAssemblyPeakBytes:p.snapshot.performance.lodDecodeBreakdown?.assemblyPeakBytes??null});
  }
}
const comparisons=[];
for(let pair=1;pair<=2;pair++){
  const a=runs.get(`pair-${pair}-reference`),b=runs.get(`pair-${pair}-candidate`);
  if(!a?.complete||!b?.complete)continue;
  assert.deepEqual(a.adapter,b.adapter);sameCut(a.initial,b.initial);sameCut(a.doorStart,b.doorStart);
  for(let i=0;i<3;i++)sameCut(a.phases[i].snapshot,b.phases[i].snapshot);
  for(const phase of ['first-door-facade','return-facade-door','revisit-door-facade']){
    const reference=rows.find(r=>r.label===a.label&&r.phase===phase),candidate=rows.find(r=>r.label===b.label&&r.phase===phase);
    const d=protocol.decision;
    comparisons.push({pair,phase,reference,candidate,
      readinessWithinTolerance:candidate.afterInputMs<=reference.afterInputMs*(1+d.readinessRelativeTolerance)+d.readinessAbsoluteToleranceMs,
      networkWithinTolerance:candidate.networkBytes<=reference.networkBytes*(1+d.networkRelativeTolerance),
      logicalGpuWithinTolerance:candidate.engineLogicalPeakBytes<=reference.engineLogicalPeakBytes*(1+d.engineLogicalGpuRelativeTolerance)});
  }
}
const summaries=['first-door-facade','return-facade-door','revisit-door-facade'].map(phase=>{
  const values=comparisons.filter(p=>p.phase===phase);if(!values.length)return{phase,pairs:0};
  return{phase,pairs:values.length,referenceMedianMs:median(values.map(v=>v.reference.afterInputMs)),candidateMedianMs:median(values.map(v=>v.candidate.afterInputMs)),
    pairedDeltaMedianMs:median(values.map(v=>v.candidate.afterInputMs-v.reference.afterInputMs)),
    referenceNetworkMedian:median(values.map(v=>v.reference.networkBytes)),candidateNetworkMedian:median(values.map(v=>v.candidate.networkBytes))};
});
const out={complete:runs.size===6&&comparisons.length===6&&!failures.length,
  pilotWithinTolerances:runs.size===6&&!failures.length&&comparisons.length===6&&comparisons.every(c=>c.readinessWithinTolerance&&c.networkWithinTolerance&&c.logicalGpuWithinTolerance),
  fullProcessPeakQualified:false,failures,rows,comparisons,summaries};
await writeFile(process.argv[2]??'analysis.json',JSON.stringify(out,null,2),{flag:'wx'});
console.log(JSON.stringify({complete:out.complete,pilotWithinTolerances:out.pilotWithinTolerances,failures,summaries},null,2));
