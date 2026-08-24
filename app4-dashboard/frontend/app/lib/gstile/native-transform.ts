export type GsTileNativeTransformColumns = {
  position: readonly Float32Array[];
  logScale: readonly Float32Array[];
  rotation: readonly Float32Array[];
};

/** Pack the native PlayCanvas transform streams without per-splat math objects. */
export const packGsTileNativeTransforms = (
  columns: GsTileNativeTransformColumns,
  transformA: Uint32Array,
  transformB: Uint16Array,
  float2Half: (value: number) => number,
) => {
  const count = columns.position[0]?.length ?? 0;
  if (
    columns.position.length !== 3 ||
    columns.logScale.length !== 3 ||
    columns.rotation.length !== 4 ||
    [...columns.position, ...columns.logScale, ...columns.rotation].some(
      (column) => column.length !== count,
    ) ||
    transformA.length < count * 4 ||
    transformB.length < count * 4
  ) {
    throw new Error("GSTile native transform stream shape is inconsistent");
  }
  const transformAFloat32 = new Float32Array(
    transformA.buffer,
    transformA.byteOffset,
    transformA.length,
  );
  const [px, py, pz] = columns.position;
  const [sx, sy, sz] = columns.logScale;
  const [rw, rx, ry, rz] = columns.rotation;
  for (let splat = 0; splat < count; splat += 1) {
    const offset = splat * 4;
    let x = rx[splat];
    let y = ry[splat];
    let z = rz[splat];
    let w = rw[splat];
    const length = Math.sqrt(x * x + y * y + z * z + w * w);
    if (length === 0) {
      x = 0;
      y = 0;
      z = 0;
      w = 1;
    } else {
      const inverseLength = 1 / length;
      x *= inverseLength;
      y *= inverseLength;
      z *= inverseLength;
      w *= inverseLength;
    }
    if (w < 0) {
      x = -x;
      y = -y;
      z = -z;
    }
    transformAFloat32[offset] = px[splat];
    transformAFloat32[offset + 1] = py[splat];
    transformAFloat32[offset + 2] = pz[splat];
    transformA[offset + 3] = float2Half(x) | (float2Half(y) << 16);
    transformB[offset] = float2Half(Math.exp(sx[splat]));
    transformB[offset + 1] = float2Half(Math.exp(sy[splat]));
    transformB[offset + 2] = float2Half(Math.exp(sz[splat]));
    transformB[offset + 3] = float2Half(z);
  }
};
