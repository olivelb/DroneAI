import type { GsTileManifest, GsTileNode, Vec3 } from "./contracts";

export type GsTileLodSelectionOptions = {
  cameraPosition: Vec3;
  cameraDirection: Vec3;
  cameraUp: Vec3;
  verticalFovRadians: number;
  viewportWidth: number;
  viewportHeight: number;
  maximumResidentGaussians: number;
  maximumProjectedErrorPixels: number;
};

export type GsTileLodSelection = {
  selectedNodeIds: string[];
  residentGaussians: number;
  maximumSelectedErrorPixels: number;
  budgetLimited: boolean;
  unresolvedMaximumErrorPixels: number;
};

type ProjectionContext = {
  cameraPosition: Vec3;
  forward: Vec3;
  right: Vec3;
  up: Vec3;
  tangentX: number;
  tangentY: number;
  focalPixels: number;
};

// A proxy that covers a large screen region must not survive solely because
// its center/covariance error estimate is optimistic. Express its projected
// footprint as an additional Cesium-style SSE term: at the nominal 2 px
// threshold a proxy may cover at most about 128 px in radius.
const PROXY_SCREEN_RADIUS_ERROR_DIVISOR = 64;

const representationCount = (node: GsTileNode) =>
  node.tile?.recordCount ?? node.lodTile?.recordCount ?? 0;

const traversalBounds = (node: GsTileNode) => node.renderBounds ?? node.bounds;

/**
 * A moment-matched proxy can keep almost identical centers while acquiring a
 * much wider covariance. Center displacement alone would then report nearly
 * zero geometric error and leave that visibly blurred proxy resident even at
 * close range. Treat its largest one-sigma radius as an additional geometric
 * error so the ordinary SSE traversal eventually replaces it with descendants.
 */
export const lodProxySupportError = (node: GsTileNode) => {
  const maximumLogScale = node.lodTile?.quantization?.logScale?.max;
  if (!Array.isArray(maximumLogScale) || maximumLogScale.length !== 3) return 0;
  const finite = maximumLogScale.filter((value) => Number.isFinite(value));
  if (finite.length !== 3) return 0;
  return Math.exp(Math.min(Math.max(...finite), 30));
};

const distanceToBounds = (
  position: Vec3,
  bounds: GsTileNode["bounds"],
) => {
  let squared = 0;
  for (let axis = 0; axis < 3; axis += 1) {
    const distance = Math.max(
      bounds.min[axis] - position[axis],
      0,
      position[axis] - bounds.max[axis],
    );
    squared += distance * distance;
  }
  return Math.max(Math.sqrt(squared), 1e-6);
};

const dot = (left: Vec3, right: Vec3) =>
  left[0] * right[0] + left[1] * right[1] + left[2] * right[2];

const cross = (left: Vec3, right: Vec3): Vec3 => [
  left[1] * right[2] - left[2] * right[1],
  left[2] * right[0] - left[0] * right[2],
  left[0] * right[1] - left[1] * right[0],
];

const normalize = (value: Vec3): Vec3 => {
  const length = Math.hypot(value[0], value[1], value[2]);
  if (!Number.isFinite(length) || length <= 1e-9) {
    throw new Error("Invalid GSTile camera basis");
  }
  return [value[0] / length, value[1] / length, value[2] / length];
};

const projectionContext = (
  options: GsTileLodSelectionOptions,
): ProjectionContext => {
  const forward = normalize(options.cameraDirection);
  const right = normalize(cross(forward, normalize(options.cameraUp)));
  const up = normalize(cross(right, forward));
  const tangentY = Math.tan(options.verticalFovRadians / 2);
  return {
    cameraPosition: options.cameraPosition,
    forward,
    right,
    up,
    tangentX: tangentY * (options.viewportWidth / options.viewportHeight),
    tangentY,
    focalPixels: options.viewportHeight / (2 * tangentY),
  };
};

