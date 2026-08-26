import type { GsTileManifest, GsTilePack, Vec3 } from "./contracts";

export const DEFAULT_GSTILE_PREFETCH_BYTES = 384 * 1024 * 1024;
export const MINIMUM_GSTILE_PREFETCH_BYTES = 96 * 1024 * 1024;
export const GSTILE_PREFETCH_UTILITY_SAMPLE_BYTES = 128 * 1024 * 1024;
export const GSTILE_PREFETCH_TARGET_UTILITY = 0.5;

export type GsTilePrefetchBudget = {
  maximumBytes: number;
  adaptive: boolean;
  utilityRatio: number | null;
};

/**
 * Scale speculative traffic only after one meaningful cohort has had an
 * opportunity to be consumed. Visible-cut requests never use this budget.
 * MiB quantization keeps plans stable when a few packs are promoted between
 * consecutive camera samples.
 */
export const gstileAdaptivePrefetchBudget = (
  completedBytes: number,
  usefulBytes: number,
): GsTilePrefetchBudget => {
  if (
    !Number.isSafeInteger(completedBytes) ||
    completedBytes < 0 ||
    !Number.isSafeInteger(usefulBytes) ||
    usefulBytes < 0 ||
    usefulBytes > completedBytes
  ) {
    throw new Error("Invalid GSTile prefetch utility sample");
  }
  const utilityRatio = completedBytes > 0 ? usefulBytes / completedBytes : null;
  if (completedBytes < GSTILE_PREFETCH_UTILITY_SAMPLE_BYTES) {
    return {
      maximumBytes: DEFAULT_GSTILE_PREFETCH_BYTES,
      adaptive: false,
      utilityRatio,
    };
  }
  const scaledBytes =
    DEFAULT_GSTILE_PREFETCH_BYTES *
    ((utilityRatio ?? 0) / GSTILE_PREFETCH_TARGET_UTILITY);
  const boundedBytes = Math.max(
    MINIMUM_GSTILE_PREFETCH_BYTES,
    Math.min(DEFAULT_GSTILE_PREFETCH_BYTES, scaledBytes),
  );
  const mebibyte = 1024 * 1024;
  return {
    maximumBytes: Math.round(boundedBytes / mebibyte) * mebibyte,
    adaptive: true,
    utilityRatio,
  };
};

export type GsTilePrefetchProjection = {
  verticalFovRadians: number;
  viewportWidth: number;
  viewportHeight: number;
};

export type GsTileCameraPose = {
  position: Vec3;
  direction: Vec3;
  up: Vec3;
};

export type GsTileCameraMotion = {
  pose: GsTileCameraPose;
  timestampMs: number;
  positionVelocity: Vec3;
  directionVelocity: Vec3;
  upVelocity: Vec3;
  samples: number;
};

const vectorLength = (value: Vec3) =>
  Math.hypot(value[0], value[1], value[2]);

const normalized = (value: Vec3, fallback: Vec3): Vec3 => {
  const length = vectorLength(value);
  if (!Number.isFinite(length) || length < 1e-9) return [...fallback];
  return [value[0] / length, value[1] / length, value[2] / length];
};

const scaledToMaximumLength = (value: Vec3, maximumLength: number): Vec3 => {
  const length = vectorLength(value);
  if (length <= maximumLength || length < 1e-9) return value;
  const scale = maximumLength / length;
  return [value[0] * scale, value[1] * scale, value[2] * scale];
};

const clampDirectionDelta = (
  current: Vec3,
  predicted: Vec3,
  maximumAngleRadians: number,
): Vec3 => {
  const from = normalized(current, [0, 0, -1]);
  const to = normalized(predicted, from);
  const dot = Math.max(
    -1,
    Math.min(1, from[0] * to[0] + from[1] * to[1] + from[2] * to[2]),
  );
  const angle = Math.acos(dot);
  if (angle <= maximumAngleRadians || angle < 1e-9) return to;
  const ratio = maximumAngleRadians / angle;
  return normalized(
    [
      from[0] + (to[0] - from[0]) * ratio,
      from[1] + (to[1] - from[1]) * ratio,
      from[2] + (to[2] - from[2]) * ratio,
    ],
    from,
  );
};

