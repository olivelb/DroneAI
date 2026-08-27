export function cadenceSummary(frames, durationMs, policy) {
  const gaps=frames.slice(1).map((f,i)=>f.at-frames[i].at);
  // performance.now() is non-decreasing, not necessarily strictly increasing.
  // Preserve equal timestamps and their zero gaps; reject only backwards time.
  // https://www.w3.org/TR/hr-time-3/#dom-performance-now
  const valid=frames.every(f=>Number.isFinite(f.at)&&f.at>=0&&f.at<=durationMs)&&gaps.every(g=>g>=0);
  const ordered=[...gaps].sort((a,b)=>a-b),middle=Math.floor(ordered.length/2);
  const medianGap=ordered.length?(ordered.length%2?ordered[middle]:(ordered[middle-1]+ordered[middle])/2):null;
  // Include the leading/trailing gaps: missing callbacks at either boundary count too.
  const maxGap=frames.length?Math.max(frames[0].at,durationMs-frames.at(-1).at,...gaps):durationMs;
  const healthy=valid&&Number.isFinite(durationMs)&&durationMs>=policy.durationMs&&
    frames.length>=policy.minimumFrames&&medianGap!==null&&medianGap<policy.maximumMedianGapMs&&maxGap<=policy.maximumGapMs&&
    frames.every(f=>f.focus===true&&f.visibility==='visible');
  return {healthy,count:frames.length,medianGap,maxGap};
}

export function series(pairs) {
  if(!Number.isInteger(pairs)||pairs<1||pairs>20)throw Error('Invalid pair count');
  const runs=[{suffix:'warmup-reference',arm:'reference'},{suffix:'warmup-candidate',arm:'candidate'}];
  for(let pair=1;pair<=pairs;pair++)for(const arm of pair%2?['reference','candidate']:['candidate','reference'])runs.push({suffix:`pair-${pair}-${arm}`,arm});
  return runs;
}

export function runSpec(cohort,index,pairs) {
  if(!/^fg-[a-z0-9-]{1,40}$/.test(cohort))throw Error('Invalid cohort');
  const runs=series(pairs);
  if(!Number.isInteger(index)||index<0||index>=runs.length)throw Error('Invalid series index');
  return {...runs[index],label:`${cohort}-${runs[index].suffix}`,cohort,index,total:runs.length};
}
