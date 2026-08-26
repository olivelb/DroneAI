import { describe, expect, it } from "vitest";
import type { GsTileManifest, GsTileNode, GsTilePack } from "./contracts";
import {
  gstilePrefetchProjection,
  planGsTilePrefetchPacks,
  predictGsTileCameraPose,
  updateGsTileCameraMotion,
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
  it("predicts a smoothed camera trajectory over a bounded horizon", () => {
    const first = updateGsTileCameraMotion(
      null,
      {
        position: [0, 0, 10],
        direction: [0, 0, -1],
        up: [0, 1, 0],
      },
      100,
    );
    const second = updateGsTileCameraMotion(
      first,
      {
        position: [1, 0, 10],
        direction: [0.1, 0, -0.995],
        up: [0, 1, 0],
      },
      200,
      1,
    );
    const predicted = predictGsTileCameraPose(
      second,
      100,
      10,
      Math.PI / 4,
    );

    expect(predicted).not.toBeNull();
    expect(predicted?.position).toEqual([2, 0, 10]);
    expect(predicted?.direction[0]).toBeGreaterThan(0.19);
    expect(Math.hypot(...(predicted?.direction ?? []))).toBeCloseTo(1, 10);
  });

  it("caps prediction distance and angle", () => {
    const first = updateGsTileCameraMotion(
      null,
      {
        position: [0, 0, 0],
        direction: [0, 0, -1],
        up: [0, 1, 0],
      },
      0,
    );
    const second = updateGsTileCameraMotion(
      first,
      {
        position: [100, 0, 0],
        direction: [1, 0, 0],
        up: [0, 1, 0],
      },
      10,
      1,
    );
    const predicted = predictGsTileCameraPose(
      second,
      100,
      2,
      Math.PI / 12,
    );

    expect(predicted?.position).toEqual([102, 0, 0]);
    const directionDot = predicted
      ? predicted.direction[0] * second.pose.direction[0] +
        predicted.direction[1] * second.pose.direction[1] +
        predicted.direction[2] * second.pose.direction[2]
      : -1;
    expect(
      Math.acos(Math.max(-1, Math.min(1, directionDot))),
    ).toBeLessThanOrEqual(Math.PI / 12 + 1e-9);
  });

  it("ignores stationary and stale motion samples", () => {
    const stationary = updateGsTileCameraMotion(
      null,
      {
        position: [1, 2, 3],
        direction: [0, 0, -1],
        up: [0, 1, 0],
      },
      0,
    );
    const unchanged = updateGsTileCameraMotion(
      stationary,
      stationary.pose,
      16,
    );
    expect(
      predictGsTileCameraPose(unchanged, 200, 10, Math.PI / 4),
    ).toBeNull();

    const stale = updateGsTileCameraMotion(
      unchanged,
      {
        ...unchanged.pose,
        position: [100, 2, 3],
      },
      3_000,
    );
    expect(stale.samples).toBe(1);
    expect(predictGsTileCameraPose(stale, 200, 10, Math.PI / 4)).toBeNull();
  });

  it("retains a slow deliberate pan sampled more than one second apart", () => {
    const first = updateGsTileCameraMotion(
      null,
      {
        position: [0, 0, 10],
        direction: [0, 0, -1],
        up: [0, 1, 0],
      },
      0,
    );
    const slowPan = updateGsTileCameraMotion(
      first,
      {
        position: [1, 0, 10],
        direction: [0, 0, -1],
        up: [0, 1, 0],
      },
      1_500,
      1,
    );

    expect(slowPan.samples).toBe(2);
    expect(
      predictGsTileCameraPose(slowPan, 1_500, 10, Math.PI / 4)?.position,
    ).toEqual([2, 0, 10]);
  });

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

  it("spends the transfer budget only on packs absent from local memory", () => {
    const planned = planGsTilePrefetchPacks(
      manifest(),
      [],
      ["r1", "r0", "r"],
      250,
      ["c"],
    );

    expect(planned.map((entry) => entry.nodeId)).toEqual(["r0", "r"]);
    expect(
      planned.reduce((total, entry) => total + entry.pack.byteLength, 0),
    ).toBe(250);
  });

  it("rejects incomplete expanded selections", () => {
    expect(() =>
      planGsTilePrefetchPacks(manifest(), [], ["missing"], 1_000),
    ).toThrow(/incomplete/);
  });
});