/**
 * Estimate camera velocity from render-time poses. Long gaps deliberately
 * reset the estimate so tab suspension and programmatic camera jumps cannot
 * trigger a large speculative transfer burst.
 */
export const updateGsTileCameraMotion = (
  previous: GsTileCameraMotion | null,
  pose: GsTileCameraPose,
  timestampMs: number,
  smoothing = 0.6,
  maximumSampleIntervalMs = 2_000,
): GsTileCameraMotion => {
  if (
    !Number.isFinite(timestampMs) ||
    !Number.isFinite(smoothing) ||
    smoothing < 0 ||
    smoothing > 1 ||
    !Number.isFinite(maximumSampleIntervalMs) ||
    maximumSampleIntervalMs <= 0
  ) {
    throw new Error("Invalid GSTile camera motion sample");
  }
  const snapshot: GsTileCameraPose = {
    position: [...pose.position],
    direction: normalized(pose.direction, [0, 0, -1]),
    up: normalized(pose.up, [0, 1, 0]),
  };
  const elapsedMs = previous ? timestampMs - previous.timestampMs : 0;
  if (!previous || elapsedMs <= 0 || elapsedMs > maximumSampleIntervalMs) {
    return {
      pose: snapshot,
      timestampMs,
      positionVelocity: [0, 0, 0],
      directionVelocity: [0, 0, 0],
      upVelocity: [0, 0, 0],
      samples: 1,
    };
  }
  const velocity = (current: Vec3, prior: Vec3, estimate: Vec3): Vec3 => [
    smoothing * ((current[0] - prior[0]) / elapsedMs) +
      (1 - smoothing) * estimate[0],
    smoothing * ((current[1] - prior[1]) / elapsedMs) +
      (1 - smoothing) * estimate[1],
    smoothing * ((current[2] - prior[2]) / elapsedMs) +
      (1 - smoothing) * estimate[2],
  ];
  return {
    pose: snapshot,
    timestampMs,
    positionVelocity: velocity(
      snapshot.position,
      previous.pose.position,
      previous.positionVelocity,
    ),
    directionVelocity: velocity(
      snapshot.direction,
      previous.pose.direction,
      previous.directionVelocity,
    ),
    upVelocity: velocity(snapshot.up, previous.pose.up, previous.upVelocity),
    samples: previous.samples + 1,
  };
};

/**
 * Extrapolate one short camera horizon, with strict translation and angular
 * caps. Returning null for negligible motion keeps stationary halo prefetch
 * behavior unchanged.
 */
export const predictGsTileCameraPose = (
  motion: GsTileCameraMotion | null,
  horizonMs: number,
  maximumPositionDelta: number,
  maximumAngleRadians: number,
): GsTileCameraPose | null => {
  if (
    !Number.isFinite(horizonMs) ||
    horizonMs < 0 ||
    !Number.isFinite(maximumPositionDelta) ||
    maximumPositionDelta < 0 ||
    !Number.isFinite(maximumAngleRadians) ||
    maximumAngleRadians < 0 ||
    maximumAngleRadians >= Math.PI
  ) {
    throw new Error("Invalid GSTile camera prediction bounds");
  }
  if (!motion || motion.samples < 2 || horizonMs === 0) return null;
  const positionDelta = scaledToMaximumLength(
    motion.positionVelocity.map((value) => value * horizonMs) as Vec3,
    maximumPositionDelta,
  );
  const rawDirection: Vec3 = motion.directionVelocity.map(
    (value, index) => motion.pose.direction[index] + value * horizonMs,
  ) as Vec3;
  const rawUp: Vec3 = motion.upVelocity.map(
    (value, index) => motion.pose.up[index] + value * horizonMs,
  ) as Vec3;
  const direction = clampDirectionDelta(
    motion.pose.direction,
    rawDirection,
    maximumAngleRadians,
  );
  const up = clampDirectionDelta(
    motion.pose.up,
    rawUp,
    maximumAngleRadians,
  );
  const directionDot = Math.max(
    -1,
    Math.min(
      1,
      direction[0] * motion.pose.direction[0] +
        direction[1] * motion.pose.direction[1] +
        direction[2] * motion.pose.direction[2],
    ),
  );
  const upDot = Math.max(
    -1,
    Math.min(
      1,
      up[0] * motion.pose.up[0] +
        up[1] * motion.pose.up[1] +
        up[2] * motion.pose.up[2],
    ),
  );
  const angularDelta = Math.max(Math.acos(directionDot), Math.acos(upDot));
  if (
    vectorLength(positionDelta) < Math.max(maximumPositionDelta * 0.01, 1e-6) &&
    angularDelta < (0.25 * Math.PI) / 180
  ) {
    return null;
  }
  return {
    position: [
      motion.pose.position[0] + positionDelta[0],
      motion.pose.position[1] + positionDelta[1],
      motion.pose.position[2] + positionDelta[2],
    ],
    direction,
    up,
  };
};

