import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {readFile,writeFile,readdir} from 'node:fs/promises';
import {resolve} from 'node:path';

// Post-cohort verification only: never run this alongside timed builds.
const root=resolve(process.argv[2]);
const digest=async path=>{
  const hash=createHash('sha256');
  for await(const bytes of createReadStream(path))hash.update(bytes);
  return hash.digest('hex');
};
const json=async name=>JSON.parse(await readFile(resolve(root,name),'utf8'));
const protocol=await json('protocol.json');
const summary=await json('summary.json');
assert.equal(summary.complete,true);
const trials=(await readFile(resolve(root,'trials.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
assert.equal(trials.length,32);
assert.equal(new Set(trials.map(row=>row.label)).size,32);
assert.equal(await digest(protocol.source),protocol.sourceSha256);
assert.equal(await digest(resolve(root,'gstile-parallel-builds.mjs')),protocol.driverSha256);
const walk=async (directory,prefix='')=>{
  const files=[];
  for(const entry of await readdir(directory,{withFileTypes:true})){
    const name=prefix+entry.name;
    assert(!entry.isSymbolicLink(),`Unexpected symlink: ${name}`);
    if(entry.isDirectory())files.push(...await walk(resolve(directory,entry.name),name+'/'));
    else {assert(entry.isFile(),`Unexpected entry: ${name}`);files.push(name);}
  }
  return files.sort();
};
const controls=new Map();
const reportHashes={};
for(const row of trials){
  const stem='results/'+row.label;
  const directory=resolve(root,stem);
  const recorded=await json(stem+'.inventory.json');
  const names=await walk(directory);
  assert.deepEqual(names,Object.keys(recorded).sort(),`${row.label}: omitted or unexpected files`);
  const actual={};
  for(const name of names)actual[name]=await digest(resolve(directory,name));
  assert.deepEqual(actual,recorded,`${row.label}: changed bytes`);
  if(!controls.has(row.profile))controls.set(row.profile,actual);
  assert.deepEqual(actual,controls.get(row.profile),`${row.label}: baseline parity`);
  const report=await json(stem+'.json');
  assert.equal(report.implementation.dirty,false,row.label);
  assert.equal(report.implementation.commit,row.arm==='old'?protocol.baseline:protocol.candidate,row.label);
  assert.equal(report.fixture.sha256,protocol.sourceSha256,row.label);
  assert.equal(report.configuration.leafSize,65536,row.label);
  assert.equal(report.configuration.chunkRecords,131072,row.label);
  assert.equal(report.result.bundle_id,row.bundleId,row.label);
  assert.equal(report.measurements.wallSeconds,row.wallSeconds,row.label);
  reportHashes[row.label]=await digest(resolve(root,stem+'.json'));
}
const result={
  schema:'gstile-parallel-build-post-cohort-audit-v1',
  allFilesVerified:true,noUnexpectedFiles:true,allReportsClean:true,
  protocol,summary,trials,reportHashes,
  evidenceRoot:root,
  verifierSha256:await digest(new URL(import.meta.url)),
};
await writeFile(resolve(root,'verified-results.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({verified:trials.length,files:trials.reduce((sum,row)=>sum+row.fileCount,0),summary},null,2));
