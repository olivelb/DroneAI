import type { GsTileQuantization } from "./contracts";
import {
  decodeGsTilePackHeader,
  GSTILE_PACK_HEADER_BYTES,
  GSTILE_RECORD_BYTES,
  type GsTilePackHeader,
} from "./pack";

export type DecodedGsTile = {
  header: GsTilePackHeader;
  count: number;
  position: Float32Array;
  logScale: Float32Array;
  rotation: Float32Array;
  opacityLogit: Float32Array;
  colorDc: Float32Array;
  colorSh: Float32Array;
  opacitySh: Float32Array;
  sourceId: BigUint64Array;
};

const decodedFloatFields = [
  ["position", 3],
  ["logScale", 3],
  ["rotation", 4],
  ["opacityLogit", 1],
  ["colorDc", 3],
  ["colorSh", 45],
  ["opacitySh", 15],
] as const satisfies ReadonlyArray<
  readonly [
    Exclude<keyof DecodedGsTile, "header" | "count" | "sourceId">,
    number,
  ]
>;

/** Allocate one final decoded cut before individual nodes are loaded. */
export const allocateDecodedGsTile = (count: number): DecodedGsTile => {
  if (!Number.isSafeInteger(count) || count < 1) {
    throw new Error("GSTile decoded allocation count must be positive");
  }
  return {
    header: {
      version: 1,
      headerBytes: GSTILE_PACK_HEADER_BYTES,
      recordBytes: GSTILE_RECORD_BYTES,
      flags: 0,
      recordCount: count,
      nodeHash: BigInt(0),
      payloadCrc32: 0,
    },
    count,
    position: new Float32Array(count * 3),
    logScale: new Float32Array(count * 3),
    rotation: new Float32Array(count * 4),
    opacityLogit: new Float32Array(count),
    colorDc: new Float32Array(count * 3),
    colorSh: new Float32Array(count * 45),
    opacitySh: new Float32Array(count * 15),
    sourceId: new BigUint64Array(count),
  };
};

/** Copy one decoded node into a preallocated cut without another full merge. */
export const copyDecodedGsTile = (
  destination: DecodedGsTile,
  recordOffset: number,
  source: DecodedGsTile,
) => {
  if (
    !Number.isSafeInteger(recordOffset) ||
    recordOffset < 0 ||
    recordOffset + source.count > destination.count
  ) {
    throw new Error("GSTile decoded copy escapes its destination");
  }
  for (const [field, width] of decodedFloatFields) {
    const sourceValues = source[field];
    if (sourceValues.length !== source.count * width) {
      throw new Error("GSTile decoded field width is inconsistent");
    }
    destination[field].set(sourceValues, recordOffset * width);
  }
  if (source.sourceId.length !== source.count) {
    throw new Error("GSTile decoded source ID width is inconsistent");
  }
  destination.sourceId.set(source.sourceId, recordOffset);
};

/** Concatenate decoded tiles without changing any decoded field values. */
export const mergeDecodedGsTiles = (
  tiles: readonly DecodedGsTile[],
): DecodedGsTile => {
  if (tiles.length === 0) throw new Error("Cannot merge an empty GSTile cut");
  const count = tiles.reduce((total, tile) => total + tile.count, 0);
  const result = allocateDecodedGsTile(count);
  result.header = { ...tiles[0].header, recordCount: count };
  let recordOffset = 0;
  for (const tile of tiles) {
    copyDecodedGsTile(result, recordOffset, tile);
    recordOffset += tile.count;
  }
  return result;
};

const dequantizeU16 = (value: number, minimum: number, maximum: number) =>
  minimum + (value / 65_535) * (maximum - minimum);

const crcTable = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < table.length; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

export const crc32 = (content: ArrayBuffer | ArrayBufferView) => {
  const bytes =
    content instanceof ArrayBuffer
      ? new Uint8Array(content)
      : new Uint8Array(content.buffer, content.byteOffset, content.byteLength);
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
};

