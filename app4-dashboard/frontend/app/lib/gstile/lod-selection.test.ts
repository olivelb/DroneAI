import { describe, expect, it } from "vitest";
import type { GsTileManifest, GsTileNode } from "./contracts";
import { lodProxySupportError, selectGsTileLod } from "./lod-selection";

const tile = (recordCount: number) => ({
  pack: "unused",
  byteOffset: 32,
  byteLength: recordCount * 96,
  recordCount,
  sha256: "a".repeat(64),
  quantization: {} as NonNullable<GsTileNode["tile"]>["quantization"],
});

const manifest = (): GsTileManifest => ({
  schema: "droneai-gstile",
  version: 1,
  profile: "dronegs-sh3-opacity-sh3-q96-minhash-lod-v1",
  bundleId: `sha256:${"a".repeat(64)}`,
  source: {
    sha256: "b".repeat(64),
    gaussianCount: 16_000,
    colorShDegree: 3,
    opacityShDegree: 3,
    recordBytes: 296,
  },
  coordinateFrame: { kind: "local", origin: [0, 0, 0], crs: null },
  root: "r",
  nodes: [
    {
      id: "r",
      bounds: { min: [-1, -1, -1], max: [1, 1, 1] },
      gaussianCount: 16_000,
      geometricError: 0.5,
      children: ["r0", "r1"],
      lodTile: tile(1_000),
    },
    {
      id: "r0",
      bounds: { min: [-1, -1, -1], max: [0, 1, 1] },
      gaussianCount: 8_000,
      geometricError: 0,
      tile: tile(8_000),
    },
    {
      id: "r1",
      bounds: { min: [0, -1, -1], max: [1, 1, 1] },
      gaussianCount: 8_000,
      geometricError: 0,
      tile: tile(8_000),
    },
  ],
  packs: [],
  statistics: {
    leafCount: 2,
    packBytes: 0,
    bytesPerGaussian: 0,
    lod: "deterministic-minhash-replacement-v1",
  },
});

const options = {
  cameraPosition: [0, 0, 10] as [number, number, number],
  cameraDirection: [0, 0, -1] as [number, number, number],
  cameraUp: [0, 1, 0] as [number, number, number],
  verticalFovRadians: Math.PI / 3,
  viewportWidth: 1_920,
  viewportHeight: 1_080,
  maximumResidentGaussians: 20_000,
  maximumProjectedErrorPixels: 100,
};

