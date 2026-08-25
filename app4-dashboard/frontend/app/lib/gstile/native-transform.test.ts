import { FloatPacking, Quat } from "playcanvas";
import { describe, expect, it } from "vitest";
import { packGsTileNativeTransforms } from "./native-transform";

describe("GSTile native transform packing", () => {
  it("keeps positions exact and half-floats within one PlayCanvas ULP", () => {
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

    let positionDifferences = 0;
    let maxHalfDistance = 0;
    const compareHalf = (actual: number, expected: number) => {
      const distance = Math.abs(actual - expected);
      maxHalfDistance = Math.max(maxHalfDistance, distance);
    };
    for (let splat = 0; splat < count; splat += 1) {
      const offset = splat * 4;
      for (let axis = 0; axis < 3; axis += 1) {
        if (actualA[offset + axis] !== expectedA[offset + axis]) {
          positionDifferences += 1;
        }
      }
      compareHalf(actualA[offset + 3] & 0xffff, expectedA[offset + 3] & 0xffff);
      compareHalf(actualA[offset + 3] >>> 16, expectedA[offset + 3] >>> 16);
      for (let component = 0; component < 4; component += 1) {
        compareHalf(
          actualB[offset + component],
          expectedB[offset + component],
        );
      }
    }
    expect(positionDifferences).toBe(0);
    expect(maxHalfDistance).toBeLessThanOrEqual(1);

    const fallbackA = new Uint32Array(count * 4 + 8);
    const fallbackB = new Uint16Array(count * 4 + 8);
    packGsTileNativeTransforms(
      { position, logScale, rotation },
      fallbackA,
      fallbackB,
      FloatPacking.float2Half,
      null,
    );
    expect(fallbackA).toEqual(expectedA);
    expect(fallbackB).toEqual(expectedB);

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

    expect(interleavedA).toEqual(actualA);
    expect(interleavedB).toEqual(actualB);
  }, 15_000);

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

  it("packs only the active prefix of a capacity-sized arena", () => {
    const capacity = 4;
    const position = Array.from(
      { length: 3 },
      (_, axis) => Float32Array.from({ length: capacity }, (_, i) => i + axis),
    );
    const logScale = Array.from(
      { length: 3 },
      () => new Float32Array(capacity),
    );
    const rotation = [
      new Float32Array(capacity).fill(1),
      new Float32Array(capacity),
      new Float32Array(capacity),
      new Float32Array(capacity),
    ];
    const transformA = new Uint32Array(capacity * 4).fill(0xffff_ffff);
    const transformB = new Uint16Array(capacity * 4).fill(0xffff);

    packGsTileNativeTransforms(
      { position, logScale, rotation },
      transformA,
      transformB,
      FloatPacking.float2Half,
      null,
      { activeCount: 2 },
    );

    expect([...transformA.slice(8)]).toEqual(new Array(8).fill(0xffff_ffff));
    expect([...transformB.slice(8)]).toEqual(new Array(8).fill(0xffff));
    expect([...transformA.slice(0, 8)]).not.toEqual(
      new Array(8).fill(0xffff_ffff),
    );
  });

  it("keeps Q96-normalized rotations within one half-float ULP", () => {
    const count = 65_536;
    let state = 0x85eb_ca6b;
    const random = () => {
      state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
      return state / 0xffff_ffff;
    };
    const rotation = Array.from({ length: 4 }, () => new Float32Array(count));
    for (let splat = 0; splat < count; splat += 1) {
      const values = Array.from(
        { length: 4 },
        () => Math.round((random() * 2 - 1) * 32_767) / 32_767,
      );
      const length = Math.hypot(...values) || 1;
      for (let component = 0; component < 4; component += 1) {
        rotation[component][splat] = values[component] / length;
      }
    }
    const columns = {
      position: [
        new Float32Array(0),
        new Float32Array(0),
        new Float32Array(0),
      ],
      centerStream: new Float32Array(count * 3),
      logScale: Array.from({ length: 3 }, () => new Float32Array(count)),
      rotation,
    };
    const referenceA = new Uint32Array(count * 4);
    const referenceB = new Uint16Array(count * 4);
    const trustedA = new Uint32Array(count * 4);
    const trustedB = new Uint16Array(count * 4);

    packGsTileNativeTransforms(
      columns,
      referenceA,
      referenceB,
      FloatPacking.float2Half,
    );
    packGsTileNativeTransforms(
      columns,
      trustedA,
      trustedB,
      FloatPacking.float2Half,
      globalThis.Float16Array,
      { rotationIsNormalized: true },
    );

    let changed = 0;
    let maximumUlp = 0;
    for (let splat = 0; splat < count; splat += 1) {
      const offset = splat * 4;
      for (const [actual, expected] of [
        [trustedA[offset + 3] & 0xffff, referenceA[offset + 3] & 0xffff],
        [trustedA[offset + 3] >>> 16, referenceA[offset + 3] >>> 16],
        [trustedB[offset + 3], referenceB[offset + 3]],
      ]) {
        const distance = Math.abs(actual - expected);
        if (distance > 0) changed += 1;
        maximumUlp = Math.max(maximumUlp, distance);
      }
    }
    expect(maximumUlp).toBeLessThanOrEqual(1);
    expect(changed).toBeLessThanOrEqual(16);
  });
});
