const SH_C0 = 0.28209479177387814;
const SH_C1 = 0.4886025119029199;
const SH_C2 = [
  1.0925484305920792,
  -1.0925484305920792,
  0.31539156525252005,
  -1.0925484305920792,
  0.5462742152960396,
] as const;
const SH_C3 = [
  -0.5900435899266435,
  2.890611442640554,
  -0.4570457994644658,
  0.3731763325901154,
  -0.4570457994644658,
  1.445305721320277,
  -0.5900435899266435,
] as const;

/** CPU reference for the exact degree-0..3 convention used by DroneGS. */
export const droneGsShBasis = (
  direction: readonly [number, number, number],
  degree: number,
): Float32Array => {
  if (!Number.isInteger(degree) || degree < 0 || degree > 3) {
    throw new Error("DroneGS SH degree must be between zero and three");
  }
  let [x, y, z] = direction;
  const squaredNorm = x * x + y * y + z * z;
  if (!Number.isFinite(squaredNorm) || squaredNorm <= 1e-20) {
    throw new Error("DroneGS SH direction must be finite and non-zero");
  }
  const inverseNorm = 1 / Math.sqrt(squaredNorm);
  x *= inverseNorm;
  y *= inverseNorm;
  z *= inverseNorm;
  const basis = new Float32Array(16);
  basis[0] = SH_C0;
  if (degree === 0) return basis;
  basis[1] = -SH_C1 * y;
  basis[2] = SH_C1 * z;
  basis[3] = -SH_C1 * x;
  if (degree === 1) return basis;
  const xx = x * x;
  const yy = y * y;
  const zz = z * z;
  basis[4] = SH_C2[0] * x * y;
  basis[5] = SH_C2[1] * y * z;
  basis[6] = SH_C2[2] * (2 * zz - xx - yy);
  basis[7] = SH_C2[3] * x * z;
  basis[8] = SH_C2[4] * (xx - yy);
  if (degree === 2) return basis;
  basis[9] = SH_C3[0] * y * (3 * xx - yy);
  basis[10] = SH_C3[1] * x * y * z;
  basis[11] = SH_C3[2] * y * (4 * zz - xx - yy);
  basis[12] = SH_C3[3] * z * (2 * zz - 3 * xx - 3 * yy);
  basis[13] = SH_C3[4] * x * (4 * zz - xx - yy);
  basis[14] = SH_C3[5] * z * (xx - yy);
  basis[15] = SH_C3[6] * x * (xx - 3 * yy);
  return basis;
};

export const droneGsDirectionalOpacity = (
  opacityLogit: number,
  opacitySh: ArrayLike<number>,
  direction: readonly [number, number, number],
  degree: number,
) => {
  const coefficientCount = (degree + 1) ** 2 - 1;
  if (opacitySh.length < coefficientCount) {
    throw new Error("DroneGS opacity SH vector is too short");
  }
  const basis = droneGsShBasis(direction, degree);
  let logit = opacityLogit;
  for (let index = 0; index < coefficientCount; index += 1) {
    logit += opacitySh[index] * basis[index + 1];
  }
  return 1 / (1 + Math.exp(-logit));
};
