import assert from "node:assert/strict";
import { describe, expect, it, vi } from "vitest";
import { allocateGsTilePlayCanvasColumns, decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns } from "./decode";
import { gsTileTextureElementCapacity, resizeGsTileStagingStreams } from "./native-streams";
import { planLinearTextureCopies } from "./merged-arena";
import { GsTileMergedAssembler, gsTileNativeColumnBuffers, gsTileNativeStreamBytes, gsTileNativeResultBuffers, canAssembleGsTileInWorker } from "./merged-assembly";
import { GsTileMergedAssemblyClient, GsTileAssemblyError, type GsTileAssemblyWorker } from "./merged-assembly-client";
import type { GsTileNativeDecodeResult } from "./native-decode";
import type { GsTileAssemblyRequest, GsTileAssemblyResponse } from "./merged-assembly-protocol";

const packed = { color: true, sh: true, transform: true, centerBounds: true };
function source(count: number): GsTileNativeDecodeResult {
  const c = allocateGsTilePlayCanvasColumns(count, packed);
  gsTileNativeColumnBuffers(c).forEach((b, stream) => {
    const data = new Uint8Array(b);
    for (let i = 0; i < data.length; i++) data[i] = (i * 37 + stream * 13) & 255;
  });
  return { count, centerStream: c.centerStream!, transformA: c.transformStreams![0],
    transformB: c.transformStreams![1], colorStream: c.colorStream!, shStreams: c.shStreams!, opacityStreams: c.opacityStreams, bounds: c.bounds };
}
class WorkerStub implements GsTileAssemblyWorker {
  onmessage: GsTileAssemblyWorker["onmessage"] = null;
  onerror: GsTileAssemblyWorker["onerror"] = null;
  onmessageerror: GsTileAssemblyWorker["onmessageerror"] = null;
  messages: GsTileAssemblyRequest[] = [];
  terminate = vi.fn();
  postMessage = (r: GsTileAssemblyRequest, transfer: Transferable[]) => { this.messages.push(structuredClone(r, { transfer })); };
  respond(r: GsTileAssemblyResponse) { this.onmessage?.({ data: r } as MessageEvent<GsTileAssemblyResponse>); }
}

