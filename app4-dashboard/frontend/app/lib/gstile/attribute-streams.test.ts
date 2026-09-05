import { expect, it } from "vitest";
import { crc32 } from "./decode";
import { validateAttributeStream, assembleAttributeTile } from "./attribute-streams";
function stream(kind:"base"|"sh", count=3) {
  const stride=kind==="base"?36:60, b=new ArrayBuffer(32+count*stride), bytes=new Uint8Array(b), v=new DataView(b);
  bytes.set(new TextEncoder().encode("GSATTR1\0")); v.setUint16(8,1,true);v.setUint16(10,32,true);
  v.setUint16(12,stride,true);v.setUint16(14,kind==="base"?1:2,true);v.setUint32(16,count,true);
  for(let i=32;i<bytes.length;i++)bytes[i]=(i*37)%256;
  v.setUint32(28,crc32(bytes.subarray(32)),true);return b;
}
it("decodes only the requested aggregate tile, preserving base, IDs and all SH",()=>{
 const base=stream("base"),sh=stream("sh");
 validateAttributeStream(base,"base",3);validateAttributeStream(sh,"sh",3);
 const full=new Uint8Array(assembleAttributeTile(base,sh,{recordCount:3},1,1));
 const zero=new Uint8Array(assembleAttributeTile(base,null,{recordCount:3},1,1));
 expect(full.slice(32,60)).toEqual(new Uint8Array(base).slice(68,96));
 expect(full.slice(120,128)).toEqual(new Uint8Array(base).slice(96,104));
 expect(full.slice(60,120)).toEqual(new Uint8Array(sh).slice(92,152));
 expect(zero.slice(60,120)).toEqual(new Uint8Array(60));
});
it("rejects corrupt, wrong-kind and out-of-range streams",()=>{
 const base=stream("base");new Uint8Array(base)[40]^=1;
 expect(()=>validateAttributeStream(base,"base",3)).toThrow();
 expect(()=>validateAttributeStream(stream("sh"),"base",3)).toThrow();
 expect(()=>assembleAttributeTile(stream("base"),null,{recordCount:3},3,1)).toThrow();
});
