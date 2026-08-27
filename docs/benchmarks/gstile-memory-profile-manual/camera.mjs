const unit=v=>{const length=Math.hypot(...v);if(!Number.isFinite(length)||length<1e-9)throw Error('Invalid camera vector');return v.map(x=>x/length);};
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const dot=(a,b)=>a.reduce((n,x,i)=>n+x*b[i],0);
export function cameraState(pose,frame,protocol){
 if(!Array.isArray(pose.target)||pose.target.length!==3||!pose.target.every(Number.isFinite)||!Number.isFinite(pose.distance)||pose.distance<=0)throw Error('Invalid pose');
 const z=unit(frame.outward),x=unit(cross(frame.up,z)),y=cross(z,x);
 const position=pose.target.map((v,i)=>v+pose.distance*z[i]);
 return {view:new Float64Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,position),-dot(y,position),-dot(z,position),1]),
  projection:new Float64Array([0,0,0,0,0,1/Math.tan(protocol.fov*Math.PI/360),0,0,0,0,0,0,0,0,0,0]),
  viewportWidth:protocol.width,viewportHeight:protocol.height};
}
export function interpolatePose(a,b,t){
 if(!Number.isFinite(t)||t<0||t>1)throw Error('Invalid interpolation');
 return {target:a.target.map((v,i)=>v+(b.target[i]-v)*t),distance:a.distance+(b.distance-a.distance)*t};
}
export const numericDelta=(a,b)=>Object.fromEntries(Object.keys(b).filter(k=>typeof b[k]==='number').map(k=>[k,b[k]-(a[k]??0)]));
