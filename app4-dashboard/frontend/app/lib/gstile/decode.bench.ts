import { bench, describe } from "vitest";
import type { GsTileQuantization } from "./contracts";
import {
  allocateDecodedGsTile,
  allocateGsTilePlayCanvasColumns,
  copyDecodedGsTile,
  decodeSha256VerifiedGsTilePackTile,
  decodeSha256VerifiedGsTilePackTileInto,
  decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns,
  gsTileOpacityStreams,
  gsTileToPlyProperties,
  type DecodedGsTile,
} from "./decode";

const recordCount = 65_536;
const recordBytes = 96;
const headerBytes = 32;
const content = new ArrayBuffer(headerBytes + recordCount * recordBytes);
const bytes = new Uint8Array(content);
bytes.set(new TextEncoder().encode("GSTILE1\0"));
const header = new DataView(content);
header.setUint16(8, 1, true);
header.setUint16(10, headerBytes, true);
header.setUint16(12, recordBytes, true);
header.setUint32(16, recordCount, true);
for (let record = 0; record < recordCount; record += 1) {
  const base = headerBytes + record * recordBytes;
  header.setInt16(base + 12, 32_767, true);
}

const quantization: GsTileQuantization = {
  position: { min: [0, 0, 0], max: [100, 100, 100] },
  logScale: { min: [-8, -8, -8], max: [2, 2, 2] },
  rotation: { encoding: "snorm16x4" },
  opacityLogit: { min: -8, max: 8 },
  colorDcScale: [0.01, 0.01, 0.01],
  colorShScale: Array.from({ length: 45 }, () => 0.01),
  opacityShScale: Array.from({ length: 15 }, () => 0.01),
  sourceColorShDegree: 3,
  sourceOpacityShDegree: 3,
};
const destination = allocateDecodedGsTile(recordCount);
const columnDestination = allocateGsTilePlayCanvasColumns(recordCount);

const legacyGsTileToPlyProperties = (tile: DecodedGsTile) => {
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
  const addColumns = (prefix: string, source: Float32Array, width: number) => {
    for (let index = 0; index < width; index += 1) {
      add(`${prefix}${index}`, column(source, width, index));
    }
  };

  addColumns("position_", tile.position, 3);
  addColumns("color_dc_", tile.colorDc, 3);
  add("opacity", tile.opacityLogit);
  addColumns("scale_", tile.logScale, 3);
  addColumns("rotation_", tile.rotation, 4);
  addColumns("color_sh_", tile.colorSh, 45);
  return properties;
};

describe("GSTile Q96 decode", () => {
  bench("allocate, decode and copy into a final cut", () => {
    const decoded = decodeSha256VerifiedGsTilePackTile(
      content,
      headerBytes,
      recordCount * recordBytes,
      recordCount,
      quantization,
    );
    copyDecodedGsTile(destination, 0, decoded);
  });

  bench("decode directly into a final cut", () => {
    decodeSha256VerifiedGsTilePackTileInto(
      content,
      headerBytes,
      recordCount * recordBytes,
      recordCount,
      quantization,
      destination,
      0,
    );
  });
});

describe("GSTile merged PlayCanvas column conversion", () => {
  bench("legacy repeated strided scans", () => {
    legacyGsTileToPlyProperties(destination);
  });

  bench("single sequential scan per decoded field", () => {
    gsTileToPlyProperties(destination);
  });
});

describe("GSTile merged PlayCanvas final CPU pipeline", () => {
  bench("row-major decode followed by final column allocations", () => {
    decodeSha256VerifiedGsTilePackTileInto(
      content,
      headerBytes,
      recordCount * recordBytes,
      recordCount,
      quantization,
      destination,
      0,
    );
    gsTileToPlyProperties(destination);
    gsTileOpacityStreams(destination);
  });

  bench("decode directly into final PlayCanvas columns", () => {
    decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
      content,
      headerBytes,
      recordCount * recordBytes,
      recordCount,
      quantization,
      columnDestination,
      0,
    );
  });
});
