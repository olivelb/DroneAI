import { describe, expect, it } from "vitest";
import type { GsTileManifest, GsTileNode } from "./contracts";
import { selectGsTileLod } from "./lod-selection";

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
  verticalFovRadians: Math.PI / 3,
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
  });

  it("uses a deterministic complete cut when only one equal-priority branch fits", () => {
    const value = manifest();
    const [root, left, right] = value.nodes;
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

    expect(selection.selectedNodeIds).toEqual(["r00", "r01", "r1"]);
    expect(selection.residentGaussians).toBe(9_000);
  });
});
