import { describe, expect, it } from "vitest";
import { droneGsDirectionalOpacity, droneGsShBasis } from "./dronegs-sh";

describe("DroneGS SH parity reference", () => {
  it("matches the DroneGS degree-three basis convention", () => {
    const basis = droneGsShBasis([1, 0, 0], 3);
    expect(basis[0]).toBeCloseTo(0.28209479177387814, 7);
    expect(basis[3]).toBeCloseTo(-0.4886025119029199, 7);
    expect(basis[6]).toBeCloseTo(-0.31539156525252005, 7);
    expect(basis[8]).toBeCloseTo(0.5462742152960396, 7);
    expect(basis[13]).toBeCloseTo(0.4570457994644658, 7);
    expect(basis[15]).toBeCloseTo(-0.5900435899266435, 7);
  });

  it("adds directional residuals in logit space before sigmoid", () => {
    const residuals = new Float32Array(15);
    residuals[2] = 2;
    expect(droneGsDirectionalOpacity(0, residuals, [1, 0, 0], 3)).toBeCloseTo(
      0.273446,
      5,
    );
    expect(droneGsDirectionalOpacity(0, residuals, [-1, 0, 0], 3)).toBeCloseTo(
      0.726554,
      5,
    );
  });

  it("rejects invalid directions", () => {
    expect(() => droneGsShBasis([0, 0, 0], 3)).toThrow(/finite and non-zero/);
  });
});
