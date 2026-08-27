import type { GsTileQuantization } from "./contracts";
import type {
  GsTileOpacityStreams,
  GsTilePlayCanvasColumns,
} from "./decode";
import { packGsTileNativeColorRecord } from "./native-color";
import {
  createGsTileNativeShScratch,
  packGsTileNativeShRecord,
  type GsTileNativeShStreams,
} from "./native-sh";
import { GSTILE_RECORD_BYTES } from "./pack";
import { gsTileTextureElementCapacity } from "./native-streams";

export type GsTileNativeDecodeResult = {
  count: number;
  centerStream: Float32Array;
  transformA: Uint32Array;
  transformB: Uint16Array;
  colorStream: Uint16Array;
  shStreams: GsTileNativeShStreams;
  opacityStreams: GsTileOpacityStreams;
  bounds: {
    minimum: [number, number, number];
    maximum: [number, number, number];
    valid: boolean;
  };
};

const dequantizeU16 = (value: number, minimum: number, maximum: number) =>
  minimum + value * ((maximum - minimum) / 65_535);

/** Decode the transform subset of an already SHA-256-authenticated Q96 payload. */
export const decodeGsTileNativePayload = (
  payload: ArrayBuffer,
  recordCount: number,
  quantization: GsTileQuantization,
  float2Half?: (value: number) => number,
): GsTileNativeDecodeResult => {
  if (
    !Number.isSafeInteger(recordCount) ||
    recordCount < 1 ||
    payload.byteLength !== recordCount * GSTILE_RECORD_BYTES
  ) {
    throw new Error("GSTile transform payload shape is inconsistent");
  }
  const hasNativeFloat16 =
    !float2Half && typeof globalThis.Float16Array === "function";
  if (!hasNativeFloat16 && !float2Half) {
    throw new Error(
      "GSTile transform decoding requires Float16Array or a half-float encoder",
    );
  }

  const capacity = gsTileTextureElementCapacity(recordCount);
  const centerStream = new Float32Array(recordCount * 3);
  const transformA = new Uint32Array(capacity * 4);
  const transformB = new Uint16Array(capacity * 4);
  const colorStream = new Uint16Array(capacity * 4);
  const shStreams: GsTileNativeShStreams = [
    new Uint32Array(capacity * 4),
    new Uint32Array(capacity * 4),
    new Uint32Array(capacity * 4),
    new Uint32Array(capacity * 4),
  ];
  const opacityStreams: GsTileOpacityStreams = [
    new Float32Array(capacity * 4),
    new Float32Array(capacity * 4),
    new Float32Array(capacity * 4),
    new Float32Array(capacity * 4),
  ];
  const transformAFloat32 = new Float32Array(transformA.buffer);
  const transformAHalf = hasNativeFloat16
    ? new Float16Array(transformA.buffer)
    : null;
  const transformBHalf = hasNativeFloat16
    ? new Float16Array(transformB.buffer)
    : null;
  const view = new DataView(payload);
  const shScratch = createGsTileNativeShScratch();
  const minimum: [number, number, number] = [Infinity, Infinity, Infinity];
  const maximum: [number, number, number] = [-Infinity, -Infinity, -Infinity];

  for (let record = 0; record < recordCount; record += 1) {
    const base = record * GSTILE_RECORD_BYTES;
    const streamOffset = record * 4;
    const centerOffset = record * 3;
    const px = Math.fround(
      dequantizeU16(
        view.getUint16(base, true),
        quantization.position.min[0],
        quantization.position.max[0],
      ),
    );
    const py = Math.fround(
      dequantizeU16(
        view.getUint16(base + 2, true),
        quantization.position.min[1],
        quantization.position.max[1],
      ),
    );
    const pz = Math.fround(
      dequantizeU16(
        view.getUint16(base + 4, true),
        quantization.position.min[2],
        quantization.position.max[2],
      ),
    );
    const sx = Math.fround(
      dequantizeU16(
        view.getUint16(base + 6, true),
        quantization.logScale.min[0],
        quantization.logScale.max[0],
      ),
    );
    const sy = Math.fround(
      dequantizeU16(
        view.getUint16(base + 8, true),
        quantization.logScale.min[1],
        quantization.logScale.max[1],
      ),
    );
    const sz = Math.fround(
      dequantizeU16(
        view.getUint16(base + 10, true),
        quantization.logScale.min[2],
        quantization.logScale.max[2],
      ),
    );
    let w = view.getInt16(base + 12, true) / 32_767;
    let x = view.getInt16(base + 14, true) / 32_767;
    let y = view.getInt16(base + 16, true) / 32_767;
    let z = view.getInt16(base + 18, true) / 32_767;
    const length = Math.sqrt(w * w + x * x + y * y + z * z);
    if (!Number.isFinite(length) || length <= 1e-12) {
      throw new Error("GSTile contains an invalid quaternion");
    }
    w /= length;
    x /= length;
    y /= length;
    z /= length;
    if (w < 0) {
      x = -x;
      y = -y;
      z = -z;
    }

    centerStream[centerOffset] = px;
    centerStream[centerOffset + 1] = py;
    centerStream[centerOffset + 2] = pz;
    transformAFloat32[streamOffset] = px;
    transformAFloat32[streamOffset + 1] = py;
    transformAFloat32[streamOffset + 2] = pz;
    if (transformAHalf && transformBHalf) {
      const halfOffset = streamOffset * 2;
      transformAHalf[halfOffset + 6] = x;
      transformAHalf[halfOffset + 7] = y;
      transformBHalf[streamOffset] = Math.exp(sx);
      transformBHalf[streamOffset + 1] = Math.exp(sy);
      transformBHalf[streamOffset + 2] = Math.exp(sz);
      transformBHalf[streamOffset + 3] = z;
    } else if (float2Half) {
      transformA[streamOffset + 3] =
        float2Half(x) | (float2Half(y) << 16);
      transformB[streamOffset] = float2Half(Math.exp(sx));
      transformB[streamOffset + 1] = float2Half(Math.exp(sy));
      transformB[streamOffset + 2] = float2Half(Math.exp(sz));
      transformB[streamOffset + 3] = float2Half(z);
    }

    const baseOpacity = Math.fround(
      dequantizeU16(
        view.getUint16(base + 20, true),
        quantization.opacityLogit.min,
        quantization.opacityLogit.max,
      ),
    );
    packGsTileNativeColorRecord(
      colorStream,
      record,
      Math.fround(
        view.getInt16(base + 22, true) * quantization.colorDcScale[0],
      ),
      Math.fround(
        view.getInt16(base + 24, true) * quantization.colorDcScale[1],
      ),
      Math.fround(
        view.getInt16(base + 26, true) * quantization.colorDcScale[2],
      ),
      baseOpacity,
    );
    const opacityScale = quantization.opacityShScale;
    opacityStreams[0][streamOffset] = baseOpacity;
    opacityStreams[0][streamOffset + 1] =
      view.getInt8(base + 73) * opacityScale[0];
    opacityStreams[0][streamOffset + 2] =
      view.getInt8(base + 74) * opacityScale[1];
    opacityStreams[0][streamOffset + 3] =
      view.getInt8(base + 75) * opacityScale[2];
    opacityStreams[1][streamOffset] =
      view.getInt8(base + 76) * opacityScale[3];
    opacityStreams[1][streamOffset + 1] =
      view.getInt8(base + 77) * opacityScale[4];
    opacityStreams[1][streamOffset + 2] =
      view.getInt8(base + 78) * opacityScale[5];
    opacityStreams[1][streamOffset + 3] =
      view.getInt8(base + 79) * opacityScale[6];
    opacityStreams[2][streamOffset] =
      view.getInt8(base + 80) * opacityScale[7];
    opacityStreams[2][streamOffset + 1] =
      view.getInt8(base + 81) * opacityScale[8];
    opacityStreams[2][streamOffset + 2] =
      view.getInt8(base + 82) * opacityScale[9];
    opacityStreams[2][streamOffset + 3] =
      view.getInt8(base + 83) * opacityScale[10];
    opacityStreams[3][streamOffset] =
      view.getInt8(base + 84) * opacityScale[11];
    opacityStreams[3][streamOffset + 1] =
      view.getInt8(base + 85) * opacityScale[12];
    opacityStreams[3][streamOffset + 2] =
      view.getInt8(base + 86) * opacityScale[13];
    opacityStreams[3][streamOffset + 3] =
      view.getInt8(base + 87) * opacityScale[14];
    const colorScale = quantization.colorShScale;
    const coefficients = shScratch.coefficients;
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const index = coefficient * 3;
      coefficients[index] = Math.fround(
        view.getInt8(base + 28 + coefficient) * colorScale[coefficient],
      );
      coefficients[index + 1] = Math.fround(
        view.getInt8(base + 43 + coefficient) * colorScale[coefficient + 15],
      );
      coefficients[index + 2] = Math.fround(
        view.getInt8(base + 58 + coefficient) * colorScale[coefficient + 30],
      );
    }
    packGsTileNativeShRecord(shScratch, shStreams, record);

    const radius = 2 * Math.exp(Math.max(sx, sy, sz));
    minimum[0] = Math.min(minimum[0], px - radius);
    minimum[1] = Math.min(minimum[1], py - radius);
    minimum[2] = Math.min(minimum[2], pz - radius);
    maximum[0] = Math.max(maximum[0], px + radius);
    maximum[1] = Math.max(maximum[1], py + radius);
    maximum[2] = Math.max(maximum[2], pz + radius);
  }

  return {
    count: recordCount,
    centerStream,
    transformA,
    transformB,
    colorStream,
    shStreams,
    opacityStreams,
    bounds: { minimum, maximum, valid: true },
  };
};

