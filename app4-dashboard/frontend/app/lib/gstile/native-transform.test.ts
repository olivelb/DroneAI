import { FloatPacking, Quat } from "playcanvas";
import { describe, expect, it } from "vitest";
import { packGsTileNativeTransforms } from "./native-transform";

describe("GSTile native transform packing", () => {
  it("matches PlayCanvas word-for-word without per-splat math objects", () => {
    const count = 65_536;
    let state = 0x9e3779b9;
    const random = () => {
      state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
      return state / 0xffff_ffff;
    };
    const columns = (width: number, scale: number, bias = 0) =>
      Array.from({ length: width }, () =>
        Float32Array.from(
          { length: count },
          () => (random() * 2 - 1) * scale + bias,
        ),
      );
    const position = columns(3, 1_000);
    const logScale = columns(3, 4, -4);
    const rotation = columns(4, 1);
    rotation.forEach((column) => (column[0] = 0));
    rotation[0][0] = 1;
    for (let splat = 0; splat < count; splat += 1) {
      const w = rotation[0][splat];
      const x = rotation[1][splat];
      const y = rotation[2][splat];
      const z = rotation[3][splat];
      const inverseLength = 1 / Math.sqrt(x * x + y * y + z * z + w * w);
      rotation[0][splat] = w * inverseLength;
      rotation[1][splat] = x * inverseLength;
      rotation[2][splat] = y * inverseLength;
      rotation[3][splat] = z * inverseLength;
    }
    const expectedA = new Uint32Array(count * 4 + 8);
    const expectedAFloat32 = new Float32Array(expectedA.buffer);
    const expectedB = new Uint16Array(count * 4 + 8);
    const quaternion = new Quat();
    for (let splat = 0; splat < count; splat += 1) {
      const offset = splat * 4;
      quaternion
        .set(
          rotation[1][splat],
          rotation[2][splat],
          rotation[3][splat],
          rotation[0][splat],
        )
        .normalize();
      if (quaternion.w < 0) quaternion.mulScalar(-1);
      expectedAFloat32[offset] = position[0][splat];
      expectedAFloat32[offset + 1] = position[1][splat];
      expectedAFloat32[offset + 2] = position[2][splat];
      expectedA[offset + 3] =
        FloatPacking.float2Half(quaternion.x) |
        (FloatPacking.float2Half(quaternion.y) << 16);
      expectedB[offset] = FloatPacking.float2Half(
        Math.exp(logScale[0][splat]),
      );
      expectedB[offset + 1] = FloatPacking.float2Half(
        Math.exp(logScale[1][splat]),
      );
      expectedB[offset + 2] = FloatPacking.float2Half(
        Math.exp(logScale[2][splat]),
      );
      expectedB[offset + 3] = FloatPacking.float2Half(quaternion.z);
    }
    const actualA = new Uint32Array(count * 4 + 8);
    const actualB = new Uint16Array(count * 4 + 8);

    packGsTileNativeTransforms(
      { position, logScale, rotation },
      actualA,
      actualB,
      FloatPacking.float2Half,
    );

    expect(actualA).toEqual(expectedA);
    expect(actualB).toEqual(expectedB);

    const centerStream = new Float32Array(count * 3);
    for (let splat = 0; splat < count; splat += 1) {
      centerStream[splat * 3] = position[0][splat];
      centerStream[splat * 3 + 1] = position[1][splat];
      centerStream[splat * 3 + 2] = position[2][splat];
    }
    const interleavedA = new Uint32Array(count * 4 + 8);
    const interleavedB = new Uint16Array(count * 4 + 8);
    packGsTileNativeTransforms(
      {
        position: [
          new Float32Array(0),
          new Float32Array(0),
          new Float32Array(0),
        ],
        centerStream,
        logScale,
        rotation,
      },
      interleavedA,
      interleavedB,
      FloatPacking.float2Half,
    );

    expect(interleavedA).toEqual(expectedA);
    expect(interleavedB).toEqual(expectedB);
  });

  it("rejects inconsistent stream shapes", () => {
    expect(() =>
      packGsTileNativeTransforms(
        {
          position: [new Float32Array(1)],
          logScale: [],
          rotation: [],
        },
        new Uint32Array(4),
        new Uint16Array(4),
        FloatPacking.float2Half,
      ),
    ).toThrow(/shape is inconsistent/);
  });
});
