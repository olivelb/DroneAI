import test from 'node:test';
import assert from 'node:assert/strict';
import {memoryProfile,runtimeDifferences} from './profile.mjs';
const protocol={schema:'gstile-memory-profile-pilot-v2',reference:'a'.repeat(40),candidate:'a'.repeat(40),
  stabilizationMs:5000,memoryProfiles:{reference:{profile:'standard',bytes:768*1024**2},candidate:{profile:'desktop',bytes:1536*1024**2}}};
test('same code, fixed per-arm caps and explicit opt-in',()=>{
  assert.deepEqual(memoryProfile(protocol,'reference'),{profile:'standard',bytes:768*1024**2});
  assert.deepEqual(memoryProfile(protocol,'candidate'),{profile:'desktop',bytes:1536*1024**2});
});
const halo={...protocol,schema:'gstile-stale-halo-pilot-v1',candidate:'b'.repeat(40),
  memoryProfiles:{reference:protocol.memoryProfiles.candidate,candidate:protocol.memoryProfiles.candidate}};
test('halo comparison holds memory constant and requires distinct pinned commits',()=>{
  for(const arm of ['reference','candidate'])assert.deepEqual(memoryProfile(halo,arm),protocol.memoryProfiles.candidate);
  assert.throws(()=>memoryProfile({...halo,candidate:halo.reference},'candidate'));
  assert.throws(()=>memoryProfile({...halo,memoryProfiles:protocol.memoryProfiles},'reference'));
  assert.throws(()=>memoryProfile({...halo,reference:'main'},'reference'));
});
const provenance=(p,changed)=>{
  const modules={};
  for(const name of ['gstile/lod-prefetch.ts','gstile/playcanvas-backend.ts','gstile/range-source.ts']){
    modules['reference/'+name]={original:'a',instrumented:'a',compiled:'a'};
    modules['candidate/'+name]=changed.includes(name)?{original:'b',instrumented:'b',compiled:'b'}:{...modules['reference/'+name]};
  }
  return {arms:{reference:p.reference,candidate:p.candidate},modules};
};
test('freeze permits exactly the two halo runtime changes, or identical RAM arms',()=>{
  const allowed=['gstile/lod-prefetch.ts','gstile/playcanvas-backend.ts'];
  assert.deepEqual(runtimeDifferences(halo,provenance(halo,allowed)),allowed);
  assert.deepEqual(runtimeDifferences(protocol,provenance(protocol,[])),[]);
  for(const changed of [[],[allowed[0]],[...allowed,'gstile/range-source.ts']])
    assert.throws(()=>runtimeDifferences(halo,provenance(halo,changed)));
  assert.throws(()=>runtimeDifferences(protocol,provenance(protocol,allowed)));
  const corrupt=provenance(halo,allowed);
  corrupt.modules['candidate/gstile/range-source.ts'].compiled='different';
  assert.throws(()=>runtimeDifferences(halo,corrupt));
});
test('refuses mixed code, a swapped profile, a mismatched cap or an unknown arm',()=>{
  assert.throws(()=>memoryProfile({...protocol,candidate:'different'},'candidate'));
  for(const profile of [{profile:'standard',bytes:1536*1024**2},{profile:'desktop',bytes:768*1024**2}])
    assert.throws(()=>memoryProfile({...protocol,memoryProfiles:{...protocol.memoryProfiles,candidate:profile}},'candidate'));
  assert.throws(()=>memoryProfile(protocol,'unknown'));
});
