import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {cadenceSummary} from './controls.mjs';

test('protocol adds five seconds without weakening cadence or changing source arms',async()=>{
  const p=JSON.parse(await readFile(new URL('./protocol.json',import.meta.url),'utf8'));
  assert.equal(p.stabilizationMs,5000);
  assert.equal(p.schema,'gstile-memory-profile-pilot-v2');
  assert.equal(p.reference,p.candidate);
  assert.deepEqual(p.cadence,{durationMs:6000,minimumFrames:180,maximumMedianGapMs:40,maximumGapMs:250});
  assert.deepEqual(p.runs.map(r=>r.arm),['reference','candidate','reference','candidate','candidate','reference']);
});

function clock(fail=false){
  let time=100,callback,id=0;
  const waits=[],cancelled=[];
  return {waits,cancelled,now:()=>time,
    requestFrame:fn=>{callback=fn;return ++id;},cancelFrame:frame=>cancelled.push(frame),
    state:()=>({focus:true,visibility:'visible'}),
    wait:async ms=>{waits.push(ms);if(fail)throw Error('timer failed');time=110;callback();time=100+ms;},
  };
}
test('samples a single fixed window and cancels its final animation callback',async()=>{
  const {collectIdleWindow}=await import('./idle-window.mjs'),c=clock();
  const value=await collectIdleWindow(5000,c);
  assert.deepEqual(c.waits,[5000]);assert.deepEqual(c.cancelled,[2]);
  assert.deepEqual(value,{start:100,durationMs:5000,samples:[{at:10,focus:true,visibility:'visible'}]});
});
test('cancels the callback even if waiting fails, without retrying',async()=>{
  const {collectIdleWindow}=await import('./idle-window.mjs'),c=clock(true);
  await assert.rejects(collectIdleWindow(5000,c),/timer failed/);
  assert.deepEqual(c.waits,[5000]);assert.deepEqual(c.cancelled,[1]);
});
test('rejects invalid window durations before scheduling anything',async()=>{
  const {collectIdleWindow}=await import('./idle-window.mjs'),c=clock();
  for(const value of [0,-1,NaN,Infinity,0.5,60001])await assert.rejects(collectIdleWindow(value,c));
  assert.deepEqual(c.waits,[]);assert.deepEqual(c.cancelled,[]);
});
test('requires matching, full, foreground stabilization before the control',async()=>{
  const {stabilizationMatchesControl}=await import('./idle-window.mjs');
  const window={name:'avant',start:100,durationMs:5000,samples:[{at:100,focus:true,visibility:'visible'}]};
  const control={name:'avant',start:5101};
  assert(stabilizationMatchesControl(window,control,5000));
  for(const change of [{name:'après'},{durationMs:4999},{start:102},{samples:[{at:100,focus:false,visibility:'visible'}]},
    {samples:[{at:100,focus:true,visibility:'hidden'}]},{samples:[{at:5001,focus:true,visibility:'visible'}]}])
    assert(!stabilizationMatchesControl({...window,...change},control,5000));
  assert(!stabilizationMatchesControl(undefined,control,5000));
});
test('a 253.3 ms gap still fails the following six-second control',async()=>{
  const {stabilizationMatchesControl}=await import('./idle-window.mjs');
  const policy={durationMs:6000,minimumFrames:180,maximumMedianGapMs:40,maximumGapMs:250};
  const samples=Array.from({length:800},(_,i)=>({at:i<50?i*7:i*7+246.3,focus:true,visibility:'visible'}));
  const window={name:'avant',start:0,durationMs:5000,samples:[{at:253.3,focus:true,visibility:'visible'}]};
  assert(stabilizationMatchesControl(window,{name:'avant',start:5000},5000));
  assert.equal(cadenceSummary(samples,6000,policy).healthy,false);
});
