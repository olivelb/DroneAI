import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {readFile,writeFile,appendFile,readdir,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {cpus,release} from 'node:os';

const repo='/home/olivier/droneAI', root=resolve(process.argv[2]);
const python=repo+'/.venv/bin/python';
const source='/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply';
const git=(directory,...args)=>execFileSync('git',args,{cwd:directory,encoding:'utf8'}).trim();
const hash=async path=>{
  const digest=createHash('sha256');
  for await(const bytes of createReadStream(path))digest.update(bytes);
  return digest.digest('hex');
};
const inventory=async (directory,prefix='')=>{
  const files={};
  for(const entry of (await readdir(directory,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){
    assert(!entry.isSymbolicLink());
    const path=resolve(directory,entry.name), name=prefix+entry.name;
    if(entry.isDirectory())Object.assign(files,await inventory(path,name+'/'));
    else {assert(entry.isFile());files[name]=await hash(path);}
  }
  return files;
};
const baseline=resolve(root,'baseline');
const runtime='6dc6a8718766b64c91bc006f861718bb9eb6b5f8';
const baselineCommit='a0434f726f7651a616909c168a70c65d77092830';
const controls=['OPENBLAS_NUM_THREADS','OPENBLAS_DEFAULT_NUM_THREADS','GOTO_NUM_THREADS',
  'OMP_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS','OPENBLAS_THREAD_TIMEOUT'];
const inheritedEnvironment=Object.fromEntries(controls.map(key=>[key,process.env[key]??null]));
assert(controls.every(key=>process.env[key]===undefined),'Explicit inherited BLAS setting: use a new declared protocol');
const numericEvidence='/home/olivier/droneai-qualifications/gstile-blas-timeout-probe-20260828/results.json';
const numericProbe=JSON.parse(await readFile(numericEvidence,'utf8'));
assert(numericProbe.every(t=>t.allFilesIdentical&&t.numericDifferences.length===0&&t.runtime.num_threads===20));
assert.equal(await hash(numericProbe[0].runtime.library),numericProbe[0].runtime.librarySha256);
for(const path of [repo,baseline])assert.equal(git(path,'status','--porcelain'),'');
assert.equal(git(baseline,'rev-parse','HEAD'),baselineCommit);
assert.equal(git(repo,'diff',runtime,'--','app1-colmap/gaussian_tiles','tools'),'');
assert.equal(await hash(source),'c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb');
const orders=[['old','new'],['old','new'],['new','old'],['old','new'],['new','old']];
const protocol={schema:'gstile-blas-timeout-complete-build-v1',runtime,baseline:baselineCommit,
  candidate:git(repo,'rev-parse','HEAD'),source,sourceSha256:await hash(source),
  driverSha256:await hash(new URL(import.meta.url)),
  machine:{cpu:cpus()[0].model,logicalCpus:cpus().length,kernel:release(),node:process.version},
  inheritedEnvironment,numericEvidence,numericEvidenceSha256:await hash(numericEvidence),
  numericRuntime:numericProbe[0].runtime,candidateTimeout:'16',referenceTimeout:null,
  orders,configuration:{leafSize:65536,chunkRecords:131072,lodProxySize:16384,
    lodProxyStrategy:'adaptive-moment',packTargetBytes:2097152,packWorkers:1},
  acceptance:'Ten builds retained: warmups then four AB/BA pairs. Exact all-file parity; unchanged source and clean pinned reports. Median reduction >=3% and all measured pairs faster. No exclusions or cache flush.'};
await mkdir(resolve(root,'results'));
await writeFile(resolve(root,'protocol.json'),JSON.stringify(protocol,null,2)+'\n',{flag:'wx'});
const trials=[],reportHashes={};
let control;
for(const [round,order] of orders.entries()){
  for(const arm of order){
    const label=(round===0?'warmup':`round${round}`)+'-'+arm;
    const output=resolve(root,'results',label),report=output+'.json';
    const implementation=arm==='old'?baseline:repo;
    const args=[repo+'/tools/benchmark_gstile_tiler.py','run',source,output,
      '--implementation-root',implementation,'--report',report,'--leaf-size','65536',
      '--chunk-records','131072','--lod-proxy-size','16384','--lod-proxy-strategy','adaptive-moment',
      '--pack-target-bytes','2097152','--pack-workers','1','--progress-jsonl'];
    console.log(JSON.stringify({started:label}));
    try{
      const stdout=execFileSync(python,args,{cwd:repo,encoding:'utf8',timeout:300000,maxBuffer:16*1024**2});
      await writeFile(output+'.stdout',stdout,{flag:'wx'});
      const data=JSON.parse(await readFile(report,'utf8'));
      assert.equal(data.implementation.dirty,false);
      assert.equal(data.implementation.commit,arm==='old'?protocol.baseline:protocol.candidate);
      assert.equal(data.fixture.sha256,protocol.sourceSha256);
      assert.deepEqual(data.configuration.inheritedBlasEnvironment,inheritedEnvironment);
      assert.equal(data.result.openblas_thread_timeout,arm==='new'?'16':undefined);
      const files=await inventory(output);
      if(!control)control=files;
      assert.deepEqual(files,control,`${label}: different complete bundle`);
      if(label!=='warmup-old')assert.equal(execFileSync('diff',['-rq',resolve(root,'results/warmup-old'),output],{encoding:'utf8'}),'');
      await writeFile(output+'.inventory.json',JSON.stringify(files,null,2)+'\n',{flag:'wx'});
      reportHashes[label]=await hash(report);
      const trial={label,round,arm,warmup:round===0,...data.measurements,
        configuredTimeout:data.result.openblas_thread_timeout??null,
        bundleId:data.result.bundle_id,fileCount:Object.keys(files).length};
      trials.push(trial);
      await appendFile(resolve(root,'trials.jsonl'),JSON.stringify(trial)+'\n');
      console.log(JSON.stringify(trial));
    }catch(error){
      await writeFile(output+'.failure.json',JSON.stringify({message:error.message,
        stdout:String(error.stdout??''),stderr:String(error.stderr??'')},null,2),{flag:'wx'});
      throw error;
    }
  }
}
const median=values=>{const s=[...values].sort((a,b)=>a-b);return (s[1]+s[2])/2;};
const old=trials.filter(t=>t.arm==='old'&&!t.warmup), fresh=trials.filter(t=>t.arm==='new'&&!t.warmup);
const oldMedian=median(old.map(t=>t.wallSeconds)),newMedian=median(fresh.map(t=>t.wallSeconds));
const paired=fresh.map(t=>({round:t.round,reduction:1-t.wallSeconds/old.find(o=>o.round===t.round).wallSeconds}));
assert.equal(await hash(source),protocol.sourceSha256);
for(const path of [repo,baseline])assert.equal(git(path,'status','--porcelain'),'');
assert.equal(git(repo,'rev-parse','HEAD'),protocol.candidate);
assert.equal(git(baseline,'rev-parse','HEAD'),protocol.baseline);
const accepted=1-newMedian/oldMedian>=0.03&&paired.every(p=>p.reduction>0);
const summary={complete:true,accepted,allFilesIdentical:true,binaryComparisons:9,oldMedian,newMedian,
  reduction:1-newMedian/oldMedian,paired,
  oldPeakRssKiB:Math.max(...old.map(t=>t.maximumRssKiB)),newPeakRssKiB:Math.max(...fresh.map(t=>t.maximumRssKiB))};
await writeFile(resolve(root,'verified-results.json'),JSON.stringify({protocol,trials,summary,control,reportHashes},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(summary,null,2));
if(!accepted)process.exitCode=2;
