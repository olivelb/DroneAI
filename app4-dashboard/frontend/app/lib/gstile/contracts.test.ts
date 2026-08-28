import { describe, expect, it } from "vitest";
import { decodeGsTileManifest, resolveGsTilePackUrl } from "./contracts";

const manifest = () => ({
  schema: "droneai-gstile",
  version: 1,
  profile: "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
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
      renderBounds: { min: [-0.5, -0.5, -0.5], max: [1.5, 1.5, 1.5] },
      geometricError: 0,
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
    lod: "deterministic-adaptive-cost-moment-opacity-refit-v4",
    proxyCount: 0,
    proxyRecords: 0,
  },
});

const lodManifest = () => {
  const base = manifest();
  const quantization = base.nodes[0].tile.quantization;
  const tile = (pack: string, sha256: string) => ({
    pack,
    byteOffset: 32,
    byteLength: 96,
    recordCount: 1,
    sha256,
    quantization,
  });
  const nodes = [
    {
      id: "r",
      bounds: { min: [0, 0, 0], max: [2, 1, 1] },
      gaussianCount: 2,
      geometricError: 0.25,
      children: ["r0", "r1"],
      lodTile: tile("lod-r", "d".repeat(64)),
    },
    {
      id: "r0",
      bounds: { min: [0, 0, 0], max: [1, 1, 1] },
      gaussianCount: 1,
      geometricError: 0,
      tile: tile("r0", "e".repeat(64)),
    },
    {
      id: "r1",
      bounds: { min: [1, 0, 0], max: [2, 1, 1] },
      gaussianCount: 1,
      geometricError: 0,
      tile: tile("r1", "f".repeat(64)),
    },
  ];
  const packs = [
    {
      id: "lod-r",
      path: "packs/lod-r.gst",
      byteLength: 128,
      recordCount: 1,
      sha256: "d".repeat(64),
      payloadCrc32: "12345678",
      byteOffset: 32,
    },
    {
      id: "r0",
      path: "packs/r0.gst",
      byteLength: 128,
      recordCount: 1,
      sha256: "e".repeat(64),
      payloadCrc32: "23456789",
      byteOffset: 32,
    },
    {
      id: "r1",
      path: "packs/r1.gst",
      byteLength: 128,
      recordCount: 1,
      sha256: "f".repeat(64),
      payloadCrc32: "3456789a",
      byteOffset: 32,
    },
  ];
  return {
    ...base,
    profile: "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
    source: { ...base.source, gaussianCount: 2 },
    nodes: nodes.map((node) => ({
      ...node,
      renderBounds: {
        min: node.bounds.min.map((coordinate) => coordinate - 0.5),
        max: node.bounds.max.map((coordinate) => coordinate + 0.5),
      },
    })),
    packs,
    statistics: {
      leafCount: 2,
      packBytes: 384,
      bytesPerGaussian: 192,
      lod: "deterministic-adaptive-cost-moment-opacity-refit-v4",
      exactPackBytes: 256,
      proxyCount: 1,
      proxyRecords: 1,
      proxyPackBytes: 128,
    },
  };
};

describe("GSTile browser contract", () => {
  it("accepts a single-leaf V4 manifest", () => {
    expect(decodeGsTileManifest(manifest()).root).toBe("r");
  });

  it("accepts a hierarchical V4 manifest", () => {
    const decoded = decodeGsTileManifest(lodManifest());
    expect(decoded.nodes.find((node) => node.id === "r")?.lodTile?.recordCount).toBe(
      1,
    );
  });

  it.each([
    "dronegs-sh3-opacity-sh3-q96",
    "dronegs-sh3-opacity-sh3-q96-minhash-lod-v1",
    "dronegs-sh3-opacity-sh3-q96-stratified-lod-v2",
    "dronegs-sh3-opacity-sh3-q96-moment-lod-v3",
  ])("rejects retired profile %s", (profile) => {
    expect(() => decodeGsTileManifest({ ...manifest(), profile })).toThrow(/profile/);
  });

  it("accepts several independently quantized tiles in one canonical pack", () => {
    const value = lodManifest();
    const exactHash = "e".repeat(64);
    const left = value.nodes[1].tile;
    const right = value.nodes[2].tile;
    if (!left || !right) throw new Error("LOD fixture exact tiles are missing");
    Object.assign(left, { pack: "aggregate-exact", sha256: exactHash });
    Object.assign(right, {
      pack: "aggregate-exact",
      byteOffset: 128,
      sha256: exactHash,
    });
    value.packs = [
      value.packs[0],
      {
        id: "aggregate-exact",
        path: "packs/aggregate-exact.gst",
        byteLength: 224,
        recordCount: 2,
        sha256: exactHash,
        payloadCrc32: "23456789",
        byteOffset: 32,
      },
    ];
    Object.assign(value.statistics, {
      packBytes: 352,
      bytesPerGaussian: 176,
      exactPackBytes: 224,
      packCount: 2,
      representationCount: 3,
      packTargetBytes: 224,
      packGrouping: "depth-spatial-v1",
    });

    const decoded = decodeGsTileManifest(value);
    expect(decoded.packs).toHaveLength(2);
    expect(decoded.nodes[2].tile?.byteOffset).toBe(128);
    expect(decoded.statistics.packGrouping).toBe("depth-spatial-v1");

    right.byteOffset = 32;
    expect(() => decodeGsTileManifest(value)).toThrow(/non-overlapping tile ranges/);
  });

  it("accepts V4 only with conservative hierarchical render bounds", () => {
    const value = lodManifest();
    value.profile = "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4";
    value.statistics.lod = "deterministic-adaptive-cost-moment-opacity-refit-v4";
    const adaptiveNodes = value.nodes as Array<
      (typeof value.nodes)[number] & {
        renderBounds: { min: number[]; max: number[] };
      }
    >;
    adaptiveNodes.forEach((node) => {
      node.renderBounds = {
        min: node.bounds.min.map((coordinate) => coordinate - 0.5),
        max: node.bounds.max.map((coordinate) => coordinate + 0.5),
      };
    });
    const decoded = decodeGsTileManifest(value);
    expect(decoded.profile).toBe(
      "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
    );

    adaptiveNodes[0].renderBounds.max[0] = 1;
    expect(() => decodeGsTileManifest(value)).toThrow(/renderBounds/);
  });

  it("rejects incomplete LOD proxy coverage", () => {
    const value = lodManifest();
    delete value.nodes[0].lodTile;
    expect(() => decodeGsTileManifest(value)).toThrow(/LOD proxy/);
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
