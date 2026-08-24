export type GsTileNativeShStreams = readonly [
  Uint32Array,
  Uint32Array,
  Uint32Array,
  Uint32Array,
];

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
  const maxFloat = new Float32Array(1);
  const maxBits = new Uint32Array(maxFloat.buffer);
  const t11 = (1 << 11) - 1;
  const t10 = (1 << 10) - 1;
  const coefficients = new Array<number>(45).fill(0);
  for (let splat = 0; splat < count; splat += 1) {
    let maximum = colorSh[0][splat];
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const index = coefficient * 3;
      const red = colorSh[coefficient][splat];
      const green = colorSh[coefficient + 15][splat];
      const blue = colorSh[coefficient + 30][splat];
      coefficients[index] = red;
      coefficients[index + 1] = green;
      coefficients[index + 2] = blue;
      if (index > 0) maximum = Math.max(maximum, Math.abs(red));
      maximum = Math.max(maximum, Math.abs(green));
      maximum = Math.max(maximum, Math.abs(blue));
    }
    if (maximum === 0) continue;
    const offset = splat * 4;
    maxFloat[0] = maximum;
    streams[0][offset] = maxBits[0];
    for (let coefficient = 0; coefficient < 15; coefficient += 1) {
      const index = coefficient * 3;
      // `maximum` is the largest absolute coefficient. For finite decoded
      // GSTile values, quantization is therefore already in [0, limit]; the
      // two Math.min/Math.max calls per channel in PlayCanvas are redundant.
      let red =
        (coefficients[index] / maximum * 0.5 + 0.5) * t11 + 0.5;
      // PlayCanvas initializes the maximum from coefficient 0 without abs().
      // Preserve its clamp for that single exceptional channel.
      if (index === 0) red = Math.max(0, Math.min(t11, red));
      const green =
        (coefficients[index + 1] / maximum * 0.5 + 0.5) * t10 + 0.5;
      const blue =
        (coefficients[index + 2] / maximum * 0.5 + 0.5) * t11 + 0.5;
      const packed = coefficient + 1;
      streams[packed >> 2][offset + (packed & 3)] =
        // Bitwise packing applies ToInt32, which is exactly Math.floor for
        // these finite non-negative values in [0, 2047].
        (red << 21) | (green << 11) | blue;
    }
  }
};
