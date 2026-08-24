import { describe, expect, it } from "vitest";
import type { GsTileQuantization } from "./contracts";
import {
  allocateDecodedGsTile,
  allocateGsTilePlayCanvasColumns,
  copyDecodedGsTile,
  crc32,
  decodeGsTilePack,
  decodeSha256VerifiedGsTilePackTile,
  decodeSha256VerifiedGsTilePackTileInto,
  decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns,
  gsTileOpacityStreams,
  gsTileToPlyProperties,
  mergeDecodedGsTiles,
} from "./decode";
import { packGsTileNativeSh } from "./native-sh";

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

const createDeterministicRandomPack = (recordCount: number) => {
  const content = new ArrayBuffer(32 + recordCount * 96);
  const bytes = new Uint8Array(content);
  bytes.set(new TextEncoder().encode("GSTILE1\0"));
  const view = new DataView(content);
  view.setUint16(8, 1, true);
  view.setUint16(10, 32, true);
  view.setUint16(12, 96, true);
  view.setUint16(14, 0, true);
  view.setUint32(16, recordCount, true);
  let state = 0x9e3779b9;
  const randomByte = () => {
    state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
    return state >>> 24;
  };
  for (let offset = 32; offset < content.byteLength; offset += 1) {
    bytes[offset] = randomByte();
  }
  for (let record = 0; record < recordCount; record += 1) {
    const base = 32 + record * 96;
    if (
      view.getInt16(base + 12, true) === 0 &&
      view.getInt16(base + 14, true) === 0 &&
      view.getInt16(base + 16, true) === 0 &&
      view.getInt16(base + 18, true) === 0
    ) {
      view.setInt16(base + 12, 1, true);
    }
  }
  view.setUint32(28, crc32(new Uint8Array(content, 32)), true);
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

  it("skips the redundant CRC pass only for a SHA-256-authenticated pack", () => {
    const pack = createPack();
    const reference = decodeGsTilePack(pack, quantization);
    const decoded = decodeSha256VerifiedGsTilePackTile(
      pack,
      32,
      96,
      1,
      quantization,
    );

    expect(Array.from(decoded.position)).toEqual(Array.from(reference.position));
    expect(Array.from(decoded.colorSh)).toEqual(Array.from(reference.colorSh));
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

  it("assembles a preallocated cut without changing decoded values", () => {
    const decoded = decodeGsTilePack(createPack(), quantization);
    const destination = allocateDecodedGsTile(2);
    copyDecodedGsTile(destination, 0, decoded);
    copyDecodedGsTile(destination, 1, decoded);
    const reference = mergeDecodedGsTiles([decoded, decoded]);

    expect(destination.count).toBe(2);
    expect(Array.from(destination.position)).toEqual(
      Array.from(reference.position),
    );
    expect(Array.from(destination.colorSh)).toEqual(
      Array.from(reference.colorSh),
    );
    expect(Array.from(destination.opacitySh)).toEqual(
      Array.from(reference.opacitySh),
    );
    expect(Array.from(destination.sourceId)).toEqual(
      Array.from(reference.sourceId),
    );
  });

  it("decodes authenticated tiles directly into a preallocated cut", () => {
    const pack = createPack();
    const decoded = decodeGsTilePack(pack, quantization);
    const destination = allocateDecodedGsTile(2);

    decodeSha256VerifiedGsTilePackTileInto(
      pack,
      32,
      96,
      1,
      quantization,
      destination,
      1,
    );

    expect(Array.from(destination.position.slice(3))).toEqual(
      Array.from(decoded.position),
    );
    expect(Array.from(destination.colorSh.slice(45))).toEqual(
      Array.from(decoded.colorSh),
    );
    expect(destination.sourceId[1]).toBe(decoded.sourceId[0]);
  });

  it("decodes authenticated tiles directly into the exact PlayCanvas columns", () => {
    const pack = createPack();
    const decoded = decodeGsTilePack(pack, quantization);
    const referenceProperties = gsTileToPlyProperties(decoded);
    const referenceOpacity = gsTileOpacityStreams(decoded);
    const destination = allocateGsTilePlayCanvasColumns(2);

    decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
      pack,
      32,
      96,
      1,
      quantization,
      destination,
      1,
    );

    expect(destination.properties.map(({ name }) => name)).toEqual(
      referenceProperties.map(({ name }) => name),
    );
    destination.properties.forEach((property, index) => {
      expect(property.storage[0]).toBe(0);
      expect(property.storage[1]).toBe(
        referenceProperties[index].storage[0],
      );
    });
    destination.opacityStreams.forEach((stream, index) => {
      expect(Array.from(stream.slice(0, 4))).toEqual([0, 0, 0, 0]);
      expect(Array.from(stream.slice(4, 8))).toEqual(
        Array.from(referenceOpacity[index]),
      );
    });
    expect("sourceId" in destination).toBe(false);
  });

  it("normalizes random Q96 quaternions bit-exactly in PlayCanvas columns", () => {
    const count = 4_096;
    const pack = createDeterministicRandomPack(count);
    const reference = decodeGsTilePack(pack, quantization);
    const referenceProperties = gsTileToPlyProperties(reference);
    const referenceOpacity = gsTileOpacityStreams(reference);
    const destination = allocateGsTilePlayCanvasColumns(count);

    decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
      pack,
      32,
      count * 96,
      count,
      quantization,
      destination,
      0,
    );

    destination.properties.forEach((property, index) => {
      expect(property.storage).toEqual(referenceProperties[index].storage);
    });
    destination.opacityStreams.forEach((stream, index) => {
      expect(stream).toEqual(referenceOpacity[index]);
    });
  });

  it("packs authenticated Q96 SH directly into exact PlayCanvas streams", () => {
    const pack = createPack();
    const decoded = decodeGsTilePack(pack, quantization);
    const referenceProperties = gsTileToPlyProperties(decoded);
    const referenceSh = referenceProperties
      .slice(-45)
      .map((property) => property.storage);
    const expected = [0, 1, 2, 3].map(
      () => new Uint32Array(8),
    ) as [Uint32Array, Uint32Array, Uint32Array, Uint32Array];
    packGsTileNativeSh(referenceSh, [
      expected[0].subarray(4),
      expected[1].subarray(4),
      expected[2].subarray(4),
      expected[3].subarray(4),
    ]);
    const destination = allocateGsTilePlayCanvasColumns(2, true);

    decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
      pack,
      32,
      96,
      1,
      quantization,
      destination,
      1,
    );

    expect(destination.shStreams).not.toBeNull();
    destination.shStreams?.forEach((stream, index) => {
      expect(stream).toEqual(expected[index]);
    });
    expect(destination.colorSh).toHaveLength(45);
    expect(destination.colorSh.every((column) => column.length === 0)).toBe(
      true,
    );
  });

  it("halves peak decoded CPU storage for the merged PlayCanvas handoff", () => {
    const count = 3;
    const decoded = allocateDecodedGsTile(count);
    const columns = allocateGsTilePlayCanvasColumns(count);
    const decodedBytes =
      decoded.position.byteLength +
      decoded.logScale.byteLength +
      decoded.rotation.byteLength +
      decoded.opacityLogit.byteLength +
      decoded.colorDc.byteLength +
      decoded.colorSh.byteLength +
      decoded.opacitySh.byteLength +
      decoded.sourceId.byteLength;
    const columnBytes =
      columns.properties.reduce(
        (total, property) => total + property.storage.byteLength,
        0,
      ) +
      columns.opacityStreams.reduce(
        (total, stream) => total + stream.byteLength,
        0,
      );

    expect(decodedBytes).toBe(count * 304);
    expect(columnBytes).toBe(count * 300);
    expect(decodedBytes + columnBytes).toBe(count * 604);
  });

  it("reduces fused SH handoff storage to 184 bytes per splat", () => {
    const count = 3;
    const columns = allocateGsTilePlayCanvasColumns(count, true);
    const bytes =
      columns.properties.reduce(
        (total, property) => total + property.storage.byteLength,
        0,
      ) +
      columns.opacityStreams.reduce(
        (total, stream) => total + stream.byteLength,
        0,
      ) +
      (columns.shStreams?.reduce(
        (total, stream) => total + stream.byteLength,
        0,
      ) ?? 0);

    expect(bytes).toBe(count * 184);
  });

  it("rejects a direct decode outside the preallocated cut", () => {
    expect(() =>
      decodeSha256VerifiedGsTilePackTileInto(
        createPack(),
        32,
        96,
        1,
        quantization,
        allocateDecodedGsTile(1),
        1,
      ),
    ).toThrow("escapes its destination");
  });

  it("rejects a columnar decode outside the preallocated cut", () => {
    expect(() =>
      decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
        createPack(),
        32,
        96,
        1,
        quantization,
        allocateGsTilePlayCanvasColumns(1),
        1,
      ),
    ).toThrow("escapes its destination");
  });

  it("rejects a decoded copy outside the preallocated cut", () => {
    const decoded = decodeGsTilePack(createPack(), quantization);
    expect(() => copyDecodedGsTile(allocateDecodedGsTile(1), 1, decoded)).toThrow(
      "escapes its destination",
    );
  });

});

it("implements the standard CRC32 polynomial", () => {
  expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
});
