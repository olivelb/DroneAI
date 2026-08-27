import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const hash=x=>createHash('sha256').update(x).digest('hex');
const provenance=JSON.parse(await readFile('provenance.json','utf8'));
assert.equal(provenance.arms.reference,provenance.arms.candidate);
for(const [name,entry] of Object.entries(provenance.modules)){
  assert.equal(hash(await readFile(`${name}.original`)),entry.original);
  assert.equal(hash(await readFile(name)),entry.instrumented);
  assert.equal(hash(await readFile(name.replace(/\.ts$/,'.js'))),entry.compiled);
  if(name.startsWith('reference/'))assert.deepEqual(entry,provenance.modules[name.replace('reference/','candidate/')]);
}
assert.equal(provenance.modules['reference/gstile/playcanvas-backend.ts'].instrumented,
  provenance.modules['candidate/gstile/playcanvas-backend.ts'].instrumented);
for(const [name,expected] of Object.entries(provenance.engineInputs))assert.equal(hash(await readFile(`engine-source/${name}`)),expected);
const files=['provenance.json','protocol.json','descriptor-original.json','harness.mjs','server.mjs','index.html','camera.mjs','camera.test.mjs','controls.mjs','controls.test.mjs','prepare.mjs','freeze.mjs','analyze.mjs','profile.mjs','profile.test.mjs','idle-window.mjs','idle-window.test.mjs'];
const checks={};
for(const name of files)checks[name]=hash(await readFile(name));
await writeFile('frozen.json',JSON.stringify(checks,null,2),{flag:'wx'});
console.log('Sources, engine, protocol and harness frozen and verified');
