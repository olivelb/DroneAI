import { describe, expect, it } from "vitest";
import {
  completeLodTargetPlan,
  configureHighQualityGsplatRendering,
  coordinateFrameCameraPosition,
  fitOrbitDistance,
  fitOrbitDistanceInFrame,
  lodTransitionCounts,
  lodProxyCoverage,
  orbitCameraBasis,
  planLodTransitions,
  prioritizeLodLoads,
  panOrbitTarget,
} from "./playcanvas-backend";
import type { GaussianViewFrame } from "./backend";
import type { GsTileManifest, GsTileNode } from "./contracts";

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

  it("does not inflate moment-matched proxies a second time", () => {
    expect(lodProxyCoverage(proxyNode, false)).toEqual({
      multiplier: 1,
      maximumScale: Number.MAX_VALUE,
    });
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
describe("PlayCanvas high-quality Gaussian rendering", () => {
  it("evaluates directional opacity in the same absolute frame as splat centers", () => {
    expect(
      coordinateFrameCameraPosition(
        [2.5, -4, 8],
        [638_000, 6_215_000, 123],
      ),
    ).toEqual([638_002.5, 6_214_996, 131]);
  });

  it("enables the reference anti-aliasing and low-contribution settings", () => {
    const settings = {
      antiAlias: false,
      alphaClip: 0.3,
      colorUpdateAngle: 4,
      dataFormat: "compact",
      minContribution: 3,
      minPixelSize: 2,
      radialSorting: false,
      renderer: 0,
    };

    configureHighQualityGsplatRendering(settings, {
      dataFormat: "large",
      renderer: 2,
    });

    expect(settings).toEqual({
      antiAlias: true,
      alphaClip: 1 / 255,
      colorUpdateAngle: 0,
      dataFormat: "large",
      minContribution: 0.05,
      minPixelSize: 0.5,
      radialSorting: true,
      renderer: 2,
    });
  });
});

describe("GSTile progressive LOD transitions", () => {
  const manifest = {
    root: "r",
    nodes: [
      { id: "r", children: ["a", "b"] },
      { id: "a", children: ["a0", "a1"] },
      { id: "b" },
      { id: "a0" },
      { id: "a1" },
    ],
  } as GsTileManifest;

  it("groups refinement and coarsening into atomic subtree swaps", () => {
    expect(planLodTransitions(manifest, ["r"], ["a", "b"])).toEqual([
      {
        addNodeIds: ["a", "b"],
        removeNodeIds: ["r"],
      },
    ]);
    expect(
      planLodTransitions(manifest, ["a", "b"], ["a0", "a1", "b"]),
    ).toEqual([
      {
        addNodeIds: ["a0", "a1"],
        removeNodeIds: ["a"],
      },
    ]);
    expect(
      planLodTransitions(manifest, ["a0", "a1", "b"], ["a", "b"]),
    ).toEqual([
      {
        addNodeIds: ["a"],
        removeNodeIds: ["a0", "a1"],
      },
    ]);
  });

  it("reveals initial target nodes independently as they become ready", () => {
    expect(planLodTransitions(manifest, [], ["a", "b"])).toEqual([
      { addNodeIds: ["a"], removeNodeIds: [] },
      { addNodeIds: ["b"], removeNodeIds: [] },
    ]);
  });

  it("loads every sibling of the highest-impact replacement before the next branch", () => {
    const transitions = [
      { addNodeIds: ["b0", "b1"], removeNodeIds: ["b"] },
      { addNodeIds: ["a0", "a1"], removeNodeIds: ["a"] },
    ];
    expect(
      prioritizeLodLoads(
        transitions,
        ["a0", "b0", "a1", "b1", "stable"],
        ["a", "b", "stable"],
      ),
    ).toEqual(["a0", "a1", "b0", "b1"]);
  });

  it("does not count an already resident target twice during an atomic swap", () => {
    const resident = new Map([
      ["r", 1_000],
      ["a", 4_000],
    ]);
    const staged = new Map([["b", 4_000]]);

    expect(
      lodTransitionCounts(
        { addNodeIds: ["a", "b"], removeNodeIds: ["r"] },
        (nodeId) => resident.get(nodeId),
        (nodeId) => staged.get(nodeId),
      ),
    ).toEqual({ add: 4_000, remove: 1_000 });
  });

  it("falls back to one complete target commit when local swaps cannot fit", () => {
    const resident = new Map([
      ["r", 1_000],
      ["a", 4_000],
    ]);
    const staged = new Map([["b", 4_000]]);

    expect(
      completeLodTargetPlan(
        resident.keys(),
        ["a", "b"],
        (nodeId) => resident.get(nodeId),
        (nodeId) => staged.get(nodeId),
      ),
    ).toEqual({
      complete: true,
      gaussianCount: 8_000,
      addNodeIds: ["b"],
      removeNodeIds: ["r"],
    });
  });

  it("refuses an incomplete final LOD target", () => {
    const resident = new Map([["a", 4_000]]);
    expect(
      completeLodTargetPlan(
        resident.keys(),
        ["a", "b"],
        (nodeId) => resident.get(nodeId),
        () => undefined,
      ),
    ).toMatchObject({ complete: false });
  });
});
