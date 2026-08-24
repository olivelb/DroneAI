const floatView = new Float32Array(1);
const int32View = new Int32Array(floatView.buffer);

const SH_C0 = 0.28209479177387814;

/** Match PlayCanvas' float32-to-float16 conversion exactly. */
const float2Half = (value: number) => {
  floatView[0] = value;
  const bits32 = int32View[0];
  let bits16 = (bits32 >> 16) & 0x8000;
  let mantissa = (bits32 >> 12) & 0x07ff;
  const exponent = (bits32 >> 23) & 0xff;
  if (exponent < 103) return bits16;
  if (exponent > 142) {
    bits16 |= 0x7c00;
    bits16 |= (exponent === 0xff ? 0 : 1) && (bits32 & 0x7f_ffff);
    return bits16;
  }
  if (exponent < 113) {
    mantissa |= 0x0800;
    bits16 |=
      (mantissa >> (114 - exponent)) +
      ((mantissa >> (113 - exponent)) & 1);
    return bits16;
  }
  bits16 |= ((exponent - 112) << 10) | (mantissa >> 1);
  bits16 += mantissa & 1;
  return bits16;
};

/** Pack one decoded base-color record into PlayCanvas' RGBA16F texture layout. */
export const packGsTileNativeColorRecord = (
  stream: Uint16Array,
  splat: number,
  redDc: number,
  greenDc: number,
  blueDc: number,
  opacityLogit: number,
) => {
  const offset = splat * 4;
  stream[offset] = float2Half(redDc * SH_C0 + 0.5);
  stream[offset + 1] = float2Half(greenDc * SH_C0 + 0.5);
  stream[offset + 2] = float2Half(blueDc * SH_C0 + 0.5);
  stream[offset + 3] = float2Half(1 / (1 + Math.exp(-opacityLogit)));
};
