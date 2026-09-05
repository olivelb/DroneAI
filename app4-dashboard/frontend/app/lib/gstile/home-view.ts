import type { GaussianViewFrame } from "./backend";
import type { Vec3 } from "./contracts";
export type HomeView = { version: 1; target: Vec3; yaw: number; pitch: number; distance: number; fov: number; frame: GaussianViewFrame };
export function decodeHomeView(raw: string): HomeView {
  const v = JSON.parse(raw) as HomeView;
  const vector = (p: unknown): p is Vec3 => Array.isArray(p) && p.length === 3 && p.every(x => typeof x === "number" && Number.isFinite(x));
  if (v.version !== 1 || !vector(v.target) || ![v.yaw,v.pitch,v.distance,v.fov].every(Number.isFinite) ||
      v.distance <= 0 || v.distance > 1e10 || v.fov < 20 || v.fov > 80 ||
      v.frame?.kind !== "facade" || ![v.frame.right,v.frame.up,v.frame.outward].every(vector))
    throw new Error("Vue d'accueil invalide");
  const dot=(a:readonly number[],b:readonly number[])=>a.reduce((s,x,i)=>s+x*b[i],0);
  const {right:a,up:c,outward:d}=v.frame;
  if ([a,c,d].some(p=>Math.abs(dot(p,p)-1)>0.001) || Math.abs(dot(a,c))>0.001 ||
      Math.abs(dot(a,d))>0.001 || Math.abs(dot(c,d))>0.001 ||
      dot([a[1]*c[2]-a[2]*c[1],a[2]*c[0]-a[0]*c[2],a[0]*c[1]-a[1]*c[0]],d)<0.999)
    throw new Error("Repère de vue d'accueil invalide");
  return v;
}
