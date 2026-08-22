import { describe, expect, it } from "vitest";
import {
  fitOrbitDistance,
  fitOrbitDistanceInFrame,
  lodProxyCoverage,
  orbitCameraBasis,
  panOrbitTarget,
} from "./playcanvas-backend";
import type { GaussianViewFrame } from "./backend";
import type { GsTileNode } from "./contracts";

describe("PlayCanvas orbit camera helpers", () => {
  it("fits both vertical and horizontal bounds into the perspective frustum", () => {
    const distance = fitOrbitDistance(
      [-3.5, -7, 0],
      [3.5, 5, 3],
      55,
      2,
      1,
    );
    const availableHalfHeight =
      (distance - 1.5) * Math.tan((55 * Math.PI) / 360);
    const availableHalfWidth = availableHalfHeight * 2;
    expect(availableHalfHeight).toBeGreaterThanOrEqual(6);
    expect(availableHalfWidth).toBeGreaterThanOrEqual(3.5);
  });

  it("uses the horizontal constraint for a wide model", () => {
    const distance = fitOrbitDistance([-10, -1, 0], [10, 1, 2], 60, 1, 1);
    expect(distance).toBeCloseTo(1 + 10 / Math.tan(Math.PI / 6));
  });

  it("pans in screen-aligned horizontal and vertical directions", () => {
    const target = panOrbitTarget(
      [0, 0, 0],
      0,
      0,
      10,
      100,
      50,
      1000,
      90,
    );
    expect(target[0]).toBeCloseTo(-2);
    expect(target[1]).toBeCloseTo(1);
    expect(target[2]).toBeCloseTo(0);
  });

  it("keeps panning screen-aligned after orbiting", () => {
    const target = panOrbitTarget(
      [1, 2, 3],
      Math.PI / 2,
      0,
      10,
      100,
      0,
      1000,
      90,
    );
    expect(target[0]).toBeCloseTo(1);
    expect(target[1]).toBeCloseTo(2);
    expect(target[2]).toBeCloseTo(5);
  });

  it("does not produce non-finite motion for a hidden or collapsed canvas", () => {
    const target = panOrbitTarget([0, 0, 0], 0, 0, 1, 1, 1, 0, 55);
    expect(target.every(Number.isFinite)).toBe(true);
  });

  const facadeFrame: GaussianViewFrame = {
    kind: "facade",
    right: [0, 1, 0],
    up: [0, 0, 1],
    outward: [1, 0, 0],
  };

  it("frames bounds in the recommended facade axes", () => {
    const distance = fitOrbitDistanceInFrame(
      [-1, -5, -10],
      [1, 5, 10],
      facadeFrame,
      90,
      1,
      1,
    );
    expect(distance).toBeCloseTo(11);
  });

  it("starts on the recommended outward normal with facade up", () => {
    const basis = orbitCameraBasis(facadeFrame, 0, 0);
    expect(basis.offset).toEqual([1, 0, 0]);
    expect(basis.right).toEqual([0, 1, 0]);
    expect(basis.up).toEqual([0, 0, 1]);
  });

  it("pans in facade screen coordinates", () => {
    const target = panOrbitTarget(
      [0, 0, 0],
      0,
      0,
      10,
      100,
      50,
      1000,
      90,
      facadeFrame,
    );
    expect(target[0]).toBeCloseTo(0);
    expect(target[1]).toBeCloseTo(-2);
    expect(target[2]).toBeCloseTo(1);
  });
});

describe("GSTile proxy coverage", () => {
  const proxyNode: GsTileNode = {
    id: "r",
    bounds: { min: [0, 0, 0], max: [8, 4, 0.25] },
    gaussianCount: 800_000,
    lodTile: {
      pack: "lod-r",
      byteOffset: 32,
      byteLength: 96_000,
      recordCount: 1_000,
      sha256: "a".repeat(64),
      quantization: {} as NonNullable<GsTileNode["lodTile"]>["quantization"],
    },
  };

  it("expands a sparse proxy up to its bounded surface spacing", () => {
    const coverage = lodProxyCoverage(proxyNode);
    expect(coverage.multiplier).toBe(800);
    expect(coverage.maximumScale).toBeCloseTo(
      Math.hypot(8, 4, 0.25) / Math.sqrt(1_000),
    );
  });

  it("keeps exact leaf representations unchanged", () => {
    const exact = {
      ...proxyNode,
      tile: proxyNode.lodTile,
    } satisfies GsTileNode;
    expect(lodProxyCoverage(exact)).toEqual({
      multiplier: 1,
      maximumScale: Number.MAX_VALUE,
    });
  });
});