describe("GSTile LOD selection", () => {
  it("keeps the parent when its projected error is acceptable", () => {
    const selection = selectGsTileLod(manifest(), options);
    expect(selection.selectedNodeIds).toEqual(["r"]);
    expect(selection.residentGaussians).toBe(1_000);
  });

  it("atomically replaces a parent with all children when close enough", () => {
    const selection = selectGsTileLod(manifest(), {
      ...options,
      cameraPosition: [0, 0, 2],
      maximumProjectedErrorPixels: 10,
    });
    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
    expect(selection.residentGaussians).toBe(16_000);
  });

  it("keeps the complete parent representation when children exceed budget", () => {
    const selection = selectGsTileLod(manifest(), {
      ...options,
      cameraPosition: [0, 0, 2],
      maximumProjectedErrorPixels: 10,
      maximumResidentGaussians: 10_000,
    });
    expect(selection.selectedNodeIds).toEqual(["r"]);
    expect(selection.residentGaussians).toBe(1_000);
    expect(selection.budgetLimited).toBe(true);
    expect(selection.unresolvedMaximumErrorPixels).toBeGreaterThan(10);
  });

  it("uses the nearest conservative depth for elongated off-axis bounds", () => {
    const value = manifest();
    value.nodes[0].bounds = { min: [-10, -1, -1], max: [10, 1, 1] };
    value.nodes[1].bounds = { min: [-10, -1, -1], max: [0, 1, 1] };
    value.nodes[2].bounds = { min: [0, -1, -1], max: [10, 1, 1] };

    const selection = selectGsTileLod(value, {
      ...options,
      maximumProjectedErrorPixels: 50,
    });

    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
  });

  it("uses anisotropic render support for Cesium-style closest-volume SSE", () => {
    const value = manifest();
    value.nodes[0].renderBounds = { min: [-1, -1, -5], max: [1, 1, 15] };

    const selection = selectGsTileLod(value, options);

    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
    expect(selection.maximumSelectedErrorPixels).toBe(0);
    expect(selection.budgetLimited).toBe(false);
  });

  it("refines a wide moment proxy even when its centers have negligible error", () => {
    const value = manifest();
    const root = value.nodes[0];
    root.geometricError = 0.001;
    root.lodTile!.quantization = {
      logScale: {
        min: [Math.log(0.01), Math.log(0.01), Math.log(0.01)],
        max: [Math.log(0.5), Math.log(0.25), Math.log(0.1)],
      },
    } as NonNullable<GsTileNode["lodTile"]>["quantization"];

    expect(lodProxySupportError(root)).toBeCloseTo(0.5, 8);
    const selection = selectGsTileLod(value, {
      ...options,
      maximumProjectedErrorPixels: 10,
    });

    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
  });

  it("bounds projected error when the camera is inside an empty node AABB", () => {
    const value = manifest();
    value.nodes[0].bounds = { min: [-10, -10, -10], max: [10, 10, 10] };

    const selection = selectGsTileLod(value, {
      ...options,
      cameraPosition: [0, 0, 0],
      maximumResidentGaussians: 1_000,
    });

    const focalPixels =
      options.viewportHeight /
      (2 * Math.tan(options.verticalFovRadians / 2));
    expect(selection.selectedNodeIds).toEqual(["r"]);
    expect(selection.budgetLimited).toBe(true);
    expect(selection.unresolvedMaximumErrorPixels).toBeLessThanOrEqual(
      focalPixels,
    );
  });

  it("raises a global SSE instead of arbitrarily refining one equal branch", () => {
    const value = manifest();
    const [root, left, right] = value.nodes;
    root.geometricError = 1;
    left.tile = undefined;
    left.lodTile = tile(1_000);
    left.geometricError = 0.5;
    left.children = ["r00", "r01"];
    right.tile = undefined;
    right.lodTile = tile(1_000);
    right.geometricError = 0.5;
    right.children = ["r10", "r11"];
    value.nodes = [
      root,
      left,
      {
        id: "r00",
        bounds: { min: [-1, -1, -1], max: [-0.5, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
      {
        id: "r01",
        bounds: { min: [-0.5, -1, -1], max: [0, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
      right,
      {
        id: "r10",
        bounds: { min: [0, -1, -1], max: [0.5, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
      {
        id: "r11",
        bounds: { min: [0.5, -1, -1], max: [1, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
    ];

    const selection = selectGsTileLod(value, {
      ...options,
      cameraPosition: [0, 0, 2],
      maximumProjectedErrorPixels: 10,
      maximumResidentGaussians: 9_000,
    });

    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
    expect(selection.residentGaussians).toBe(2_000);
    expect(selection.budgetLimited).toBe(true);
  });

  it("does not refine cheaper neighbours while a higher-error branch is blocked", () => {
    const value = manifest();
    const [root, left, right] = value.nodes;
    root.lodTile = tile(500);
    root.geometricError = 1.2;
    left.tile = undefined;
    left.lodTile = tile(1_000);
    left.geometricError = 0.6;
    left.children = ["r00", "r01"];
    right.tile = undefined;
    right.lodTile = tile(1_000);
    right.geometricError = 0.5;
    right.children = ["r10", "r11"];
    value.nodes = [
      root,
      left,
      {
        id: "r00",
        bounds: { min: [-1, -1, -1], max: [-0.5, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
      {
        id: "r01",
        bounds: { min: [-0.5, -1, -1], max: [0, 1, 1] },
        gaussianCount: 4_000,
        geometricError: 0,
        tile: tile(4_000),
      },
      right,
      {
        id: "r10",
        bounds: { min: [0, -1, -1], max: [0.5, 1, 1] },
        gaussianCount: 1_000,
        geometricError: 0,
        tile: tile(1_000),
      },
      {
        id: "r11",
        bounds: { min: [0.5, -1, -1], max: [1, 1, 1] },
        gaussianCount: 1_000,
        geometricError: 0,
        tile: tile(1_000),
      },
    ];

    const selection = selectGsTileLod(value, {
      ...options,
      cameraPosition: [0, 0, 2],
      maximumProjectedErrorPixels: 10,
      maximumResidentGaussians: 4_000,
    });

    expect(selection.selectedNodeIds).toEqual(["r0", "r1"]);
    expect(selection.residentGaussians).toBe(2_000);
    expect(selection.budgetLimited).toBe(true);
  });

  it("culls a REPLACE parent when only its loose union sphere is visible", () => {
    const value = manifest();
    value.nodes[0].renderBounds = { min: [-31, -1, -1], max: [31, 1, 1] };
    value.nodes[1].renderBounds = { min: [-31, -1, -1], max: [-29, 1, 1] };
    value.nodes[2].renderBounds = { min: [29, -1, -1], max: [31, 1, 1] };

    const selection = selectGsTileLod(value, {
      ...options,
      verticalFovRadians: Math.PI / 6,
      viewportWidth: 1_000,
      viewportHeight: 1_000,
      maximumProjectedErrorPixels: 1_000,
    });

    expect(selection.selectedNodeIds).toEqual([]);
    expect(selection.residentGaussians).toBe(0);
  });

  it("spends the budget on visible descendants instead of an off-screen sibling", () => {
    const value = manifest();
    value.nodes[2].bounds = { min: [20, -1, -1], max: [21, 1, 1] };

    const selection = selectGsTileLod(value, {
      ...options,
      cameraPosition: [-0.5, 0, 2],
      verticalFovRadians: Math.PI / 6,
      viewportWidth: 1_000,
      viewportHeight: 1_000,
      maximumProjectedErrorPixels: 10,
      maximumResidentGaussians: 8_000,
    });

    expect(selection.selectedNodeIds).toEqual(["r0"]);
    expect(selection.residentGaussians).toBe(8_000);
  });

  it("keeps a tile visible when anisotropic splat support crosses the frustum", () => {
    const value = manifest();
    value.nodes[0].renderBounds = { min: [-1, -1, -1], max: [22, 1, 1] };
    value.nodes[1].bounds = { min: [20, -1, -1], max: [21, 1, 1] };
    value.nodes[1].renderBounds = { min: [-0.5, -1, -1], max: [22, 1, 1] };
    value.nodes[2].bounds = { min: [30, -1, -1], max: [31, 1, 1] };
    value.nodes[2].renderBounds = { min: [29, -1, -1], max: [32, 1, 1] };

    const selection = selectGsTileLod(value, {
      ...options,
      cameraPosition: [0, 0, 2],
      verticalFovRadians: Math.PI / 6,
      viewportWidth: 1_000,
      viewportHeight: 1_000,
      maximumProjectedErrorPixels: 10,
      maximumResidentGaussians: 8_000,
    });

    expect(selection.selectedNodeIds).toEqual(["r0"]);
  });

  it("returns an empty view cut when the hierarchy is entirely behind the camera", () => {
    const selection = selectGsTileLod(manifest(), {
      ...options,
      cameraDirection: [0, 0, 1],
    });

    expect(selection.selectedNodeIds).toEqual([]);
    expect(selection.residentGaussians).toBe(0);
  });
});
