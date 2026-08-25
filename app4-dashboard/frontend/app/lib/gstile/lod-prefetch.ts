import type { GsTileManifest, GsTilePack } from "./contracts";

export const DEFAULT_GSTILE_PREFETCH_BYTES = 384 * 1024 * 1024;

export type GsTilePrefetchProjection = {
  verticalFovRadians: number;
  viewportWidth: number;
  viewportHeight: number;
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
): GsTilePrefetchPack[] => {
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 0) {
    throw new Error("GSTile prefetch budget must be a non-negative integer");
  }
  const resident = new Set(residentNodeIds);
  const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
  const packs = new Map(manifest.packs.map((pack) => [pack.id, pack]));
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
    if (scheduledPacks.has(pack.id)) continue;
    if (plannedBytes + pack.byteLength > maximumBytes) continue;
    scheduledPacks.add(pack.id);
    planned.push({ nodeId, pack });
    plannedBytes += pack.byteLength;
  }
  return planned;
};
