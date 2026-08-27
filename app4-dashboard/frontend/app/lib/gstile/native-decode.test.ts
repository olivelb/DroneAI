import { FloatPacking } from "playcanvas";
import { describe, expect, it } from "vitest";
import type { GsTileQuantization } from "./contracts";
import {
  allocateGsTilePlayCanvasColumns,
  decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns,
} from "./decode";
import { calculateMergedArenaBounds } from "./merged-arena";
import {
  copyGsTileNativeResult,
  decodeGsTileNativePayload,
} from "./native-decode";
import { gsTileTextureElementCapacity } from "./native-streams";
import { packGsTileNativeTransforms } from "./native-transform";
import { GSTILE_PACK_HEADER_BYTES } from "./pack";

const quantization: GsTileQuantization = {
  position: { min: [-800, -250, 30], max: [1_200, 900, 410] },
  logScale: { min: [-9, -8, -7], max: [2, 1, 0] },
  rotation: { encoding: "snorm16x4" },
  opacityLogit: { min: -10, max: 10 },
  colorDcScale: [1, 1, 1],
  colorShScale: new Array(45).fill(1),
  opacityShScale: new Array(15).fill(1),
  sourceColorShDegree: 3,
  sourceOpacityShDegree: 3,
};

describe("GSTile native transform payload decoding", () => {
  it("matches the existing Q96 decode and native packer exactly", () => {
    const count = 16_384;
    const payload = new ArrayBuffer(count * 96);
    const view = new DataView(payload);
    let state = 0x27d4_eb2d;
    const randomU16 = () => {
      state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
      return state & 0xffff;
    };
    for (let record = 0; record < count; record += 1) {
      const base = record * 96;
      for (let component = 0; component < 6; component += 1) {
        view.setUint16(base + component * 2, randomU16(), true);
      }
      for (let component = 0; component < 4; component += 1) {
        const value = (randomU16() % 65_535) - 32_767;
        view.setInt16(base + 12 + component * 2, value, true);
      }
      if (
        view.getInt16(base + 12, true) === 0 &&
        view.getInt16(base + 14, true) === 0 &&
        view.getInt16(base + 16, true) === 0 &&
        view.getInt16(base + 18, true) === 0
      ) {
        view.setInt16(base + 12, 1, true);
      }
    }

    const dequantize = (value: number, minimum: number, maximum: number) =>
      minimum + value * ((maximum - minimum) / 65_535);
    const centerStream = new Float32Array(count * 3);
    const logScale = Array.from({ length: 3 }, () => new Float32Array(count));
    const rotation = Array.from({ length: 4 }, () => new Float32Array(count));
    for (let record = 0; record < count; record += 1) {
      const base = record * 96;
      for (let axis = 0; axis < 3; axis += 1) {
        centerStream[record * 3 + axis] = Math.fround(
          dequantize(
            view.getUint16(base + axis * 2, true),
            quantization.position.min[axis],
            quantization.position.max[axis],
          ),
        );
        logScale[axis][record] = dequantize(
          view.getUint16(base + 6 + axis * 2, true),
          quantization.logScale.min[axis],
          quantization.logScale.max[axis],
        );
      }
      const values = [12, 14, 16, 18].map(
        (offset) => view.getInt16(base + offset, true) / 32_767,
      );
      const length = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
      for (let component = 0; component < 4; component += 1) {
        rotation[component][record] = values[component] / length;
      }
    }
    const capacity = gsTileTextureElementCapacity(count);
    const expectedA = new Uint32Array(capacity * 4);
    const expectedB = new Uint16Array(capacity * 4);
    packGsTileNativeTransforms(
      {
        position: Array.from({ length: 3 }, () => new Float32Array(0)),
        centerStream,
        logScale,
        rotation,
      },
      expectedA,
      expectedB,
      FloatPacking.float2Half,
      null,
      { rotationIsNormalized: true },
    );

    const actual = decodeGsTileNativePayload(
      payload,
      count,
      quantization,
      FloatPacking.float2Half,
    );

    const pack = new ArrayBuffer(GSTILE_PACK_HEADER_BYTES + payload.byteLength);
    const packBytes = new Uint8Array(pack);
    packBytes.set([71, 83, 84, 73, 76, 69, 49, 0]);
    const packView = new DataView(pack);
    packView.setUint16(8, 1, true);
    packView.setUint16(10, GSTILE_PACK_HEADER_BYTES, true);
    packView.setUint16(12, 96, true);
    packView.setUint32(16, count, true);
    packBytes.set(new Uint8Array(payload), GSTILE_PACK_HEADER_BYTES);
    const expectedStreams = allocateGsTilePlayCanvasColumns(count, {
      centerBounds: true,
      color: true,
      sh: true,
    });
    decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
      pack,
      GSTILE_PACK_HEADER_BYTES,
      payload.byteLength,
      count,
      quantization,
      expectedStreams,
      0,
    );

    expect(actual.centerStream).toEqual(centerStream);
    expect(actual.transformA).toEqual(expectedA);
    expect(actual.transformB).toEqual(expectedB);
    expect(actual.colorStream).toEqual(expectedStreams.colorStream);
    expect(actual.shStreams).toEqual(expectedStreams.shStreams);
    expect(actual.opacityStreams).toEqual(expectedStreams.opacityStreams);
    const expectedBounds = calculateMergedArenaBounds(
      Array.from({ length: 3 }, () => new Float32Array(0)),
      logScale,
      0,
      count,
      centerStream,
    );
    expect(actual.bounds).toEqual({
      minimum: expectedBounds.min,
      maximum: expectedBounds.max,
      valid: true,
    });
  });

  it("copies only active texels into a bounded destination range", () => {
    const source = decodeGsTileNativePayload(
      new Uint8Array(96).map((_, index) => (index === 12 ? 1 : 0)).buffer,
      1,
      quantization,
      FloatPacking.float2Half,
    );
    const destination = allocateGsTilePlayCanvasColumns(3, {
      centerBounds: true,
      color: true,
      sh: true,
      transform: true,
    });
    expect(copyGsTileNativeResult(destination, 1, source)).toBe(172);
    expect(destination.centerStream?.slice(3, 6)).toEqual(source.centerStream);
    expect(destination.transformStreams?.[0].slice(4, 8)).toEqual(
      source.transformA.slice(0, 4),
    );
    expect(destination.transformStreams?.[1].slice(4, 8)).toEqual(
      source.transformB.slice(0, 4),
    );
    expect(destination.colorStream?.slice(4, 8)).toEqual(
      source.colorStream.slice(0, 4),
    );
    for (let stream = 0; stream < 4; stream += 1) {
      expect(destination.shStreams?.[stream].slice(4, 8)).toEqual(
        source.shStreams[stream].slice(0, 4),
      );
      expect(destination.opacityStreams[stream].slice(4, 8)).toEqual(
        source.opacityStreams[stream].slice(0, 4),
      );
    }
  });

  it("bounds the merged handoff to 172 bytes per active splat", () => {
    const count = 3;
    const columns = allocateGsTilePlayCanvasColumns(count, {
      centerBounds: true,
      color: true,
      sh: true,
      transform: true,
    });
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
      ) ?? 0) +
      (columns.colorStream?.byteLength ?? 0) +
      (columns.centerStream?.byteLength ?? 0) +
      (columns.transformStreams?.reduce(
        (total, stream) => total + stream.byteLength,
        0,
      ) ?? 0);
    const padding = gsTileTextureElementCapacity(count) - count;
    expect(bytes).toBe(count * 172 + padding * 160);
  });
});
