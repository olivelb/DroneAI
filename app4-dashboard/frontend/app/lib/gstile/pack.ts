export const GSTILE_PACK_HEADER_BYTES = 32;
export const GSTILE_RECORD_BYTES = 96;

export type GsTilePackHeader = {
  version: number;
  headerBytes: number;
  recordBytes: number;
  flags: number;
  recordCount: number;
  nodeHash: bigint;
  payloadCrc32: number;
};

export const decodeGsTilePackHeader = (buffer: ArrayBuffer): GsTilePackHeader => {
  if (buffer.byteLength < GSTILE_PACK_HEADER_BYTES) {
    throw new Error("GSTile pack header is truncated");
  }
  const bytes = new Uint8Array(buffer, 0, 8);
  const magic = String.fromCharCode(...bytes);
  if (magic !== "GSTILE1\0") throw new Error("GSTile pack magic is invalid");
  const view = new DataView(buffer);
  const header = {
    version: view.getUint16(8, true),
    headerBytes: view.getUint16(10, true),
    recordBytes: view.getUint16(12, true),
    flags: view.getUint16(14, true),
    recordCount: view.getUint32(16, true),
    nodeHash: view.getBigUint64(20, true),
    payloadCrc32: view.getUint32(28, true),
  };
  if (
    header.version !== 1 ||
    header.headerBytes !== GSTILE_PACK_HEADER_BYTES ||
    header.recordBytes !== GSTILE_RECORD_BYTES ||
    header.flags !== 0 ||
    header.recordCount < 1
  ) {
    throw new Error("GSTile pack layout is unsupported");
  }
  return header;
};
