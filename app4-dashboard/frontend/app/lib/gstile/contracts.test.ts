import { describe, expect, it } from "vitest";
import { decodeGsTileManifest, resolveGsTilePackUrl } from "./contracts";

const manifest = () => ({
  schema: "droneai-gstile",
  version: 1,
  profile: "dronegs-sh3-opacity-sh3-q96",
  bundleId: `sha256:${"a".repeat(64)}`,
  source: {
    sha256: "b".repeat(64),
    gaussianCount: 1,
    colorShDegree: 3,
    opacityShDegree: 3,
    recordBytes: 296,
  },
  coordinateFrame: { kind: "local", origin: [0, 0, 0], crs: null },
  root: "r",
  nodes: [
    {
      id: "r",
      bounds: { min: [0, 0, 0], max: [1, 1, 1] },
      gaussianCount: 1,
      tile: {
        pack: "r",
        byteOffset: 32,
        byteLength: 96,
        recordCount: 1,
        sha256: "c".repeat(64),
        quantization: {
          position: { min: [0, 0, 0], max: [1, 1, 1] },
          logScale: { min: [-4, -4, -4], max: [-3, -3, -3] },
          rotation: { encoding: "snorm16x4" },
          opacityLogit: { min: -1, max: 1 },
          colorDcScale: [1, 1, 1],
          colorShScale: Array(45).fill(1),
          opacityShScale: Array(15).fill(1),
          sourceColorShDegree: 3,
          sourceOpacityShDegree: 3,
        },
      },
    },
  ],
  packs: [
    {
      id: "r",
      path: "packs/r.gst",
      byteLength: 128,
      recordCount: 1,
      sha256: "c".repeat(64),
      payloadCrc32: "12345678",
      byteOffset: 32,
    },
  ],
  statistics: {
    leafCount: 1,
    packBytes: 128,
    bytesPerGaussian: 128,
    lod: "leaf-only",
  },
});

describe("GSTile browser contract", () => {
  it("accepts the version-one baseline manifest", () => {
    expect(decodeGsTileManifest(manifest()).root).toBe("r");
  });

  it("rejects path traversal before any pack request", () => {
    const value = manifest();
    value.packs[0].path = "../secret";
    expect(() => decodeGsTileManifest(value)).toThrow(/safe relative POSIX path/);
  });

  it("rejects record counts that could bypass the resident allocation gate", () => {
    const value = manifest();
    value.source.gaussianCount = 0;
    expect(() => decodeGsTileManifest(value)).toThrow(/non-empty nodes and packs/);

    const inconsistent = manifest();
    inconsistent.nodes[0].tile.recordCount = 2;
    expect(() => decodeGsTileManifest(inconsistent)).toThrow(/valid aligned range/);
  });

  it("requires tile ranges to cover their pack without holes or overlaps", () => {
    const value = manifest();
    value.nodes[0].tile.byteOffset = 128;
    expect(() => decodeGsTileManifest(value)).toThrow(/valid aligned range/);
  });

  it("resolves a safe pack relative to the manifest", () => {
    expect(
      resolveGsTilePackUrl(
        "https://assets.example/bundle/manifest.json",
        "packs/r.gst",
      ),
    ).toBe("https://assets.example/bundle/packs/r.gst");
  });
});