const projectNode = (node: GsTileNode, context: ProjectionContext) => {
  const cullingBounds = traversalBounds(node);
  const center: Vec3 = [
    (cullingBounds.min[0] + cullingBounds.max[0]) / 2,
    (cullingBounds.min[1] + cullingBounds.max[1]) / 2,
    (cullingBounds.min[2] + cullingBounds.max[2]) / 2,
  ];
  const halfExtent: Vec3 = [
    (cullingBounds.max[0] - cullingBounds.min[0]) / 2,
    (cullingBounds.max[1] - cullingBounds.min[1]) / 2,
    (cullingBounds.max[2] - cullingBounds.min[2]) / 2,
  ];
  const radius = Math.hypot(halfExtent[0], halfExtent[1], halfExtent[2]);
  const relative: Vec3 = [
    center[0] - context.cameraPosition[0],
    center[1] - context.cameraPosition[1],
    center[2] - context.cameraPosition[2],
  ];
  const depth = dot(relative, context.forward);
  const horizontal = Math.abs(dot(relative, context.right));
  const vertical = Math.abs(dot(relative, context.up));
  const visible =
    depth + radius > 1e-6 &&
    depth * context.tangentX -
      horizontal +
      radius * Math.hypot(1, context.tangentX) >=
      0 &&
    depth * context.tangentY -
      vertical +
      radius * Math.hypot(1, context.tangentY) >=
      0;
  const screenRadiusPixels = visible
    ? (radius * context.focalPixels) / Math.max(depth - radius, 1e-6)
    : 0;
  const geometricError = Math.max(
    node.geometricError ?? 0,
    lodProxySupportError(node),
  );
  const geometricErrorPixels =
    visible && geometricError > 0 && node.children
      ? (geometricError * context.focalPixels) /
        Math.max(
          distanceToBounds(context.cameraPosition, cullingBounds),
          geometricError,
        )
      : 0;
  const proxyFootprintErrorPixels =
    visible && node.lodTile && node.children
      ? Math.min(screenRadiusPixels, context.focalPixels) /
        PROXY_SCREEN_RADIUS_ERROR_DIVISOR
      : 0;
  return {
    visible,
    screenRadiusPixels,
    errorPixels: Math.max(
      geometricErrorPixels,
      proxyFootprintErrorPixels,
    ),
  };
};

type LodCut = {
  nodes: GsTileNode[];
  residentGaussians: number;
  maximumSelectedErrorPixels: number;
};

