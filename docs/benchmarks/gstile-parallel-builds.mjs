import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFile,writeFile,appendFile,readdir,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';

const repo='/home/olivier/droneAI',root=resolve(process.argv[2]);
const python=`${repo}/.venv/bin/python`,tool=`${repo}/tools/benchmark_gstile_tiler.py`;
const git=(...args)=>execFileSync('git',args,{cwd:repo,encoding:'utf8'}).trim();
assert.equal(git('status','--porcelain'),'','Use a clean candidate');
const runtime='1872c59418867eacbc8274faf81b784263483af0';
assert.equal(git('diff',runtime,'--','app1-colmap/gaussian_tiles','tools/build_gstiles.py'),'','Pinned runtime changed');
const source=resolve(root,'source.ply');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
assert.equal(sha(await readFile(source)),'c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb');
const protocol={schema:'gstile-complete-parallel-build-v1',candidate:git('rev-parse','HEAD'),runtime,
  baseline:'0ff7ab0531c017c8e84219f5e205ff572ed11087',source,sourceSha256:sha(await readFile(source)),
  driverSha256:sha(await readFile(new URL(import.meta.url))),
  arms:['old','one','two','four'],orders:[['old','one','two','four'],['four','two','one','old'],['one','old','four','two']],
  profiles:{individual:[],v4aggregate:['--lod-proxy-size','16384','--lod-proxy-strategy','adaptive-moment','--pack-target-bytes','2097152']},
  policy:'One warmup per arm/profile; retain all three measured rounds. No cache flush. Exact complete bundle parity required. Worker default remains one.'};
await mkdir(resolve(root,'results'));
await writeFile(resolve(root,'protocol.json'),JSON.stringify(protocol,null,2),{flag:'wx'});
const inventory=async directory=>{
  const entries={};
  for(const name of (await readdir(directory,{recursive:true})).sort()){
    if(!name.endsWith('.gst')&&!name.endsWith('.zst')&&name!=='manifest.json')continue;
    entries[name]=sha(await readFile(resolve(directory,name)));
  }
  return entries;
};
const rows=[];
for(const [profile,extra] of Object.entries(protocol.profiles)){
  let control;
  for(const [round,order] of [protocol.arms,...protocol.orders].entries()){
    for(const arm of order){
      const label=`${profile}-${round===0?'warmup':`round${round}`}-${arm}`;
      const output=resolve(root,'results',label),report=output+'.json';
      const implementation=arm==='old'?resolve(root,'baseline'):repo;
      const args=[tool,'run',source,output,'--implementation-root',implementation,'--report',report,
        '--leaf-size','65536','--chunk-records','131072','--progress-jsonl',...extra];
      if(arm!=='old')args.push('--pack-workers',String({one:1,two:2,four:4}[arm]),'--pack-pending-bytes','134217728');
      console.log(JSON.stringify({started:label}));
      try{
        const stdout=execFileSync(python,args,{cwd:repo,encoding:'utf8',timeout:300000,maxBuffer:16*1024**2});
        await writeFile(output+'.stdout',stdout,{flag:'wx'});
      }catch(error){
        await writeFile(output+'.failure.json',JSON.stringify({message:error.message,stdout:String(error.stdout??''),stderr:String(error.stderr??'')},null,2),{flag:'wx'});
        throw error;
      }
      const data=JSON.parse(await readFile(report,'utf8'));
      const files=await inventory(output);
      if(!control)control=files;
      assert.deepEqual(files,control,`${label}: output bytes differ`);
      const telemetry=data.stderr.trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));
      const queue=telemetry.find(e=>e.event==='pack_preparation')??null;
      if(arm!=='old'){
        assert(queue);assert(queue.peakPendingBytes<=134217728);assert(queue.peakPendingTasks<=queue.workers);
      }
      const row={label,profile,round,arm,warmup:round===0,...data.measurements,bundleId:data.result.bundle_id,
        fileCount:Object.keys(files).length,queue,inventorySha256:sha(JSON.stringify(files))};
      rows.push(row);
      await writeFile(output+'.inventory.json',JSON.stringify(files,null,2),{flag:'wx'});
      await appendFile(resolve(root,'trials.jsonl'),JSON.stringify(row)+'\n');
      console.log(JSON.stringify(row));
    }
  }
}
const median=a=>[...a].sort((a,b)=>a-b)[Math.floor(a.length/2)];
const summaries=[];
for(const profile of Object.keys(protocol.profiles)){
  const baseline=median(rows.filter(r=>r.profile===profile&&r.arm==='old'&&!r.warmup).map(r=>r.wallSeconds));
  for(const arm of protocol.arms){
    const samples=rows.filter(r=>r.profile===profile&&r.arm===arm&&!r.warmup);
    const seconds=median(samples.map(r=>r.wallSeconds));
    summaries.push({profile,arm,medianSeconds:seconds,reductionVsOld:1-seconds/baseline,
      peakChildRssKiB:Math.max(...samples.map(r=>r.maximumRssKiB))});
  }
}
await writeFile(resolve(root,'summary.json'),JSON.stringify({complete:true,allBundlesIdentical:true,summaries},null,2),{flag:'wx'});
console.log(JSON.stringify(summaries,null,2));
