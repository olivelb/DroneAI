import { describe, expect, it } from "vitest";
import { decodeGsTileViewerDescriptor } from "./descriptor";

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
    expect(decoded.packUrls.get("r")).toEqual({
      identity: "https://objects.example/pack?signature=test",
    });
  });

  it("binds an optional zstd transport to its manifest identity", () => {
    const value = descriptor();
    Object.assign(value.manifest.packs[0], { encodings: {
      zstd: {
        path: "packs/r.gst.zst",
        byteLength: 96,
        sha256: "d".repeat(64),
      },
    } });
    Object.assign(value.packs[0], { encodings: {
      zstd: {
        url: "https://objects.example/pack.zst?signature=test",
        byteLength: 96,
        sha256: "d".repeat(64),
      },
    } });
    expect(decodeGsTileViewerDescriptor(value).packUrls.get("r")).toEqual({
      identity: "https://objects.example/pack?signature=test",
      zstd: "https://objects.example/pack.zst?signature=test",
    });
  });

  it("falls back to the signed identity URL when zstd was not negotiated", () => {
    const value = descriptor();
    Object.assign(value.manifest.packs[0], { encodings: {
      zstd: {
        path: "packs/r.gst.zst",
        byteLength: 96,
        sha256: "d".repeat(64),
      },
    } });
    expect(decodeGsTileViewerDescriptor(value).packUrls.get("r")).toEqual({
      identity: "https://objects.example/pack?signature=test",
    });
  });

  it("accepts an optional right-handed facade view", () => {
    const value = {
      ...descriptor(),
      recommendedView: {
        kind: "facade",
        right: [0, 1, 0],
        up: [0, 0, 1],
        outward: [1, 0, 0],
      },
    };
    expect(decodeGsTileViewerDescriptor(value).recommendedView).toEqual(
      value.recommendedView,
    );
  });

  it("rejects a non-orthonormal recommended view", () => {
    const value = {
      ...descriptor(),
      recommendedView: {
        kind: "facade",
        right: [1, 0, 0],
        up: [1, 0, 0],
        outward: [0, 0, 1],
      },
    };
    expect(() => decodeGsTileViewerDescriptor(value)).toThrow(
      /right-handed orthonormal frame/,
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

describe("GSTile signed attribute streams", () => {
  const value = () => {
    const result = descriptor();
    const streams = {
      version: 1,
      base: {path: "packs/r.gst.base", byteLength: 68, sha256: "d".repeat(64)},
      sh: {path: "packs/r.gst.sh", byteLength: 92, sha256: "e".repeat(64)},
    };
    Object.assign(result.manifest.packs[0], {streams});
    const signed = {
      version: 1,
      base: {...streams.base, url: "https://objects.example/base?signature=test"},
      sh: {...streams.sh, url: "https://objects.example/sh?signature=test"},
    };
    Object.assign(result.packs[0], {streams: signed});
    return {result, signed};
  };
  it("binds both independent identities and URLs", () => {
    const {result, signed} = value();
    expect(decodeGsTileViewerDescriptor(result).packUrls.get("r")?.streams).toEqual({
      base: signed.base.url, sh: signed.sh.url,
    });
  });
  it("rejects a mismatched SH identity and a non-HTTP base URL", () => {
    const {result, signed} = value();
    signed.sh.sha256 = "f".repeat(64);
    expect(() => decodeGsTileViewerDescriptor(result)).toThrow(/identity/);
    signed.sh.sha256 = "e".repeat(64);
    signed.base.url = "file:///private";
    expect(() => decodeGsTileViewerDescriptor(result)).toThrow(/URL/);
  });
  it("retains canonical fallback when streams were not negotiated", () => {
    const {result} = value();
    Reflect.deleteProperty(result.packs[0], "streams");
    expect(decodeGsTileViewerDescriptor(result).packUrls.get("r")?.streams).toBeUndefined();
  });
  it("opens a stream-only descriptor without a canonical URL and rejects missing streams", () => {
    const {result} = value();
    const bytes = new Uint8Array(32);
    bytes.set(new TextEncoder().encode("GSTILE1\0"));
    const h = new DataView(bytes.buffer);
    h.setUint16(8, 1, true); h.setUint16(10, 32, true); h.setUint16(12, 96, true); h.setUint32(16, 1, true);
    Object.assign(result.manifest.packs[0], {storage: "streams", q96Header: Array.from(bytes, b=>b.toString(16).padStart(2,"0")).join("")});
    Reflect.deleteProperty(result.packs[0], "url");
    expect(decodeGsTileViewerDescriptor(result).packUrls.get("r")?.identity).toBeUndefined();
    expect(decodeGsTileViewerDescriptor(result).packUrls.get("r")?.streams?.sh).toContain("/sh");
    Reflect.deleteProperty(result.packs[0], "streams");
    expect(()=>decodeGsTileViewerDescriptor(result)).toThrow(/Stream-only/);
  });

});