export const selectGsTileLod = (
  manifest: GsTileManifest,
  options: GsTileLodSelectionOptions,
): GsTileLodSelection => {
  if (
    !Number.isFinite(options.verticalFovRadians) ||
    options.verticalFovRadians <= 0 ||
    options.verticalFovRadians >= Math.PI ||
    options.cameraPosition.some((value) => !Number.isFinite(value)) ||
    options.cameraDirection.some((value) => !Number.isFinite(value)) ||
    options.cameraUp.some((value) => !Number.isFinite(value)) ||
    !Number.isFinite(options.viewportWidth) ||
    options.viewportWidth <= 0 ||
    !Number.isFinite(options.viewportHeight) ||
    options.viewportHeight <= 0 ||
    !Number.isSafeInteger(options.maximumResidentGaussians) ||
    options.maximumResidentGaussians < 1 ||
    !Number.isFinite(options.maximumProjectedErrorPixels) ||
    options.maximumProjectedErrorPixels <= 0
  ) {
    throw new Error("Invalid GSTile LOD selection options");
  }
  const context = projectionContext(options);

  const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
  const root = nodes.get(manifest.root);
  if (!root || representationCount(root) < 1) {
    throw new Error("GSTile LOD root has no renderable representation");
  }
  const projections = new Map<string, ReturnType<typeof projectNode>>();
  const projection = (node: GsTileNode) => {
    const cached = projections.get(node.id);
    if (cached) return cached;
    const projected = projectNode(node, context);
    projections.set(node.id, projected);
    return projected;
  };
  if (!projection(root).visible) {
    return {
      selectedNodeIds: [],
      residentGaussians: 0,
      maximumSelectedErrorPixels: 0,
      budgetLimited: false,
      unresolvedMaximumErrorPixels: 0,
    };
  }
  if (representationCount(root) > options.maximumResidentGaussians) {
    throw new Error("GSTile root proxy exceeds the resident splat budget");
  }

  const childrenOf = (node: GsTileNode) => {
    if (!node.children) return [];
    const children = node.children.map((id) => nodes.get(id));
    if (children.some((child) => !child || representationCount(child) < 1)) {
      throw new Error(`GSTile node ${node.id} has invalid LOD children`);
    }
    return (children as GsTileNode[]).filter(
      (child) => projection(child).visible,
    );
  };

  // Cesium-style REPLACE traversal: every visible branch is evaluated against
  // one SSE threshold. A parent whose conservative traversal volume intersects
  // the frustum but whose child union is entirely outside contributes nothing.
  // Keeping that parent would render a coarse proxy over unrelated pixels.
  const buildCut = (maximumErrorPixels: number): LodCut => {
    const selected: GsTileNode[] = [];
    let residentGaussians = 0;
    let maximumSelectedErrorPixels = 0;

    const visit = (node: GsTileNode) => {
      const projected = projection(node);
      if (!projected.visible) return;
      const visibleChildren = childrenOf(node);
      if (node.children && visibleChildren.length === 0) return;
      if (
        visibleChildren.length > 0 &&
        projected.errorPixels > maximumErrorPixels
      ) {
        for (const child of visibleChildren) visit(child);
        return;
      }
      selected.push(node);
      residentGaussians += representationCount(node);
      maximumSelectedErrorPixels = Math.max(
        maximumSelectedErrorPixels,
        projected.errorPixels,
      );
    };

    visit(root);
    return { nodes: selected, residentGaussians, maximumSelectedErrorPixels };
  };

  const requestedCut = buildCut(options.maximumProjectedErrorPixels);
  let selectedCut = requestedCut;
  let budgetLimited = false;
  if (requestedCut.residentGaussians > options.maximumResidentGaussians) {
    budgetLimited = true;

    // Cesium raises one memory-adjusted SSE for the whole tileset. Choosing a
    // single threshold avoids the previous greedy failure mode where an
    // expensive branch stayed blurry while cheaper neighbours became exact.
    const candidateErrors = [...new Set(
      manifest.nodes
        .filter((node) => node.children && projection(node).visible)
        .map((node) => projection(node).errorPixels)
        .filter((error) => error > options.maximumProjectedErrorPixels),
    )].sort((left, right) => left - right);
    let lower = 0;
    let upper = candidateErrors.length - 1;
    let fittingCut: LodCut | null = null;
    while (lower <= upper) {
      const middle = Math.floor((lower + upper) / 2);
      const cut = buildCut(candidateErrors[middle]);
      if (cut.residentGaussians <= options.maximumResidentGaussians) {
        fittingCut = cut;
        upper = middle - 1;
      } else {
        lower = middle + 1;
      }
    }
    if (!fittingCut) {
      throw new Error("GSTile hierarchy cannot satisfy the resident splat budget");
    }
    selectedCut = fittingCut;
  }

  const selectedNodes = selectedCut.nodes
    .sort((left, right) => {
      const priority =
        projection(right).screenRadiusPixels -
        projection(left).screenRadiusPixels;
      return priority || left.id.localeCompare(right.id);
    });
  return {
    selectedNodeIds: selectedNodes.map((node) => node.id),
    residentGaussians: selectedCut.residentGaussians,
    maximumSelectedErrorPixels: selectedCut.maximumSelectedErrorPixels,
    budgetLimited,
    unresolvedMaximumErrorPixels: budgetLimited
      ? selectedCut.maximumSelectedErrorPixels
      : 0,
  };
};
