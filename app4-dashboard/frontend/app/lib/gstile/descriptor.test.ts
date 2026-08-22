import { describe, expect, it } from "vitest";
import { decodeGsTileViewerDescriptor } from "./descriptor";

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

const descriptor = () => ({
  schema: "droneai-gaussian-viewer-descriptor",
  version: 1,
  artifactId: "artifact-1",
  bundleId: `sha256:${"a".repeat(64)}`,
  expiresAt: "2026-08-22T12:00:00Z",
  manifest: manifest(),
  packs: [
    {
      id: "r",
      url: "https://objects.example/pack?signature=test",
      byteLength: 128,
      sha256: "c".repeat(64),
    },
  ],
});

describe("GSTile signed descriptor", () => {
  it("binds every manifest pack to its signed URL", () => {
    const decoded = decodeGsTileViewerDescriptor(descriptor());
    expect(decoded.packUrls.get("r")).toBe(
      "https://objects.example/pack?signature=test",
    );
  });

  it("rejects a signed pack whose integrity differs from the manifest", () => {
    const value = descriptor();
    value.packs[0].sha256 = "d".repeat(64);
    expect(() => decodeGsTileViewerDescriptor(value)).toThrow(
      /unique manifest-matching pack/,
    );
  });

  it("rejects non-HTTP signed URLs", () => {
    const value = descriptor();
    value.packs[0].url = "javascript:alert(1)";
    expect(() => decodeGsTileViewerDescriptor(value)).toThrow(/HTTP\(S\) URL/);
  });
});
