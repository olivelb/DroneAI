import { describe, expect, it } from "vitest";
import {
  GSPLAT_HEXAGON_FOOTPRINT,
  patchGsplatHybridMeshSource,
  patchGsplatHexagonIndexCountSource,
  patchGsplatHexagonMeshSource,
} from "./patch-playcanvas-hexagon-footprint.mjs";

describe("PlayCanvas GSplat hexagon footprint patch", () => {
  it("strictly contains the unit disk with less area than a square", () => {
    let twiceArea = 0;
    for (let edge = 0; edge < GSPLAT_HEXAGON_FOOTPRINT.length; edge++) {
      const current = GSPLAT_HEXAGON_FOOTPRINT[edge];
      const next =
        GSPLAT_HEXAGON_FOOTPRINT[
          (edge + 1) % GSPLAT_HEXAGON_FOOTPRINT.length
        ];
      twiceArea += current[0] * next[1] - current[1] * next[0];
      for (let sample = 0; sample < 4096; sample++) {
        const angle = (sample / 4096) * Math.PI * 2;
        const x = Math.cos(angle);
        const y = Math.sin(angle);
        const cross =
          (next[0] - current[0]) * (y - current[1]) -
          (next[1] - current[1]) * (x - current[0]);
        expect(cross).toBeGreaterThanOrEqual(0);
      }
    }
    const area = Math.abs(twiceArea) * 0.5;
    expect(area).toBeGreaterThan(Math.PI);
    expect(area).toBeLessThan(3.47);
  });

  it("patches the mesh once and fails closed on drift", () => {
    const source = `class GSplatResourceBase {
\tstatic createMesh(device) { return device; }
\tstatic get instanceSize() {
\t\treturn 128;
\t}
}`;
    const patched = patchGsplatHexagonMeshSource(source);
    expect(patched).toContain("DroneAI circumscribed hexagon");
    expect(patched).toContain("static createHybridMesh(device)");
    expect(patched).toContain("static createMesh(device) { return device; }");
    expect(patched).toContain("verticesPerSplat = 6");
    expect(patched).toContain("indicesPerSplat = 12");
    expect(patchGsplatHexagonMeshSource(patched)).toBe(patched);
    expect(() => patchGsplatHexagonMeshSource("class Other {}"))
      .toThrow("Unexpected PlayCanvas");
  });

  it("uses the dedicated mesh only in the hybrid renderer", () => {
    const source = [
      "const mesh = GSplatResourceBase.createMesh(this.device);",
      "const unrelated = true;",
      "const mesh = GSplatResourceBase.createMesh(this.device);",
    ].join("\n");
    const patched = patchGsplatHybridMeshSource(source);
    expect(patched.match(/createHybridMesh/g)).toHaveLength(2);
    expect(patched).toContain("const unrelated = true;");
    expect(patchGsplatHybridMeshSource(patched)).toBe(patched);
    expect(() => patchGsplatHybridMeshSource("const mesh = other();"))
      .toThrow("Unexpected PlayCanvas");
  });

  it("updates indirect index counts once and fails closed on drift", () => {
    const source =
      "const INDEX_COUNT = 6 * GSplatResourceBase.instanceSize;";
    const patched = patchGsplatHexagonIndexCountSource(source);
    expect(patched).toContain("INDEX_COUNT = 12 *");
    expect(patchGsplatHexagonIndexCountSource(patched)).toBe(patched);
    expect(() => patchGsplatHexagonIndexCountSource("const OTHER = 6;"))
      .toThrow("Unexpected PlayCanvas");
  });
});