describe("aligned GSTile staging", () => {
  it("resizes empty engine streams once, before native adoption", () => {
    const streams = { textureDimensions: { x:5,y:4 },resize:vi.fn((x:number,y:number)=>{streams.textureDimensions={x,y};}) };
    resizeGsTileStagingStreams(streams,17,7);
    resizeGsTileStagingStreams(streams,17,7);
    expect(streams.resize).toHaveBeenCalledExactlyOnceWith(7,3);
    expect(()=>resizeGsTileStagingStreams(streams,17,0)).toThrow();
    expect(streams.resize).toHaveBeenCalledOnce();
  });

  it("preserves scalar/main-thread decoding bits, centers and bounds in aligned columns", () => {
    const count=7,pack=new ArrayBuffer(32+count*96),view=new DataView(pack);
    new Uint8Array(pack).set(new TextEncoder().encode("GSTILE1\0"));
    view.setUint16(8,1,true);view.setUint16(10,32,true);view.setUint16(12,96,true);view.setUint32(16,count,true);
    for(let i=0;i<count;i++) {view.setInt16(32+i*96+12,32767,true);view.setUint16(32+i*96,i*9123,true);}
    const quantization={position:{min:[0,0,0] as [number,number,number],max:[1,1,1] as [number,number,number]},logScale:{min:[-3,-3,-3] as [number,number,number],max:[-1,-1,-1] as [number,number,number]},rotation:{encoding:"snorm16x4" as const},opacityLogit:{min:-2,max:2},colorDcScale:[1,1,1] as [number,number,number],colorShScale:Array(45).fill(1),opacityShScale:Array(15).fill(1),sourceColorShDegree:3 as const,sourceOpacityShDegree:3 as const};
    const layout={color:true,sh:true,centerBounds:true};
    const a=allocateGsTilePlayCanvasColumns(17,{...layout,textureWidth:7}),b=allocateGsTilePlayCanvasColumns(17,layout);
    for(const c of [a,b])decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(pack,32,count*96,count,quantization,c,2);
    for(const key of ["position","logScale","rotation","centerStream","bounds"] as const)expect(a[key]).toEqual(b[key]);
    const aa=[a.colorStream!,...a.shStreams!,...a.opacityStreams],bb=[b.colorStream!,...b.shStreams!,...b.opacityStreams];
    aa.forEach((data,i)=>{expect(new Uint8Array(data.buffer,0,bb[i].byteLength)).toEqual(new Uint8Array(bb[i].buffer));expect(data.subarray(17*4).every(v=>v===0)).toBe(true);});
  });
  it("allocates explicit padded width without changing square defaults or centers", () => {
    const c = allocateGsTilePlayCanvasColumns(17, { ...packed, textureWidth: 7 });
    expect(c.textureWidth).toBe(7);
    expect(gsTileTextureElementCapacity(17, 7)).toBe(21);
    expect(gsTileNativeColumnBuffers(c).map(b => b.byteLength)).toEqual([204,336,168,168,336,336,336,336,336,336,336,336]);
    expect(gsTileNativeStreamBytes(17, 7)).toBe(17 * 12 + 21 * 160);
    expect(allocateGsTilePlayCanvasColumns(17, packed).textureWidth).toBe(5);
    expect(gsTileTextureElementCapacity(17)).toBe(20);
  });

  it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER])("rejects invalid or overflowing width %s before allocating", textureWidth => {
    expect(() => allocateGsTilePlayCanvasColumns(17, { ...packed, textureWidth })).toThrow();
    expect(() => new GsTileMergedAssembler(17, [17], textureWidth)).toThrow();
  });

  it("counts alignment padding in admission and the destination budget", () => {
    const counts = Array(40).fill(65_536);
    expect(canAssembleGsTileInWorker(3_000_000, counts, 2739)).toBe(true);
    expect(canAssembleGsTileInWorker(3_000_000, counts, 20_000_000)).toBe(false);
    expect(() => new GsTileMergedAssembler(3_000_000, counts, 20_000_000)).toThrow("capacity");
  });

  it("assembles all exact active bytes out of order with independent aligned padding", () => {
    const inputs = [source(2),source(3),source(5)], offsets = [0,2,5];
    const square = new GsTileMergedAssembler(17, [2,3,5]);
    const aligned = new GsTileMergedAssembler(17, [2,3,5], 7);
    for (const i of [2,0,1]) { square.copy(offsets[i],inputs[i]); aligned.copy(offsets[i],inputs[i]); }
    const a = aligned.finish(), b = square.finish();
    expect(a.textureWidth).toBe(7);
    const ab = gsTileNativeColumnBuffers(a), bb = gsTileNativeColumnBuffers(b);
    ab.forEach((buffer,i) => {
      const active = 10 * (i === 0 ? 12 : i === 2 || i === 3 ? 8 : 16);
      expect(new Uint8Array(buffer,0,active)).toEqual(new Uint8Array(bb[i],0,active));
      expect(new Uint8Array(buffer).subarray(active).every(v => v === 0)).toBe(true);
    });
    const s = source(3); s.transformA = new Uint32Array(7 * 4);
    expect(() => gsTileNativeResultBuffers(s)).toThrow("inconsistent");
    a.textureWidth = 5;
    expect(() => gsTileNativeColumnBuffers(a)).toThrow("inconsistent");
  });

  it.each(["missing", "different", "correct"])("validates the returned Worker layout (%s)", async mode => {
    const w = new WorkerStub(), client = new GsTileMergedAssemblyClient(17,[17],new AbortController().signal,()=>w,7);
    expect(w.messages[0]).toMatchObject({ textureWidth: 7 });
    w.respond({ type: "ready", id: 1 }); await client.ready;
    const result = client.finish();
    const c = allocateGsTilePlayCanvasColumns(17,{...packed,textureWidth:7});
    if (mode === "missing") Reflect.deleteProperty(c,"textureWidth");
    if (mode === "different") c.textureWidth = 8;
    const checked = mode === "correct" ? expect(result).resolves.toBe(c) : expect(result).rejects.toBeInstanceOf(GsTileAssemblyError);
    w.respond({ type:"finished",id:2,columns:c }); await checked;
    expect(w.terminate).toHaveBeenCalledOnce();
  });

  it("coalesces equal-width rows into at most six rectangles with exact disjoint coverage", () => {
    for (let w=1;w<=8;w++) for(let so=0;so<4*w;so++) for(let d=0;d<5*w;d++) for(let n=1;n<=Math.min(4*w-so,5*w-d);n++) {
      const plan=planLinearTextureCopies(w,4,so,w,5,d,n),actual=new Int32Array(5*w).fill(-1);
      assert(plan.length<=6);
      for (const c of plan) {
        assert(c.width>0&&c.height>0&&c.sourceX+c.width<=w&&c.destX+c.width<=w&&c.sourceY+c.height<=4&&c.destY+c.height<=5);
        for(let y=0;y<c.height;y++) for(let x=0;x<c.width;x++) {
          const di=(c.destY+y)*w+c.destX+x;
          assert.equal(actual[di],-1);actual[di]=(c.sourceY+y)*w+c.sourceX+x;
        }
      }
      for(let i=0;i<actual.length;i++) assert.equal(actual[i],i>=d&&i<d+n?so+i-d:-1);
    }
    expect(planLinearTextureCopies(2739,2653,0,2739,2739,17,7_265_600).length).toBeLessThanOrEqual(6);
  });
});
