/** Fixed-deadline sampling; never extends a window to obtain healthy cadence. */
export async function collectIdleWindow(milliseconds, runtime) {
  if(!Number.isSafeInteger(milliseconds)||milliseconds<1||milliseconds>60000)
    throw Error('Invalid idle window duration');
  const start=runtime.now(),samples=[];let raf;
  const tick=()=>{
    samples.push({at:runtime.now()-start,...runtime.state()});
    raf=runtime.requestFrame(tick);
  };
  try{
    raf=runtime.requestFrame(tick);
    // An early timer callback must not shorten the predeclared window.
    do{await runtime.wait(Math.max(0,start+milliseconds-runtime.now()));}
    while(runtime.now()-start<milliseconds);
    return {start,durationMs:runtime.now()-start,samples};
  }finally{runtime.cancelFrame(raf);}
}

export function stabilizationMatchesControl(window,control,milliseconds){
  return !!window&&window.name===control.name&&Number.isFinite(window.start)&&window.start>=0&&
    Number.isFinite(window.durationMs)&&window.durationMs>=milliseconds&&
    window.start+window.durationMs<=control.start&&Array.isArray(window.samples)&&
    window.samples.every((sample,i)=>Number.isFinite(sample.at)&&sample.at>=0&&sample.at<=window.durationMs&&
      (i===0||sample.at>=window.samples[i-1].at)&&sample.focus===true&&sample.visibility==='visible');
}