/**
 * Expand the planning frustum without lowering its angular pixel density.
 * Scaling the virtual viewport by the tangent ratio keeps focalPixels exactly
 * equal to the rendered view, so prefetched nodes have the LOD required after
 * a camera pan instead of a coarser wide-angle representation.
 */
export const gstilePrefetchProjection = (
  verticalFovRadians: number,
  viewportWidth: number,
  viewportHeight: number,
  verticalFovMultiplier: number,
  maximumVerticalFovRadians: number,
): GsTilePrefetchProjection => {
  if (
    !Number.isFinite(verticalFovRadians) ||
    verticalFovRadians <= 0 ||
    verticalFovRadians >= Math.PI ||
    !Number.isFinite(viewportWidth) ||
    viewportWidth <= 0 ||
    !Number.isFinite(viewportHeight) ||
    viewportHeight <= 0 ||
    !Number.isFinite(verticalFovMultiplier) ||
    verticalFovMultiplier < 1 ||
    !Number.isFinite(maximumVerticalFovRadians) ||
    maximumVerticalFovRadians <= 0 ||
    maximumVerticalFovRadians >= Math.PI
  ) {
    throw new Error("Invalid GSTile prefetch projection");
  }
  const expandedVerticalFovRadians = Math.max(
    verticalFovRadians,
    Math.min(
      verticalFovRadians * verticalFovMultiplier,
      maximumVerticalFovRadians,
    ),
  );
  const viewportScale =
    Math.tan(expandedVerticalFovRadians / 2) /
    Math.tan(verticalFovRadians / 2);
  return {
    verticalFovRadians: expandedVerticalFovRadians,
    viewportWidth: viewportWidth * viewportScale,
    viewportHeight: viewportHeight * viewportScale,
  };
};

export type GsTilePrefetchPack = {
  nodeId: string;
  pack: GsTilePack;
};

/**
 * Keep the expanded-cut screen priority while enforcing a strict transfer
 * budget. Pack identity, rather than node identity, prevents duplicate reads
 * when a future manifest stores several representations in one object.
 */
export const planGsTilePrefetchPacks = (
  manifest: GsTileManifest,
  residentNodeIds: Iterable<string>,
  expandedNodeIds: Iterable<string>,
  maximumBytes = DEFAULT_GSTILE_PREFETCH_BYTES,
  locallyAvailablePackIds: Iterable<string> = [],
): GsTilePrefetchPack[] => {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 0) {
    throw new Error("GSTile prefetch budget must be a non-negative integer");
  }
  const resident = new Set(residentNodeIds);
  const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
  const packs = new Map(manifest.packs.map((pack) => [pack.id, pack]));
  const locallyAvailablePacks = new Set(locallyAvailablePackIds);
  const scheduledPacks = new Set<string>();
  const planned: GsTilePrefetchPack[] = [];
  let plannedBytes = 0;

  for (const nodeId of expandedNodeIds) {
    if (resident.has(nodeId)) continue;
    const node = nodes.get(nodeId);
    const tile = node?.tile ?? node?.lodTile;
    const pack = tile ? packs.get(tile.pack) : undefined;
    if (!node || !tile || !pack) {
      throw new Error(`GSTile prefetch node ${nodeId} is incomplete`);
    }
    if (locallyAvailablePacks.has(pack.id)) continue;
    if (scheduledPacks.has(pack.id)) continue;
    if (plannedBytes + pack.byteLength > maximumBytes) continue;
    scheduledPacks.add(pack.id);
    planned.push({ nodeId, pack });
    plannedBytes += pack.byteLength;
  }
  return planned;
};
