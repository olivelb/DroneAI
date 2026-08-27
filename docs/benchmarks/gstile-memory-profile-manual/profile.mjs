export function memoryProfile(protocol, arm) {
  if(protocol.schema!=='gstile-memory-profile-pilot-v2'||protocol.reference!==protocol.candidate||protocol.stabilizationMs!==5000)
    throw Error('Expected a same-code RAM comparison');
  const expected=arm==='reference'?['standard',805306368]:arm==='candidate'?['desktop',1610612736]:null;
  const value=protocol.memoryProfiles?.[arm];
  if(!expected||value?.profile!==expected[0]||value?.bytes!==expected[1])throw Error('Invalid frozen memory profile');
  return value;
}