const normalizeQuaternion = (
  output: Float32Array,
  offset: number,
  w: number,
  x: number,
  y: number,
  z: number,
) => {
  const length = Math.hypot(w, x, y, z);
  if (!Number.isFinite(length) || length <= 1e-12) {
    throw new Error("GSTile contains an invalid quaternion");
  }
  output[offset] = w / length;
  output[offset + 1] = x / length;
  output[offset + 2] = y / length;
  output[offset + 3] = z / length;
};

const validatePack = (
  content: ArrayBuffer,
  verifyPayloadCrc = true,
) => {
  const header = decodeGsTilePackHeader(content);
  const expectedBytes =
    GSTILE_PACK_HEADER_BYTES + header.recordCount * GSTILE_RECORD_BYTES;
  if (content.byteLength !== expectedBytes) {
    throw new Error("GSTile pack length does not match its header");
  }
  const payload = new Uint8Array(content, GSTILE_PACK_HEADER_BYTES);
  if (verifyPayloadCrc && crc32(payload) !== header.payloadCrc32) {
    throw new Error("GSTile payload CRC mismatch");
  }
  return header;
};

const decodeRecords = (
  content: ArrayBuffer,
  header: GsTilePackHeader,
  byteOffset: number,
  count: number,
  quantization: GsTileQuantization,
): DecodedGsTile => {
  if (
    !Number.isSafeInteger(byteOffset) ||
    !Number.isSafeInteger(count) ||
    byteOffset < GSTILE_PACK_HEADER_BYTES ||
    count < 1 ||
    byteOffset % GSTILE_RECORD_BYTES !== GSTILE_PACK_HEADER_BYTES
  ) {
    throw new Error("GSTile tile range is invalid");
  }
  const byteLength = count * GSTILE_RECORD_BYTES;
  if (byteOffset + byteLength > content.byteLength) {
    throw new Error("GSTile tile range escapes its pack");
  }
  const view = new DataView(content);
  const position = new Float32Array(count * 3);
  const logScale = new Float32Array(count * 3);
  const rotation = new Float32Array(count * 4);
  const opacityLogit = new Float32Array(count);
  const colorDc = new Float32Array(count * 3);
  const colorSh = new Float32Array(count * 45);
  const opacitySh = new Float32Array(count * 15);
  const sourceId = new BigUint64Array(count);

  for (let record = 0; record < count; record += 1) {
    const base = byteOffset + record * GSTILE_RECORD_BYTES;
    for (let axis = 0; axis < 3; axis += 1) {
      position[record * 3 + axis] = dequantizeU16(
        view.getUint16(base + axis * 2, true),
        quantization.position.min[axis],
        quantization.position.max[axis],
      );
      logScale[record * 3 + axis] = dequantizeU16(
        view.getUint16(base + 6 + axis * 2, true),
        quantization.logScale.min[axis],
        quantization.logScale.max[axis],
      );
      colorDc[record * 3 + axis] =
        view.getInt16(base + 22 + axis * 2, true) *
        quantization.colorDcScale[axis];
    }
    normalizeQuaternion(
      rotation,
      record * 4,
      view.getInt16(base + 12, true) / 32_767,
      view.getInt16(base + 14, true) / 32_767,
      view.getInt16(base + 16, true) / 32_767,
      view.getInt16(base + 18, true) / 32_767,
    );
    opacityLogit[record] = dequantizeU16(
      view.getUint16(base + 20, true),
      quantization.opacityLogit.min,
      quantization.opacityLogit.max,
    );
    for (let coefficient = 0; coefficient < 45; coefficient += 1) {
      colorSh[record * 45 + coefficient] =
        view.getInt8(base + 28 + coefficient) *
        quantization.colorShScale[coefficient];
    }
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      opacitySh[record * 15 + coefficient] =
        view.getInt8(base + 73 + coefficient) *
        quantization.opacityShScale[coefficient];
    }
    sourceId[record] = view.getBigUint64(base + 88, true);
  }

  return {
    header,
    count,
    position,
    logScale,
    rotation,
    opacityLogit,
    colorDc,
    colorSh,
    opacitySh,
    sourceId,
  };
};

