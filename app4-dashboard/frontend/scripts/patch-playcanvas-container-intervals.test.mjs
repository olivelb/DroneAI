import { describe, expect, it } from "vitest";
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
    expect(patchPlayCanvasContainerIntervalsSource(patched)).toBe(patched);
  });

  it("patches the public type contract once", () => {
    const source = "export class GSplatComponent {\n    hide(): void;\n}";
    const patched = patchPlayCanvasContainerIntervalsTypes(source);
    expect(patched).toContain(
      "ReadonlyArray<{ start: number; count: number }>",
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
    const info = `if (intervals.size) {\n\t} else {\n\t\tthis.activeSplats = totalCount;\n\t}\n`;
    const patchedInfo = patchPlayCanvasContainerIntervalInfoSource(info);
    expect(patchedInfo).toContain(
      "this.activeSplats = totalCount;\n\t\t// DroneAI non-octree interval bounds\n\t\tthis.numBoundsEntries = 1;",
    );
    expect(patchPlayCanvasContainerIntervalInfoSource(patchedInfo)).toBe(
      patchedInfo,
    );
    const legacyInfo = `\t} else {\n\t\tthis.activeSplats = totalCount;\t\t// DroneAI non-octree interval bounds\n\t\tthis.numBoundsEntries = 1;\n`;
    expect(patchPlayCanvasContainerIntervalInfoSource(legacyInfo)).toBe(
      legacyInfo,
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
