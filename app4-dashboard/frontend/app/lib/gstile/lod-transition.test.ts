import { describe, it, expect } from "vitest";
import { nextLodCut } from "./lod-transition";
import type { GsTileNode } from "./contracts";
const nodes = [
  { id: "root", children: ["west", "east"] },
  { id: "west", children: ["a", "b"] }, { id: "east", children: ["c", "d"] },
  ...["a","b","c","d"].map(id => ({id})),
].map(n => ({...n, tile: {recordCount: 10}})) as unknown as GsTileNode[];
const coverage: Record<string,string[]> = { root:["a","b","c","d"],west:["a","b"],east:["c","d"],a:["a"],b:["b"],c:["c"],d:["d"] };
describe("local replacement transitions", () => {
  it("converges under bounded work while preserving complete non-overlapping coverage", () => {
    let cut = ["root"];
    for(let i=0;i<10;i++) {
      const step=nextLodCut(nodes,cut,["a","b","c","d"],40,20);
      expect(step.added).toBeLessThanOrEqual(20);
      expect(step.ids.flatMap(id=>coverage[id]).sort()).toEqual(["a","b","c","d"]);
      cut=step.ids;
      if(step.complete) break;
    }
    expect(cut).toEqual(["a","b","c","d"]);
  });
  it("reverses a zoom and shrinks the budget without deadlock", () => {
    expect(nextLodCut(nodes,["a","b","c","d"],["root"],10).ids).toEqual(["root"]);
    expect(nextLodCut(nodes,["a","b","c","d"],["west","east"],20).ids).toEqual(["root"]);
    expect(nextLodCut(nodes,["root"],["west","east"],20).complete).toBe(true);
  });
  it("handles disjoint visible regions and starts with whole-scene coverage", () => {
    expect(nextLodCut(nodes,[],["a","c"],40).ids).toEqual(["root"]);
    expect(nextLodCut(nodes,["a","b"],["c","d"],40).ids).toEqual(["c","d"]);
    expect(nextLodCut(nodes,["root"],[],40).ids).toEqual([]);
  });
});

it("skips oversized intermediate proxies when final descendants fit the budget",()=>{
 const expensive=nodes.map(n=>({...n,tile:{recordCount:n.id==="root"?10:n.children?25:8}})) as unknown as GsTileNode[];
 expect(nextLodCut(expensive,["root"],["a","b","c","d"],32).complete).toBe(true);
});
