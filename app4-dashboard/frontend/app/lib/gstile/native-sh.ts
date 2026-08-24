export type GsTileNativeShStreams = readonly [
  Uint32Array,
  Uint32Array,
  Uint32Array,
  Uint32Array,
];

export type GsTileNativeShScratch = {
  coefficients: number[];
  maximumFloat: Float32Array;
  maximumBits: Uint32Array;
};

export const createGsTileNativeShScratch = (): GsTileNativeShScratch => {
  const maximumFloat = new Float32Array(1);
  return {
    coefficients: new Array<number>(45).fill(0),
    maximumFloat,
    maximumBits: new Uint32Array(maximumFloat.buffer),
  };
};

/** Pack one interleaved RGB×15 coefficient record into PlayCanvas SH3 words. */
export const packGsTileNativeShRecord = (
  scratch: GsTileNativeShScratch,
  streams: GsTileNativeShStreams,
  splat: number,
) => {
  const { coefficients, maximumFloat, maximumBits } = scratch;
  let maximum = coefficients[0];
  for (let index = 1; index < 45; index += 1) {
    maximum = Math.max(maximum, Math.abs(coefficients[index]));
  }
  if (maximum === 0) return;
  const offset = splat * 4;
  maximumFloat[0] = maximum;
  streams[0][offset] = maximumBits[0];
  const t11 = (1 << 11) - 1;
  const t10 = (1 << 10) - 1;
  for (let coefficient = 0; coefficient < 15; coefficient += 1) {
    const index = coefficient * 3;
    let red =
      (coefficients[index] / maximum * 0.5 + 0.5) * t11 + 0.5;
    if (index === 0) red = Math.max(0, Math.min(t11, red));
    const green =
      (coefficients[index + 1] / maximum * 0.5 + 0.5) * t10 + 0.5;
    const blue =
      (coefficients[index + 2] / maximum * 0.5 + 0.5) * t11 + 0.5;
    const packed = coefficient + 1;
    streams[packed >> 2][offset + (packed & 3)] =
      (red << 21) | (green << 11) | blue;
  }
};

/** Pack PlayCanvas SH3 textures directly from its 45 column-major properties. */
export const packGsTileNativeSh = (
  colorSh: readonly Float32Array[],
  streams: GsTileNativeShStreams,
) => {
  const count = colorSh[0]?.length ?? 0;
  if (
    colorSh.length !== 45 ||
    colorSh.some((column) => column.length !== count) ||
    streams.some((stream) => stream.length < count * 4)
  ) {
    throw new Error("GSTile native SH stream shape is inconsistent");
  }
  const scratch = createGsTileNativeShScratch();
  const { coefficients } = scratch;
  for (let splat = 0; splat < count; splat += 1) {
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const index = coefficient * 3;
      coefficients[index] = colorSh[coefficient][splat];
      coefficients[index + 1] = colorSh[coefficient + 15][splat];
      coefficients[index + 2] = colorSh[coefficient + 30][splat];
    }
    packGsTileNativeShRecord(scratch, streams, splat);
  }
};
