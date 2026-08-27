import { allocateGsTilePlayCanvasColumns, type GsTilePlayCanvasColumns } from "./decode";
import { copyGsTileNativeResult, type GsTileNativeDecodeResult } from "./native-decode";
import { gsTileTextureElementCapacity } from "./native-streams";
import { GSTILE_RECORD_BYTES } from "./pack";

export const GSTILE_ASSEMBLY_MAX_TASKS = 4;
export const GSTILE_ASSEMBLY_MAX_WORKING_BYTES = 128 * 1024 * 1024;
export const GSTILE_ASSEMBLY_MAX_DESTINATION_BYTES = 2 * 1024 * 1024 * 1024;
export const GSTILE_ASSEMBLY_MIN_RECORDS = 2_000_000;

export const gsTileNativeStreamBytes = (count: number) =>
  count * 12 + gsTileTextureElementCapacity(count) * 160;

/** Payload and decoded result can coexist in a decoder until its task ends. */
export const gsTileDecodeWorkingBytes = (count: number) =>
  count * GSTILE_RECORD_BYTES + gsTileNativeStreamBytes(count);

export const canAssembleGsTileInWorker = (capacity: number, counts: readonly number[]) =>
  Number.isSafeInteger(capacity) && capacity > 0 &&
  gsTileNativeStreamBytes(capacity) <= GSTILE_ASSEMBLY_MAX_DESTINATION_BYTES &&
  counts.length > 0 && counts.every(count =>
    Number.isSafeInteger(count) && count > 0 &&
    gsTileDecodeWorkingBytes(count) <= GSTILE_ASSEMBLY_MAX_WORKING_BYTES) &&
  counts.reduce((sum, count) => sum + count, 0) >= GSTILE_ASSEMBLY_MIN_RECORDS &&
  counts.reduce((sum, count) => sum + count, 0) <= capacity;

const validateNativeStreams = (
  count: number,
  center: Float32Array,
  transformA: Uint32Array,
  transformB: Uint16Array,
  color: Uint16Array,
  sh: readonly Uint32Array[],
  opacity: readonly Float32Array[],
) => {
  const length = gsTileTextureElementCapacity(count) * 4;
  if (
    !(center instanceof Float32Array) || center.length !== count * 3 ||
    !(transformA instanceof Uint32Array) || transformA.length !== length ||
    !(transformB instanceof Uint16Array) || transformB.length !== length ||
    !(color instanceof Uint16Array) || color.length !== length ||
    sh.length !== 4 || sh.some(stream => !(stream instanceof Uint32Array) || stream.length !== length) ||
    opacity.length !== 4 || opacity.some(stream => !(stream instanceof Float32Array) || stream.length !== length)
  ) throw new Error("GSTile native assembly streams are inconsistent");
  const streams = [center, transformA, transformB, color, ...sh, ...opacity];
  if (streams.some(stream => stream.byteOffset !== 0 || stream.byteLength !== stream.buffer.byteLength)) {
    throw new Error("GSTile native assembly rejects oversized backing buffers");
  }
  const buffers = streams.map(stream => stream.buffer);
  if (buffers.some(buffer => !(buffer instanceof ArrayBuffer)) || new Set(buffers).size !== 12) {
    throw new Error("GSTile native assembly requires twelve owned ArrayBuffers");
  }
  return buffers as ArrayBuffer[];
};

export const gsTileNativeResultBuffers = (result: GsTileNativeDecodeResult) =>
  validateNativeStreams(result.count, result.centerStream, result.transformA,
    result.transformB, result.colorStream, result.shStreams, result.opacityStreams);

export const gsTileNativeColumnBuffers = (columns: GsTilePlayCanvasColumns) => {
  if (!columns.centerStream || !columns.transformStreams || !columns.colorStream || !columns.shStreams) {
    throw new Error("GSTile assembly requires packed columns");
  }
  return validateNativeStreams(columns.count, columns.centerStream, columns.transformStreams[0],
    columns.transformStreams[1], columns.colorStream, columns.shStreams, columns.opacityStreams);
};

/** One cut, one owner, exactly one write for each planned compact range. */
export class GsTileMergedAssembler {
  #columns: GsTilePlayCanvasColumns | null;
  readonly #remaining = new Map<number, number>();

  constructor(capacity: number, counts: readonly number[]) {
    if (gsTileNativeStreamBytes(capacity) > GSTILE_ASSEMBLY_MAX_DESTINATION_BYTES || !counts.length) {
      throw new Error("GSTile assembly capacity is invalid");
    }
    let offset = 0;
    for (const count of counts) {
      if (!Number.isSafeInteger(count) || count < 1 ||
          gsTileDecodeWorkingBytes(count) > GSTILE_ASSEMBLY_MAX_WORKING_BYTES || offset + count > capacity) {
        throw new Error("GSTile assembly plan exceeds its capacity");
      }
      this.#remaining.set(offset, count);
      offset += count;
    }
    this.#columns = allocateGsTilePlayCanvasColumns(capacity, {
      color: true, centerBounds: true, sh: true, transform: true,
    });
  }

  copy(offset: number, result: GsTileNativeDecodeResult) {
    if (!this.#columns || this.#remaining.get(offset) !== result.count) {
      throw new Error("GSTile assembly range is unexpected or already written");
    }
    gsTileNativeResultBuffers(result);
    const bytes = copyGsTileNativeResult(this.#columns, offset, result);
    this.#remaining.delete(offset);
    return bytes;
  }

  finish() {
    if (!this.#columns || this.#remaining.size) {
      throw new Error("GSTile assembly is incomplete or already finished");
    }
    const columns = this.#columns;
    this.#columns = null;
    return columns;
  }
}
