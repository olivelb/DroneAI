import { describe, expect, it } from "vitest";
import type { GsTileManifest, GsTileNode, GsTilePack } from "./contracts";
import {
  gstilePrefetchProjection,
  planGsTilePrefetchPacks,
} from "./lod-prefetch";

const pack = (id: string, byteLength: number): GsTilePack => ({
  id,
  path: `packs/${id}.gst`,
  byteLength,
  recordCount: 1,
  sha256: id.padEnd(64, "0"),
  payloadCrc32: "00000000",
  byteOffset: 0,
});

const node = (id: string, packId: string): GsTileNode => ({
  id,
  bounds: { min: [0, 0, 0], max: [1, 1, 1] },
  gaussianCount: 1,
  tile: {
    pack: packId,
    byteOffset: 0,
    byteLength: 96,
    recordCount: 1,
    sha256: packId.padEnd(64, "0"),
    quantization: {} as NonNullable<GsTileNode["tile"]>["quantization"],
  },
});

const manifest = (): GsTileManifest => {
  const packs = [pack("a", 100), pack("b", 150), pack("c", 200)];
  return {
    schema: "droneai-gstile",
    version: 1,
    profile: "dronegs-sh3-opacity-sh3-q96",
    bundleId: `sha256:${"f".repeat(64)}`,
    source: {
      sha256: "e".repeat(64),
      gaussianCount: 3,
      colorShDegree: 3,
      opacityShDegree: 3,
      recordBytes: 96,
    },
    coordinateFrame: { kind: "local", origin: [0, 0, 0], crs: null },
    root: "r",
    nodes: [node("r", "a"), node("r0", "b"), node("r1", "c")],
    packs,
    statistics: {
      leafCount: 3,
      packBytes: 450,
      bytesPerGaussian: 150,
      lod: "test",
    },
  };
};

describe("GSTile LOD halo prefetch", () => {
  it("expands the frustum while preserving rendered pixel density", () => {
    const verticalFovRadians = (42 * Math.PI) / 180;
    const projection = gstilePrefetchProjection(
      verticalFovRadians,
      1_920,
      1_080,
      1.75,
      (80 * Math.PI) / 180,
    );
    const renderedFocalPixels =
      1_080 / (2 * Math.tan(verticalFovRadians / 2));
    const prefetchFocalPixels =
      projection.viewportHeight /
      (2 * Math.tan(projection.verticalFovRadians / 2));

    expect((projection.verticalFovRadians * 180) / Math.PI).toBeCloseTo(
      73.5,
      10,
    );
    expect(projection.viewportWidth / projection.viewportHeight).toBeCloseTo(
      1_920 / 1_080,
      12,
    );
    expect(prefetchFocalPixels).toBeCloseTo(renderedFocalPixels, 10);
  });

  it("never narrows a rendered FOV that already exceeds the prefetch cap", () => {
    const verticalFovRadians = (90 * Math.PI) / 180;
    const projection = gstilePrefetchProjection(
      verticalFovRadians,
      1_920,
      1_080,
      1.75,
      (80 * Math.PI) / 180,
    );

    expect(projection.verticalFovRadians).toBe(verticalFovRadians);
    expect(projection.viewportWidth).toBeCloseTo(1_920, 12);
    expect(projection.viewportHeight).toBeCloseTo(1_080, 12);
  });

  it("keeps expanded-cut priority and excludes the resident cut", () => {
    const planned = planGsTilePrefetchPacks(
      manifest(),
      ["r"],
      ["r", "r1", "r0"],
      400,
    );

    expect(planned.map((entry) => entry.nodeId)).toEqual(["r1", "r0"]);
  });

  it("enforces the byte budget without hiding later smaller packs", () => {
    const planned = planGsTilePrefetchPacks(
      manifest(),
      [],
      ["r1", "r0", "r"],
      250,
    );

    expect(planned.map((entry) => entry.nodeId)).toEqual(["r1"]);
    expect(planned[0].pack.byteLength).toBe(200);
  });

  it("rejects incomplete expanded selections", () => {
    expect(() =>
      planGsTilePrefetchPacks(manifest(), [], ["missing"], 1_000),
    ).toThrow(/incomplete/);
  });
});
