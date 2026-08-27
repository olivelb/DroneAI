import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {memoryProfile} from './profile.mjs';
const protocol=JSON.parse(await readFile(new URL('./protocol.json',import.meta.url),'utf8'));
test('same code, fixed per-arm caps and explicit opt-in',()=>{
  assert.deepEqual(memoryProfile(protocol,'reference'),{profile:'standard',bytes:768*1024**2});
  assert.deepEqual(memoryProfile(protocol,'candidate'),{profile:'desktop',bytes:1536*1024**2});
});
test('refuses mixed code, a swapped profile, a mismatched cap or an unknown arm',()=>{
  assert.throws(()=>memoryProfile({...protocol,candidate:'different'},'candidate'));
  for(const profile of [{profile:'standard',bytes:1536*1024**2},{profile:'desktop',bytes:768*1024**2}])
    assert.throws(()=>memoryProfile({...protocol,memoryProfiles:{...protocol.memoryProfiles,candidate:profile}},'candidate'));
  assert.throws(()=>memoryProfile(protocol,'unknown'));
});
