import { describe, expect, it } from "vitest";
import type { GsTileQuantization } from "./contracts";
import {
  crc32,
  decodeGsTilePack,
  gsTileOpacityStreams,
  gsTileToPlyProperties,
} from "./decode";

const quantization: GsTileQuantization = {
  position: { min: [10, 20, 30], max: [20, 40, 60] },
  logScale: { min: [-4, -3, -2], max: [-2, -1, 0] },
  rotation: { encoding: "snorm16x4" },
  opacityLogit: { min: -2, max: 2 },
  colorDcScale: [0.1, 0.2, 0.3],
  colorShScale: Array.from({ length: 45 }, (_, index) => (index + 1) / 100),
  opacityShScale: Array.from({ length: 15 }, (_, index) => (index + 1) / 50),
  sourceColorShDegree: 3,
  sourceOpacityShDegree: 3,
};

const createPack = () => {
  const content = new ArrayBuffer(128);
  const bytes = new Uint8Array(content);
  bytes.set(new TextEncoder().encode("GSTILE1\0"));
  const header = new DataView(content);
  header.setUint16(8, 1, true);
  header.setUint16(10, 32, true);
  header.setUint16(12, 96, true);
  header.setUint16(14, 0, true);
  header.setUint32(16, 1, true);
  header.setBigUint64(20, BigInt("0x0102030405060708"), true);

  const record = new DataView(content, 32);
  record.setUint16(0, 0, true);
  record.setUint16(2, 32768, true);
  record.setUint16(4, 65535, true);
  record.setUint16(6, 65535, true);
  record.setUint16(8, 32768, true);
  record.setUint16(10, 0, true);
  record.setInt16(12, 32767, true);
  record.setInt16(14, 0, true);
  record.setInt16(16, 0, true);
  record.setInt16(18, 0, true);
  record.setUint16(20, 49151, true);
  record.setInt16(22, 10, true);
  record.setInt16(24, -10, true);
  record.setInt16(26, 2, true);
  for (let index = 0; index < 45; index += 1) {
    record.setInt8(28 + index, index % 2 === 0 ? 2 : -2);
  }
  for (let index = 0; index < 15; index += 1) {
    record.setInt8(73 + index, index + 1);
  }
  record.setBigUint64(88, BigInt("0xfedcba9876543210"), true);
  header.setUint32(28, crc32(new Uint8Array(content, 32)), true);
  return content;
};

describe("GSTile pack decoder", () => {
  it("matches the reference affine and symmetric decoding", () => {
    const decoded = decodeGsTilePack(createPack(), quantization);
    expect(decoded.count).toBe(1);
    expect(Array.from(decoded.position)).toEqual([
      10,
      expect.closeTo(30.0001526, 5),
      60,
    ]);
    expect(Array.from(decoded.logScale)).toEqual([
      -2,
      expect.closeTo(-1.9999847, 5),
      -2,
    ]);
    expect(Array.from(decoded.rotation)).toEqual([1, 0, 0, 0]);
    expect(decoded.opacityLogit[0]).toBeCloseTo(1, 4);
    expect(Array.from(decoded.colorDc)).toEqual([
      expect.closeTo(1, 6),
      expect.closeTo(-2, 6),
      expect.closeTo(0.6, 6),
    ]);
    expect(decoded.colorSh[0]).toBeCloseTo(0.02);
    expect(decoded.colorSh[1]).toBeCloseTo(-0.04);
    expect(decoded.opacitySh[14]).toBeCloseTo(4.5);
    expect(decoded.sourceId[0]).toBe(BigInt("0xfedcba9876543210"));
  });

  it("rejects corruption before decoding", () => {
    const pack = createPack();
    new Uint8Array(pack)[50] ^= 0xff;
    expect(() => decodeGsTilePack(pack, quantization)).toThrow(
      "CRC mismatch",
    );
  });

  it("packs logit and opacity SH into four float32 RGBA streams", () => {
    const decoded = decodeGsTilePack(createPack(), quantization);
    const streams = gsTileOpacityStreams(decoded);
    const flattened = streams.flatMap((stream) => Array.from(stream));
    expect(flattened[0]).toBeCloseTo(decoded.opacityLogit[0]);
    expect(flattened.slice(1)).toEqual(
      Array.from(decoded.opacitySh, (value) => expect.closeTo(value, 6)),
    );
  });

  it("exposes PlayCanvas PLY properties in channel-major order", () => {
    const decoded = decodeGsTilePack(createPack(), quantization);
    const properties = gsTileToPlyProperties(decoded);
    expect(properties.map(({ name }) => name).slice(0, 14)).toEqual([
      "x",
      "y",
      "z",
      "f_dc_0",
      "f_dc_1",
      "f_dc_2",
      "opacity",
      "scale_0",
      "scale_1",
      "scale_2",
      "rot_0",
      "rot_1",
      "rot_2",
      "rot_3",
    ]);
    expect(properties.find(({ name }) => name === "f_rest_44")?.storage[0]).toBe(
      decoded.colorSh[44],
    );
  });
});

it("implements the standard CRC32 polynomial", () => {
  expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
});
