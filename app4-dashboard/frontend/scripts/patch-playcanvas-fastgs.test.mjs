import { describe, expect, it } from "vitest";

import {
  fastGsProjectedRatio,
  patchFastGsProjectionSource,
} from "./patch-playcanvas-fastgs.mjs";

describe("PlayCanvas FastGS projection patch", () => {
  it("bounds off-axis perspective ratios to the FastGS support margin", () => {
    expect(
      fastGsProjectedRatio({
        coordinate: 5,
        depth: 1,
        focal: 1_000,
        viewportDimension: 800,
      }),
    ).toBeCloseTo(1.04);
    expect(
      fastGsProjectedRatio({
        coordinate: -5,
        depth: 1,
        focal: 1_000,
        viewportDimension: 800,
      }),
    ).toBeCloseTo(-1.04);
  });

  it("leaves in-frustum perspective ratios unchanged", () => {
    expect(
      fastGsProjectedRatio({
        coordinate: 0.5,
        depth: 1,
        focal: 1_000,
        viewportDimension: 800,
      }),
    ).toBeCloseTo(0.5);
  });

  it("replaces the unbounded PlayCanvas Jacobian exactly once", () => {
    const source = `
        let ortho = isOrtho == 1u;
        let v = select(viewCenter.xyz, vec3f(0.0, 0.0, 1.0), ortho);
        let vz = select(min(v.z, -0.001), v.z, ortho);
        let J1 = focal / vz;
        let J2 = -J1 / vz * v.xy;
    `;
    const patched = patchFastGsProjectionSource(source);

    expect(patched).toContain("let projected = clamp(-v.xy / vz");
    expect(patched).toContain("let J2 = J1 * projected;");
    expect(patched).not.toContain("let J2 = -J1 / vz * v.xy;");
    expect(patchFastGsProjectionSource(patched)).toBe(patched);
  });
});
