export type GsTileNativeTransformColumns = {
  position: readonly Float32Array[];
  centerStream?: Float32Array | null;
  logScale: readonly Float32Array[];
  rotation: readonly Float32Array[];
};

/** Pack the native PlayCanvas transform streams without per-splat math objects. */
export const packGsTileNativeTransforms = (
  columns: GsTileNativeTransformColumns,
  transformA: Uint32Array,
  transformB: Uint16Array,
  float2Half: (value: number) => number,
  float16ArrayConstructor:
    | typeof Float16Array
    | null
    | undefined = globalThis.Float16Array,
  activeCount?: number,
) => {
  const capacity = columns.logScale[0]?.length ?? 0;
  const count = activeCount ?? capacity;
  if (
    !Number.isSafeInteger(count) ||
    count < 0 ||
    count > capacity ||
    columns.position.length !== 3 ||
    columns.logScale.length !== 3 ||
    columns.rotation.length !== 4 ||
    [...columns.logScale, ...columns.rotation].some(
      (column) => column.length !== capacity,
    ) ||
    (columns.centerStream
      ? columns.centerStream.length !== capacity * 3 ||
        columns.position.some((column) => column.length !== 0)
      : columns.position.some((column) => column.length !== capacity)) ||
    transformA.length < capacity * 4 ||
    transformB.length < capacity * 4
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
  const centers = columns.centerStream;
  if (typeof float16ArrayConstructor !== "function") {
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
      const centerOffset = splat * 3;
      transformAFloat32[offset] = centers ? centers[centerOffset] : px[splat];
      transformAFloat32[offset + 1] = centers
        ? centers[centerOffset + 1]
        : py[splat];
      transformAFloat32[offset + 2] = centers
        ? centers[centerOffset + 2]
        : pz[splat];
      transformA[offset + 3] = float2Half(x) | (float2Half(y) << 16);
      transformB[offset] = float2Half(Math.exp(sx[splat]));
      transformB[offset + 1] = float2Half(Math.exp(sy[splat]));
      transformB[offset + 2] = float2Half(Math.exp(sz[splat]));
      transformB[offset + 3] = float2Half(z);
    }
    return;
  }
  // Native stores avoid seven JavaScript bit-packing calls per splat. They use
  // IEEE half rounding directly; the fallback retains PlayCanvas compatibility
  // on runtimes predating Float16Array.
  const transformAHalf = new float16ArrayConstructor(
    transformA.buffer,
    transformA.byteOffset,
    transformA.length * 2,
  );
  const transformBHalf = new float16ArrayConstructor(
    transformB.buffer,
    transformB.byteOffset,
    transformB.length,
  );
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
    const centerOffset = splat * 3;
    transformAFloat32[offset] = centers ? centers[centerOffset] : px[splat];
    transformAFloat32[offset + 1] = centers
      ? centers[centerOffset + 1]
      : py[splat];
    transformAFloat32[offset + 2] = centers
      ? centers[centerOffset + 2]
      : pz[splat];
    const halfOffset = offset * 2;
    transformAHalf[halfOffset + 6] = x;
    transformAHalf[halfOffset + 7] = y;
    transformBHalf[offset] = Math.exp(sx[splat]);
    transformBHalf[offset + 1] = Math.exp(sy[splat]);
    transformBHalf[offset + 2] = Math.exp(sz[splat]);
    transformBHalf[offset + 3] = z;
  }
};
