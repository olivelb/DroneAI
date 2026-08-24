import { describe, expect, it } from "vitest";
import { packGsTileNativeSh } from "./native-sh";

const referencePack = (
  colorSh: readonly Float32Array[],
  streams: readonly [Uint32Array, Uint32Array, Uint32Array, Uint32Array],
) => {
  const count = colorSh[0].length;
  const t11 = (1 << 11) - 1;
  const t10 = (1 << 10) - 1;
  const maxFloat = new Float32Array(1);
  const maxBits = new Uint32Array(maxFloat.buffer);
  const coefficients = new Array(45).fill(0);
  for (let splat = 0; splat < count; splat += 1) {
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      coefficients[coefficient * 3] = colorSh[coefficient][splat];
      coefficients[coefficient * 3 + 1] = colorSh[coefficient + 15][splat];
      coefficients[coefficient * 3 + 2] = colorSh[coefficient + 30][splat];
    }
    let maximum = coefficients[0];
    for (let coefficient = 1; coefficient < 45; coefficient += 1) {
      maximum = Math.max(maximum, Math.abs(coefficients[coefficient]));
    }
    if (maximum === 0) continue;
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      coefficients[coefficient * 3] = Math.max(
        0,
        Math.min(
          t11,
          Math.floor(
            (coefficients[coefficient * 3] / maximum * 0.5 + 0.5) * t11 +
              0.5,
          ),
        ),
      );
      coefficients[coefficient * 3 + 1] = Math.max(
        0,
        Math.min(
          t10,
          Math.floor(
            (coefficients[coefficient * 3 + 1] / maximum * 0.5 + 0.5) * t10 +
              0.5,
          ),
        ),
      );
      coefficients[coefficient * 3 + 2] = Math.max(
        0,
        Math.min(
          t11,
          Math.floor(
            (coefficients[coefficient * 3 + 2] / maximum * 0.5 + 0.5) * t11 +
              0.5,
          ),
        ),
      );
    }
    const offset = splat * 4;
    maxFloat[0] = maximum;
    streams[0][offset] = maxBits[0];
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const packed = coefficient + 1;
      const index = coefficient * 3;
      streams[packed >> 2][offset + (packed & 3)] =
        (coefficients[index] << 21) |
        (coefficients[index + 1] << 11) |
        coefficients[index + 2];
    }
  }
};

describe("GSTile native SH3 packing", () => {
  it("matches PlayCanvas word-for-word across padded texture streams", () => {
    const count = 16_384;
    let state = 0x85ebca6b;
    const random = () => {
      state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
      return (state / 0xffff_ffff) * 2 - 1;
    };
    const colorSh = Array.from({ length: 45 }, () =>
      Float32Array.from({ length: count }, random),
    );
    colorSh.forEach((column) => (column[0] = 0));
    const allocate = () =>
      [0, 1, 2, 3].map(() => new Uint32Array(count * 4 + 8)) as [
        Uint32Array,
        Uint32Array,
        Uint32Array,
        Uint32Array,
      ];
    const expected = allocate();
    const actual = allocate();

    referencePack(colorSh, expected);
    packGsTileNativeSh(colorSh, actual);

    actual.forEach((stream, index) => expect(stream).toEqual(expected[index]));
  });

  it("rejects inconsistent stream shapes", () => {
    expect(() =>
      packGsTileNativeSh(
        [new Float32Array(1)],
        [
          new Uint32Array(4),
          new Uint32Array(4),
          new Uint32Array(4),
          new Uint32Array(4),
        ],
      ),
    ).toThrow(/shape is inconsistent/);
  });
});
