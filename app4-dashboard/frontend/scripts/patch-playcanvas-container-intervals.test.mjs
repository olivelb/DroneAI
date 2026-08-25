import { describe, expect, it } from "vitest";
import { runInNewContext } from "node:vm";
import {
  patchPlayCanvasContainerIntervalInfoSource,
  patchPlayCanvasContainerIntervalRenderSource,
  patchPlayCanvasContainerIntervalWorldSource,
  patchPlayCanvasContainerIntervalsBundleTypes,
  patchPlayCanvasContainerIntervalsSource,
  patchPlayCanvasContainerIntervalsTypes,
} from "./patch-playcanvas-container-intervals.mjs";

describe("PlayCanvas GSplat container interval patch", () => {
  it("patches JS once and remains idempotent", () => {
    const source = "class GSplatComponent {\n\thide() {\n\t}\n}";
    const patched = patchPlayCanvasContainerIntervalsSource(source);
    expect(patched).toContain("setActiveSplatIntervals(intervals)");
    expect(patched).toContain("DroneAI conservative interval spheres v2");
    expect(patched).toContain("placement.intervalBounds = intervalBounds");
    expect(patchPlayCanvasContainerIntervalsSource(patched)).toBe(patched);
  });

  it("stores a conservative sphere for every validated interval", () => {
    const source = "class GSplatComponent {\n\thide() {\n\t}\n}";
    const patched = patchPlayCanvasContainerIntervalsSource(source);
    const Component = runInNewContext(`${patched}\nGSplatComponent`);
    const placement = {
      intervals: new Map(),
      intervalBounds: null,
      dirty: false,
      markDirty() {
        this.dirty = true;
      },
    };
    const layer = { gsplatPlacementsDirty: false };
    const component = new Component();
    component._placement = placement;
    component.resource = { maxSplats: 8 };
    component._layers = [0];
    component.system = {
      app: { scene: { layers: { getLayerById: () => layer } } },
    };

    const bounds = { min: [-3, -2, -1], max: [4, 6, 8] };
    component.setActiveSplatIntervals([
      { start: 0, count: 8, bounds },
    ]);
    expect(placement.intervals.get(0)).toEqual({ x: 0, y: 7 });
    expect(placement.dirty).toBe(true);
    expect(layer.gsplatPlacementsDirty).toBe(true);
    const [x, y, z, radius] = placement.intervalBounds;
    for (const cx of [bounds.min[0], bounds.max[0]]) {
      for (const cy of [bounds.min[1], bounds.max[1]]) {
        for (const cz of [bounds.min[2], bounds.max[2]]) {
          expect(Math.hypot(cx - x, cy - y, cz - z)).toBeLessThanOrEqual(
            radius,
          );
        }
      }
    }
    expect(() =>
      component.setActiveSplatIntervals([
        {
          start: 0,
          count: 8,
          bounds: { min: [1, 0, 0], max: [0, 1, 1] },
        },
      ]),
    ).toThrow("bounds are invalid");
    expect(placement.intervals.size).toBe(0);
    expect(placement.intervalBounds).toBeNull();
  });

  it("patches the public type contract once", () => {
    const source = "export class GSplatComponent {\n    hide(): void;\n}";
    const patched = patchPlayCanvasContainerIntervalsTypes(source);
    expect(patched).toContain(
      "count: number; bounds: { min: readonly [number, number, number]",
    );
    expect(patchPlayCanvasContainerIntervalsTypes(patched)).toBe(patched);
  });

  it("patches only GSplatComponent in the aggregate declarations", () => {
    const source = `declare class OtherComponent {\n    hide(): void;\n}\ndeclare class GSplatComponent extends Component {\n    hide(): void;\n}\n`;
    const patched = patchPlayCanvasContainerIntervalsBundleTypes(source);
    expect(patched.split("setActiveSplatIntervals")).toHaveLength(2);
    expect(patched.indexOf("setActiveSplatIntervals")).toBeGreaterThan(
      patched.indexOf("class GSplatComponent"),
    );
    expect(patchPlayCanvasContainerIntervalsBundleTypes(patched)).toBe(patched);
  });

  it("supports non-octree bounds, contiguous offsets and full uploads", () => {
    const info = `class GSplatInfo {
\tintervalNodeIndices = [];
\tconstructor(placement, nodeInfos) {
\t\tthis.nodeInfos = nodeInfos;
\t\tthis.updateIntervals(placement.intervals);
\t}
\tupdateIntervals(intervals) {
\t\tif (intervals.size > 0) {
\t\t\tif (this.octreeNodes) {
\t\t\t\tthis.activeSplats = totalCount;
\t\t\t\tthis.numBoundsEntries = this.octreeNodes.length;
\t\t\t} else if (totalCount === this.numSplats) {
\t\t\t\tthis.intervals.length = 0;
\t\t\t} else {
\t\t\t\tthis.activeSplats = totalCount;
\t\t\t}
\t\t}
\t}
\twriteBoundsSpheres(data, offset) {
\t\tif (this.octreeNodes) {
\t\t} else {
\t\t\tconst aabb = this.resource.aabb;
\t\t\tconst he = aabb.halfExtents;
\t\t\tconst r = Math.sqrt(he.x * he.x + he.y * he.y + he.z * he.z);
\t\t\tdata[offset++] = aabb.center.x;
\t\t\tdata[offset++] = aabb.center.y;
\t\t\tdata[offset++] = aabb.center.z;
\t\t\tdata[offset++] = r;
\t\t}
\t}
}`;
    const patchedInfo = patchPlayCanvasContainerIntervalInfoSource(info);
    expect(patchedInfo).toContain("placement.intervalBounds ?? null");
    expect(patchedInfo).toContain(
      "DroneAI non-octree per-interval bounds",
    );
    expect(patchedInfo).toContain("this.numBoundsEntries = intervals.size");
    expect(patchedInfo).toContain("data.set(this.intervalBounds, offset)");
    expect(patchPlayCanvasContainerIntervalInfoSource(patchedInfo)).toBe(
      patchedInfo,
    );
    const legacyInfo = info.replace(
      "\t\t\t\tthis.activeSplats = totalCount;\n\t\t\t}\n\t\t}",
      "\t\t\t\tthis.activeSplats = totalCount;\n\t\t\t\t// DroneAI non-octree interval bounds\n\t\t\t\tthis.numBoundsEntries = 1;\n\t\t\t}\n\t\t}",
    );
    expect(legacyInfo).not.toBe(info);
    expect(patchPlayCanvasContainerIntervalInfoSource(legacyInfo)).toContain(
      "DroneAI non-octree per-interval bounds",
    );

    const world = `\n\tconst block = allocationMap.get(splat.allocId);\n\tif (block) {\n\t\tintervalOffsets.push(block.offset);\n\t}\n`;
    const patchedWorld = patchPlayCanvasContainerIntervalWorldSource(world);
    expect(patchedWorld).toContain("let intervalOffset = block.offset");
    expect(patchedWorld).toContain("intervalOffset += intervals[j * 2 + 1]");
    expect(patchedWorld).toContain(
      "\n\t\t\tlet intervalOffset = block.offset;\n\t\t\tfor (let j = 0; j < numIntervals; j++) {\n\t\t\t\tintervalOffsets.push(intervalOffset);",
    );
    expect(patchPlayCanvasContainerIntervalWorldSource(patchedWorld)).toBe(
      patchedWorld,
    );

    const render = `\tconst numIntervals = intervals.length / 2;\n\tif (numIntervals === 0) {\n\t} else {\n\t\tconst baseOffset = writeOffset;\n\t\tconst allocIds = splatInfo.intervalAllocIds;\n\t}\n`;
    const patchedRender = patchPlayCanvasContainerIntervalRenderSource(render);
    expect(patchedRender).toContain("allocIds.length !== numIntervals");
    expect(patchPlayCanvasContainerIntervalRenderSource(patchedRender)).toBe(
      patchedRender,
    );
  });

  it("fails closed when the audited anchors drift", () => {
    expect(() =>
      patchPlayCanvasContainerIntervalsSource("class Other {}"),
    ).toThrow("Unexpected PlayCanvas");
    expect(() =>
      patchPlayCanvasContainerIntervalsTypes("type Other = {}"),
    ).toThrow("Unexpected PlayCanvas");
    expect(() =>
      patchPlayCanvasContainerIntervalsBundleTypes("type Other = {}"),
    ).toThrow("anchor is missing");
  });
});
