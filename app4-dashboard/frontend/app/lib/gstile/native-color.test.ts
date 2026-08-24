import { FloatPacking } from "playcanvas";
import { describe, expect, it } from "vitest";
import { packGsTileNativeColorRecord } from "./native-color";

describe("GSTile native base-color packing", () => {
  it("matches PlayCanvas word-for-word across representative Q96 values", () => {
    const count = 65_536;
    const expected = new Uint16Array(count * 4 + 8);
    const actual = new Uint16Array(count * 4 + 8);
    const shC0 = 0.28209479177387814;
    let state = 0x27d4eb2d;
    const random = () => {
      state = (Math.imul(state, 1_664_525) + 1_013_904_223) >>> 0;
      return state / 0xffff_ffff;
    };
    for (let splat = 0; splat < count; splat += 1) {
      const red = Math.fround((random() * 2 - 1) * 32);
      const green = Math.fround((random() * 2 - 1) * 32);
      const blue = Math.fround((random() * 2 - 1) * 32);
      const opacityLogit = Math.fround((random() * 2 - 1) * 16);
      const offset = splat * 4;
      expected[offset] = FloatPacking.float2Half(red * shC0 + 0.5);
      expected[offset + 1] = FloatPacking.float2Half(green * shC0 + 0.5);
      expected[offset + 2] = FloatPacking.float2Half(blue * shC0 + 0.5);
      expected[offset + 3] = FloatPacking.float2Half(
        1 / (1 + Math.exp(-opacityLogit)),
      );
      packGsTileNativeColorRecord(
        actual,
        splat,
        red,
        green,
        blue,
        opacityLogit,
      );
    }

    expect(actual).toEqual(expected);
  });
});