export const copyGsTileNativeResult = (
  destination: GsTilePlayCanvasColumns,
  recordOffset: number,
  source: GsTileNativeDecodeResult,
) => {
  if (
    !destination.centerStream ||
    !destination.transformStreams ||
    !destination.colorStream ||
    !destination.shStreams ||
    !Number.isSafeInteger(recordOffset) ||
    recordOffset < 0 ||
    recordOffset + source.count > destination.count
  ) {
    throw new Error("GSTile transform copy escapes its destination");
  }
  const activeElements = source.count * 4;
  destination.centerStream.set(source.centerStream, recordOffset * 3);
  destination.transformStreams[0].set(
    source.transformA.subarray(0, activeElements),
    recordOffset * 4,
  );
  destination.transformStreams[1].set(
    source.transformB.subarray(0, activeElements),
    recordOffset * 4,
  );
  destination.colorStream.set(
    source.colorStream.subarray(0, activeElements),
    recordOffset * 4,
  );
  for (let stream = 0; stream < 4; stream += 1) {
    destination.shStreams[stream].set(
      source.shStreams[stream].subarray(0, activeElements),
      recordOffset * 4,
    );
    destination.opacityStreams[stream].set(
      source.opacityStreams[stream].subarray(0, activeElements),
      recordOffset * 4,
    );
  }
  // Logical active payload: centers, transform, color, four SH streams and
  // four opacity streams, totaling 172 bytes per active Gaussian.
  // Texture padding is never copied. No browser/driver internal copies counted.
  return source.count * (3 * 4 + 4 * 4 + 8 * 2 + 8 * 4 * 4);
};