/** Decode one complete v1 pack into the exact DroneGS property convention. */
export const decodeGsTilePack = (
  content: ArrayBuffer,
  quantization: GsTileQuantization,
): DecodedGsTile => {
  const header = validatePack(content);
  return decodeRecords(
    content,
    header,
    GSTILE_PACK_HEADER_BYTES,
    header.recordCount,
    quantization,
  );
};

/** Decode a tile range from a validated pack (supports future aggregated packs). */
export const decodeGsTilePackTile = (
  content: ArrayBuffer,
  byteOffset: number,
  byteLength: number,
  recordCount: number,
  quantization: GsTileQuantization,
) => {
  const header = validatePack(content);
  if (byteLength !== recordCount * GSTILE_RECORD_BYTES) {
    throw new Error("GSTile tile byte length does not match its record count");
  }
  return decodeRecords(
    content,
    header,
    byteOffset,
    recordCount,
    quantization,
  );
};

/** Decode a tile after the caller authenticated the complete pack by SHA-256. */
export const decodeSha256VerifiedGsTilePackTile = (
  content: ArrayBuffer,
  byteOffset: number,
  byteLength: number,
  recordCount: number,
  quantization: GsTileQuantization,
) => {
  // SHA-256 already authenticates every payload byte. Repeating the CRC32
  // pass here would synchronously scan hundreds of MiB on every cached LOD
  // reconstruction without detecting anything SHA-256 did not detect.
  const header = validatePack(content, false);
  if (byteLength !== recordCount * GSTILE_RECORD_BYTES) {
    throw new Error("GSTile tile byte length does not match its record count");
  }
  return decodeRecords(
    content,
    header,
    byteOffset,
    recordCount,
    quantization,
  );
};

/** Convert row-major decoded fields to PlayCanvas' PLY-compatible column arrays. */
export const gsTileToPlyProperties = (tile: DecodedGsTile) => {
  const properties: Array<{
    name: string;
    type: "float";
    byteSize: 4;
    storage: Float32Array;
  }> = [];
  const column = (source: Float32Array, width: number, index: number) => {
    const result = new Float32Array(tile.count);
    for (let row = 0; row < tile.count; row += 1) {
      result[row] = source[row * width + index];
    }
    return result;
  };
  const add = (name: string, storage: Float32Array) =>
    properties.push({ name, type: "float", byteSize: 4, storage });

  add("x", column(tile.position, 3, 0));
  add("y", column(tile.position, 3, 1));
  add("z", column(tile.position, 3, 2));
  add("f_dc_0", column(tile.colorDc, 3, 0));
  add("f_dc_1", column(tile.colorDc, 3, 1));
  add("f_dc_2", column(tile.colorDc, 3, 2));
  add("opacity", tile.opacityLogit);
  add("scale_0", column(tile.logScale, 3, 0));
  add("scale_1", column(tile.logScale, 3, 1));
  add("scale_2", column(tile.logScale, 3, 2));
  add("rot_0", column(tile.rotation, 4, 0));
  add("rot_1", column(tile.rotation, 4, 1));
  add("rot_2", column(tile.rotation, 4, 2));
  add("rot_3", column(tile.rotation, 4, 3));
  for (let coefficient = 0; coefficient < 45; coefficient += 1) {
    add(`f_rest_${coefficient}`, column(tile.colorSh, 45, coefficient));
  }
  return properties;
};

/** RGBA32F resource streams: base logit followed by all 15 opacity-SH values. */
export const gsTileOpacityStreams = (tile: DecodedGsTile) => {
  const streams = Array.from(
    { length: 4 },
    () => new Float32Array(tile.count * 4),
  );
  for (let record = 0; record < tile.count; record += 1) {
    streams[0][record * 4] = tile.opacityLogit[record];
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const packed = coefficient + 1;
      streams[Math.floor(packed / 4)][record * 4 + (packed % 4)] =
        tile.opacitySh[record * 15 + coefficient];
    }
  }
  return streams;
};
