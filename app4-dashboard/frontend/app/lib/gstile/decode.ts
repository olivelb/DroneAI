import type { GsTileQuantization } from "./contracts";
import {
  decodeGsTilePackHeader,
  GSTILE_PACK_HEADER_BYTES,
  GSTILE_RECORD_BYTES,
  type GsTilePackHeader,
} from "./pack";
import {
  createGsTileNativeShScratch,
  packGsTileNativeShRecord,
  type GsTileNativeShStreams,
} from "./native-sh";

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

export type GsTilePlyProperty = {
  name: string;
  type: "float";
  byteSize: 4;
  storage: Float32Array;
};

export type GsTileOpacityStreams = [
  Float32Array,
  Float32Array,
  Float32Array,
  Float32Array,
];

type GsTilePlyColumns = {
  position: Float32Array[];
  colorDc: Float32Array[];
  opacityLogit: Float32Array;
  logScale: Float32Array[];
  rotation: Float32Array[];
  colorSh: Float32Array[];
};

/**
 * Final CPU representation consumed by PlayCanvas for one monolithic cut.
 * Source IDs are intentionally absent: the renderer never uploads them.
 */
export type GsTilePlayCanvasColumns = GsTilePlyColumns & {
  count: number;
  properties: GsTilePlyProperty[];
  opacityStreams: GsTileOpacityStreams;
  shStreams: GsTileNativeShStreams | null;
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

const validateDecodedRange = (
  destination: DecodedGsTile,
  recordOffset: number,
  count: number,
) => {
  if (
    !Number.isSafeInteger(recordOffset) ||
    recordOffset < 0 ||
    recordOffset + count > destination.count
  ) {
    throw new Error("GSTile decoded range escapes its destination");
  }
  for (const [field, width] of decodedFloatFields) {
    if (destination[field].length !== destination.count * width) {
      throw new Error("GSTile decoded field width is inconsistent");
    }
  }
  if (destination.sourceId.length !== destination.count) {
    throw new Error("GSTile decoded source ID width is inconsistent");
  }
};

const validatePlayCanvasColumnRange = (
  destination: GsTilePlayCanvasColumns,
  recordOffset: number,
  count: number,
) => {
  if (
    !Number.isSafeInteger(recordOffset) ||
    recordOffset < 0 ||
    recordOffset + count > destination.count
  ) {
    throw new Error("GSTile PlayCanvas column range escapes its destination");
  }
  const groups: ReadonlyArray<readonly [Float32Array[], number]> = [
    [destination.position, 3],
    [destination.colorDc, 3],
    [destination.logScale, 3],
    [destination.rotation, 4],
  ];
  for (const [columns, width] of groups) {
    if (
      columns.length !== width ||
      columns.some((column) => column.length !== destination.count)
    ) {
      throw new Error("GSTile PlayCanvas column width is inconsistent");
    }
  }
  if (
    destination.colorSh.length !== 45 ||
    (destination.shStreams === null &&
      destination.colorSh.some((column) => column.length !== destination.count)) ||
    (destination.shStreams !== null &&
      destination.shStreams.some(
        (stream) => stream.length !== destination.count * 4,
      ))
  ) {
    throw new Error("GSTile PlayCanvas SH width is inconsistent");
  }
  if (
    destination.opacityLogit.length !== destination.count ||
    destination.opacityStreams.length !== 4 ||
    destination.opacityStreams.some(
      (stream) => stream.length !== destination.count * 4,
    )
  ) {
    throw new Error("GSTile PlayCanvas opacity stream width is inconsistent");
  }
};

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

const gsTilePlyPropertiesFromColumns = (
  columns: GsTilePlyColumns,
): GsTilePlyProperty[] => {
  const properties: GsTilePlyProperty[] = [];
  const add = (name: string, storage: Float32Array) =>
    properties.push({ name, type: "float", byteSize: 4, storage });
  const addColumns = (
    names: readonly string[],
    storage: readonly Float32Array[],
  ) => {
    if (names.length !== storage.length) {
      throw new Error("GSTile PlayCanvas column schema is inconsistent");
    }
    names.forEach((name, index) => add(name, storage[index]));
  };

  addColumns(["x", "y", "z"], columns.position);
  addColumns(["f_dc_0", "f_dc_1", "f_dc_2"], columns.colorDc);
  add("opacity", columns.opacityLogit);
  addColumns(["scale_0", "scale_1", "scale_2"], columns.logScale);
  addColumns(["rot_0", "rot_1", "rot_2", "rot_3"], columns.rotation);
  addColumns(
    Array.from({ length: 45 }, (_, coefficient) =>
      `f_rest_${coefficient}`,
    ),
    columns.colorSh,
  );
  return properties;
};

/** Allocate the final column-major CPU cut consumed by PlayCanvas. */
export const allocateGsTilePlayCanvasColumns = (
  count: number,
  packShDuringDecode = false,
): GsTilePlayCanvasColumns => {
  if (!Number.isSafeInteger(count) || count < 1) {
    throw new Error("GSTile PlayCanvas column count must be positive");
  }
  const columns = (width: number) =>
    Array.from({ length: width }, () => new Float32Array(count));
  const position = columns(3);
  const colorDc = columns(3);
  const opacityLogit = new Float32Array(count);
  const logScale = columns(3);
  const rotation = columns(4);
  const colorSh = packShDuringDecode
    ? Array.from({ length: 45 }, () => new Float32Array(0))
    : columns(45);
  const opacityStreams: GsTileOpacityStreams = [
    new Float32Array(count * 4),
    new Float32Array(count * 4),
    new Float32Array(count * 4),
    new Float32Array(count * 4),
  ];
  const shStreams: GsTileNativeShStreams | null = packShDuringDecode
    ? [
        new Uint32Array(count * 4),
        new Uint32Array(count * 4),
        new Uint32Array(count * 4),
        new Uint32Array(count * 4),
      ]
    : null;
  const plyColumns = {
    position,
    colorDc,
    opacityLogit,
    logScale,
    rotation,
    colorSh,
  };
  return {
    count,
    ...plyColumns,
    properties: gsTilePlyPropertiesFromColumns(plyColumns),
    opacityStreams,
    shStreams,
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

const normalizedQuaternionLength = (
  w: number,
  x: number,
  y: number,
  z: number,
) => {
  const length = Math.hypot(w, x, y, z);
  if (!Number.isFinite(length) || length <= 1e-12) {
    throw new Error("GSTile contains an invalid quaternion");
  }
  return length;
};

// Q96 snorm16 components are bounded to [-1, 1], so a direct squared sum
// cannot overflow or underflow and avoids Math.hypot's general scaling path.
const normalizedBoundedQuaternionLength = (
  w: number,
  x: number,
  y: number,
  z: number,
) => {
  const length = Math.sqrt(w * w + x * x + y * y + z * z);
  if (!Number.isFinite(length) || length <= 1e-12) {
    throw new Error("GSTile contains an invalid quaternion");
  }
  return length;
};

const normalizeQuaternion = (
  output: Float32Array,
  offset: number,
  w: number,
  x: number,
  y: number,
  z: number,
) => {
  const length = normalizedQuaternionLength(w, x, y, z);
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

const validateRecordRange = (
  content: ArrayBuffer,
  byteOffset: number,
  count: number,
) => {
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
};

const decodeRecords = (
  content: ArrayBuffer,
  header: GsTilePackHeader,
  byteOffset: number,
  count: number,
  quantization: GsTileQuantization,
  destination?: DecodedGsTile,
  recordOffset = 0,
): DecodedGsTile => {
  validateRecordRange(content, byteOffset, count);
  const output = destination ?? allocateDecodedGsTile(count);
  validateDecodedRange(output, recordOffset, count);
  if (!destination) output.header = header;
  const view = new DataView(content);
  const {
    position,
    logScale,
    rotation,
    opacityLogit,
    colorDc,
    colorSh,
    opacitySh,
    sourceId,
  } = output;

  for (let record = 0; record < count; record += 1) {
    const base = byteOffset + record * GSTILE_RECORD_BYTES;
    const targetRecord = recordOffset + record;
    for (let axis = 0; axis < 3; axis += 1) {
      position[targetRecord * 3 + axis] = dequantizeU16(
        view.getUint16(base + axis * 2, true),
        quantization.position.min[axis],
        quantization.position.max[axis],
      );
      logScale[targetRecord * 3 + axis] = dequantizeU16(
        view.getUint16(base + 6 + axis * 2, true),
        quantization.logScale.min[axis],
        quantization.logScale.max[axis],
      );
      colorDc[targetRecord * 3 + axis] =
        view.getInt16(base + 22 + axis * 2, true) *
        quantization.colorDcScale[axis];
    }
    normalizeQuaternion(
      rotation,
      targetRecord * 4,
      view.getInt16(base + 12, true) / 32_767,
      view.getInt16(base + 14, true) / 32_767,
      view.getInt16(base + 16, true) / 32_767,
      view.getInt16(base + 18, true) / 32_767,
    );
    opacityLogit[targetRecord] = dequantizeU16(
      view.getUint16(base + 20, true),
      quantization.opacityLogit.min,
      quantization.opacityLogit.max,
    );
    for (let coefficient = 0; coefficient < 45; coefficient += 1) {
      colorSh[targetRecord * 45 + coefficient] =
        view.getInt8(base + 28 + coefficient) *
        quantization.colorShScale[coefficient];
    }
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      opacitySh[targetRecord * 15 + coefficient] =
        view.getInt8(base + 73 + coefficient) *
        quantization.opacityShScale[coefficient];
    }
    sourceId[targetRecord] = view.getBigUint64(base + 88, true);
  }

  return output;
};

const decodeRecordsIntoPlayCanvasColumns = (
  content: ArrayBuffer,
  byteOffset: number,
  count: number,
  quantization: GsTileQuantization,
  destination: GsTilePlayCanvasColumns,
  recordOffset: number,
) => {
  validateRecordRange(content, byteOffset, count);
  validatePlayCanvasColumnRange(destination, recordOffset, count);
  const view = new DataView(content);
  const {
    position,
    logScale,
    rotation,
    opacityLogit,
    colorDc,
    colorSh,
    opacityStreams,
    shStreams,
  } = destination;
  const shScratch = shStreams ? createGsTileNativeShScratch() : null;

  for (let record = 0; record < count; record += 1) {
    const base = byteOffset + record * GSTILE_RECORD_BYTES;
    const targetRecord = recordOffset + record;
    for (let axis = 0; axis < 3; axis += 1) {
      position[axis][targetRecord] = dequantizeU16(
        view.getUint16(base + axis * 2, true),
        quantization.position.min[axis],
        quantization.position.max[axis],
      );
      logScale[axis][targetRecord] = dequantizeU16(
        view.getUint16(base + 6 + axis * 2, true),
        quantization.logScale.min[axis],
        quantization.logScale.max[axis],
      );
      colorDc[axis][targetRecord] =
        view.getInt16(base + 22 + axis * 2, true) *
        quantization.colorDcScale[axis];
    }
    const w = view.getInt16(base + 12, true) / 32_767;
    const x = view.getInt16(base + 14, true) / 32_767;
    const y = view.getInt16(base + 16, true) / 32_767;
    const z = view.getInt16(base + 18, true) / 32_767;
    const quaternionLength = normalizedBoundedQuaternionLength(w, x, y, z);
    rotation[0][targetRecord] = w / quaternionLength;
    rotation[1][targetRecord] = x / quaternionLength;
    rotation[2][targetRecord] = y / quaternionLength;
    rotation[3][targetRecord] = z / quaternionLength;
    const baseOpacity = dequantizeU16(
      view.getUint16(base + 20, true),
      quantization.opacityLogit.min,
      quantization.opacityLogit.max,
    );
    opacityLogit[targetRecord] = baseOpacity;
    const opacityOffset = targetRecord * 4;
    const opacityScale = quantization.opacityShScale;
    opacityStreams[0][opacityOffset] = baseOpacity;
    opacityStreams[0][opacityOffset + 1] =
      view.getInt8(base + 73) * opacityScale[0];
    opacityStreams[0][opacityOffset + 2] =
      view.getInt8(base + 74) * opacityScale[1];
    opacityStreams[0][opacityOffset + 3] =
      view.getInt8(base + 75) * opacityScale[2];
    opacityStreams[1][opacityOffset] =
      view.getInt8(base + 76) * opacityScale[3];
    opacityStreams[1][opacityOffset + 1] =
      view.getInt8(base + 77) * opacityScale[4];
    opacityStreams[1][opacityOffset + 2] =
      view.getInt8(base + 78) * opacityScale[5];
    opacityStreams[1][opacityOffset + 3] =
      view.getInt8(base + 79) * opacityScale[6];
    opacityStreams[2][opacityOffset] =
      view.getInt8(base + 80) * opacityScale[7];
    opacityStreams[2][opacityOffset + 1] =
      view.getInt8(base + 81) * opacityScale[8];
    opacityStreams[2][opacityOffset + 2] =
      view.getInt8(base + 82) * opacityScale[9];
    opacityStreams[2][opacityOffset + 3] =
      view.getInt8(base + 83) * opacityScale[10];
    opacityStreams[3][opacityOffset] =
      view.getInt8(base + 84) * opacityScale[11];
    opacityStreams[3][opacityOffset + 1] =
      view.getInt8(base + 85) * opacityScale[12];
    opacityStreams[3][opacityOffset + 2] =
      view.getInt8(base + 86) * opacityScale[13];
    opacityStreams[3][opacityOffset + 3] =
      view.getInt8(base + 87) * opacityScale[14];
    const colorScale = quantization.colorShScale;
    if (shStreams && shScratch) {
      const coefficients = shScratch.coefficients;
      for (let coefficient = 0; coefficient < 15; coefficient += 1) {
        const index = coefficient * 3;
        coefficients[index] = Math.fround(
          view.getInt8(base + 28 + coefficient) * colorScale[coefficient],
        );
        coefficients[index + 1] = Math.fround(
          view.getInt8(base + 43 + coefficient) *
            colorScale[coefficient + 15],
        );
        coefficients[index + 2] = Math.fround(
          view.getInt8(base + 58 + coefficient) *
            colorScale[coefficient + 30],
        );
      }
      packGsTileNativeShRecord(shScratch, shStreams, targetRecord);
    } else {
      for (let coefficient = 0; coefficient < 45; coefficient += 3) {
        colorSh[coefficient][targetRecord] =
          view.getInt8(base + 28 + coefficient) * colorScale[coefficient];
        colorSh[coefficient + 1][targetRecord] =
          view.getInt8(base + 29 + coefficient) * colorScale[coefficient + 1];
        colorSh[coefficient + 2][targetRecord] =
          view.getInt8(base + 30 + coefficient) * colorScale[coefficient + 2];
      }
    }
  }
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

/** Decode authenticated records directly into a final preallocated cut. */
export const decodeSha256VerifiedGsTilePackTileInto = (
  content: ArrayBuffer,
  byteOffset: number,
  byteLength: number,
  recordCount: number,
  quantization: GsTileQuantization,
  destination: DecodedGsTile,
  recordOffset: number,
) => {
  const header = validatePack(content, false);
  if (byteLength !== recordCount * GSTILE_RECORD_BYTES) {
    throw new Error("GSTile tile byte length does not match its record count");
  }
  decodeRecords(
    content,
    header,
    byteOffset,
    recordCount,
    quantization,
    destination,
    recordOffset,
  );
};

/** Decode authenticated records directly into PlayCanvas' final CPU layout. */
export const decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns = (
  content: ArrayBuffer,
  byteOffset: number,
  byteLength: number,
  recordCount: number,
  quantization: GsTileQuantization,
  destination: GsTilePlayCanvasColumns,
  recordOffset: number,
) => {
  validatePack(content, false);
  if (byteLength !== recordCount * GSTILE_RECORD_BYTES) {
    throw new Error("GSTile tile byte length does not match its record count");
  }
  decodeRecordsIntoPlayCanvasColumns(
    content,
    byteOffset,
    recordCount,
    quantization,
    destination,
    recordOffset,
  );
};

/** Convert row-major decoded fields to PlayCanvas' PLY-compatible column arrays. */
export const gsTileToPlyProperties = (tile: DecodedGsTile) => {
  const columns = (source: Float32Array, width: number) => {
    const result = Array.from(
      { length: width },
      () => new Float32Array(tile.count),
    );
    for (let row = 0; row < tile.count; row += 1) {
      const sourceOffset = row * width;
      for (let index = 0; index < width; index += 1) {
        result[index][row] = source[sourceOffset + index];
      }
    }
    return result;
  };
  const position = columns(tile.position, 3);
  const colorDc = columns(tile.colorDc, 3);
  const logScale = columns(tile.logScale, 3);
  const rotation = columns(tile.rotation, 4);
  const colorSh = columns(tile.colorSh, 45);
  return gsTilePlyPropertiesFromColumns({
    position,
    colorDc,
    opacityLogit: tile.opacityLogit,
    logScale,
    rotation,
    colorSh,
  });
};

/** RGBA32F resource streams: base logit followed by all 15 opacity-SH values. */
export const gsTileOpacityStreams = (tile: DecodedGsTile) => {
  const streams: GsTileOpacityStreams = [
    new Float32Array(tile.count * 4),
    new Float32Array(tile.count * 4),
    new Float32Array(tile.count * 4),
    new Float32Array(tile.count * 4),
  ];
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
