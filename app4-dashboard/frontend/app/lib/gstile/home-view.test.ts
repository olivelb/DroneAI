import { expect,it } from "vitest";
import { decodeHomeView } from "./home-view";
const valid={version:1,target:[1,2,3],yaw:0,pitch:0,distance:5,fov:42,frame:{kind:"facade",right:[1,0,0],up:[0,0,1],outward:[0,-1,0]}};
it("preserves a model-specific home view",()=>expect(decodeHomeView(JSON.stringify(valid))).toEqual(valid));
it("rejects invalid camera frames and values",()=>{
 expect(()=>decodeHomeView(JSON.stringify({...valid,distance:-1}))).toThrow();
 expect(()=>decodeHomeView(JSON.stringify({...valid,frame:{...valid.frame,up:[1,0,0]}}))).toThrow();
});
