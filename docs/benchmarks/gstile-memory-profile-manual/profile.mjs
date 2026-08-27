export function memoryProfile(protocol, arm) {
  const ram=protocol.schema==='gstile-memory-profile-pilot-v2';
  const halo=protocol.schema==='gstile-stale-halo-pilot-v1';
  if((!ram&&!halo)||protocol.stabilizationMs!==5000||
    ![protocol.reference,protocol.candidate].every(x=>/^[a-f0-9]{40}$/.test(x))||
    (ram?protocol.reference!==protocol.candidate:protocol.reference===protocol.candidate))
    throw Error('Invalid pinned comparison protocol');
  const expected=arm==='reference'?(ram?['standard',805306368]:['desktop',1610612736]):arm==='candidate'?['desktop',1610612736]:null;
  const value=protocol.memoryProfiles?.[arm];
  if(!expected||value?.profile!==expected[0]||value?.bytes!==expected[1])throw Error('Invalid frozen memory profile');
  return value;
}

export function runtimeDifferences(protocol, provenance) {
  memoryProfile(protocol,'reference');memoryProfile(protocol,'candidate');
  if(provenance.arms.reference!==protocol.reference||provenance.arms.candidate!==protocol.candidate)
    throw Error('Runtime commits differ from protocol');
  const files=Object.keys(provenance.modules).filter(x=>x.startsWith('reference/')).sort();
  if(Object.keys(provenance.modules).length!==2*files.length||files.length===0)
    throw Error('Mismatched module sets');
  const changed=[];
  for(const file of files){
    const a=provenance.modules[file],b=provenance.modules[file.replace('reference/','candidate/')];
    if(!b)throw Error('Missing candidate module');
    if(a.original!==b.original)changed.push(file.slice('reference/'.length));
    else if(a.instrumented!==b.instrumented||a.compiled!==b.compiled)throw Error('Unrelated instrumentation differs');
  }
  const expected=protocol.schema==='gstile-stale-halo-pilot-v1'
    ?['gstile/lod-prefetch.ts','gstile/playcanvas-backend.ts']:[];
  if(JSON.stringify(changed)!==JSON.stringify(expected))throw Error('Unexpected runtime differences');
  return changed;
}
