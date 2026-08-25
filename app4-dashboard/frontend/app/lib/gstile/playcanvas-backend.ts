import type {
  GaussianCameraState,
  GaussianViewFrame,
  GaussianRenderBackend,
  GaussianRenderStatistics,
} from "./backend";
import { GaussianBackendUnavailable } from "./backend";
import {
  type GsTileManifest,
  type GsTileNode,
  type Vec3,
  GSTILE_ADAPTIVE_LOD_PROFILE,
  GSTILE_MOMENT_LOD_PROFILE,
  isGsTileLodProfile,
} from "./contracts";
import { resolveGsTilePackUrl } from "./contracts";
import {
  allocateGsTilePlayCanvasColumns,
  decodeGsTilePackTile,
  decodeSha256VerifiedGsTilePackTile,
  decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns,
  gsTileOpacityStreams,
  gsTileToPlyProperties,
  type DecodedGsTile,
  type GsTilePlayCanvasColumns,
} from "./decode";
import {
  DRONEGS_OPACITY_MODIFIER_GLSL,
  DRONEGS_OPACITY_MODIFIER_WGSL,
} from "./playcanvas-opacity";
import type { GsTileRangeScheduler } from "./range-source";
import { selectGsTileLod } from "./lod-selection";
import {
  calculateMergedArenaBounds,
  mergedArenaActiveSpans,
  mergeMergedArenaBounds,
  planLinearTextureCopies,
  planMergedArenaSlots,
  type MergedArenaBounds,
  type MergedArenaSlot,
} from "./merged-arena";
import { packGsTileNativeTransforms } from "./native-transform";
import { packGsTileNativeSh } from "./native-sh";
import { adoptGsTileNativeRgbaStreams } from "./native-streams";
import {
  copyGsTileNativeResult,
  decodeGsTileNativePayload,
} from "./native-decode";
import { GsTileDecodeWorkerPool } from "./decode-worker-pool";

type Pc = typeof import("playcanvas");
type PcApplication = import("playcanvas").Application;
type PcEntity = import("playcanvas").Entity;
type PcResource = import("playcanvas").GSplatResourceBase;
type PcArenaResource = PcResource & {
  readonly maxSplats: number;
  centers: Float32Array;
  update: (numSplats?: number, centersUpdated?: boolean) => void;
};
type LoadedTile = {
  entity: PcEntity;
  resource: PcResource;
  arenaResource?: PcArenaResource;
  gaussianCount: number;
  byteLength: number;
  resourceCreateMs: number;
  resourceColorMs: number;
  resourceTransformMs: number;
  resourceShMs: number;
  streamUploadMs: number;
  sceneAttachMs: number;
};
type LodLoadTimings = {
  fetchServiceMs: number;
  sha256ServiceMs: number;
  decodeCpuMs: number;
  decodeWorkerServiceMs: number;
  decodeWorkerFallbacks: number;
};
export type GsTileFrameTelemetry = {
  frameCpuMs: number | null;
  frameGpuMs: number | null;
  workBufferUploadPercent: number | null;
  gpuPasses: Array<{ name: string; durationMs: number }>;
};

export const captureGsTileGpuPassTelemetry = (
  timings: ReadonlyMap<string, number> | null | undefined,
) =>
  [...(timings ?? [])]
    .filter(
      (entry): entry is [string, number] =>
        entry[0].length > 0 && Number.isFinite(entry[1]) && entry[1] >= 0,
    )
    .map(([name, durationMs]) => ({ name, durationMs }));

/** Keep the last real render sample while the on-demand renderer is idle. */
export const retainGsTileFrameTelemetry = (
  previous: GsTileFrameTelemetry,
  current: GsTileFrameTelemetry,
  rendered: boolean,
) => (rendered ? current : previous);

type PreparedTile = {
  pc: Pc;
  app: PcApplication;
  decoded: DecodedGsTile;
  origin: Vec3;
  node: GsTileNode;
  byteLength: number;
};
type MergedArenaState = {
  loaded: LoadedTile;
  container: PcArenaResource;
  slots: Map<string, MergedArenaSlot>;
  bounds: Map<string, MergedArenaBounds>;
  byteLengths: Map<string, number>;
};
type DestroyableGsplatManager = { destroy: () => void };
type ResettableGsplatLayerData = {
  gsplatManager?: DestroyableGsplatManager | null;
  gsplatManagerShadow?: DestroyableGsplatManager | null;
};
type ResettableGsplatDirector = {
  camerasMap: Map<
    unknown,
    { layersMap: Map<unknown, ResettableGsplatLayerData> }
  >;
};

/**
 * Destroy every unified GSplat manager so the next PlayCanvas frame rebuilds
 * its world from the layer's current placements. This is required after an
 * atomic replacement: PlayCanvas 2.21 can otherwise keep rendering the last
 * sorted world even though the layer already contains the new placement.
 */
export const resetPlayCanvasGsplatManagers = (
  director: ResettableGsplatDirector | null | undefined,
) => {
  const destroyed = new Set<DestroyableGsplatManager>();
  for (const cameraData of director?.camerasMap.values() ?? []) {
    for (const layerData of cameraData.layersMap.values()) {
      for (const key of ["gsplatManager", "gsplatManagerShadow"] as const) {
        const manager = layerData[key];
        if (manager && !destroyed.has(manager)) {
          manager.destroy();
          destroyed.add(manager);
        }
        layerData[key] = null;
      }
    }
  }
  return destroyed.size;
};

/**
 * Give a fully populated GSplatResource the small mutable surface used by the
 * persistent arena. The underlying streams stay byte-for-byte identical: only
 * the active count and centers version can change after promotion.
 */
export const configurePlayCanvasGsplatArenaResource = <
  Resource extends { centersVersion: number },
>(
  resource: Resource,
  data: { numSplats: number },
  maximumSplats: number,
  activeSplats: number,
) => {
  if (
    !Number.isSafeInteger(maximumSplats) ||
    maximumSplats < 1 ||
    !Number.isSafeInteger(activeSplats) ||
    activeSplats < 0 ||
    activeSplats > maximumSplats
  ) {
    throw new Error("GSTile promoted arena counts are invalid");
  }
  const update = (
    numSplats = data.numSplats,
    centersUpdated = true,
  ) => {
    data.numSplats = Math.min(Math.max(numSplats, 0), maximumSplats);
    if (centersUpdated) resource.centersVersion += 1;
  };
  Object.defineProperties(resource, {
    maxSplats: { value: maximumSplats },
    update: { value: update },
  });
  update(activeSplats, false);
  return resource as Resource & {
    readonly maxSplats: number;
    update: typeof update;
  };
};

/** Release uploaded typed-array levels retained by PlayCanvas data textures. */
export const releasePlayCanvasTextureCpuSources = (
  textures: Iterable<{ getSource: (mipLevel?: number) => unknown }>,
) => {
  let releasedBytes = 0;
  for (const texture of textures) {
    const source = texture.getSource(0);
    if (!ArrayBuffer.isView(source)) continue;
    const levels = (
      texture as unknown as { _levels: Array<unknown> | null }
    )._levels;
    if (!levels || levels[0] !== source) continue;
    levels[0] = null;
    releasedBytes += source.byteLength;
  }
  return releasedBytes;
};
export type LodTransitionGroup = {
  addNodeIds: string[];
  removeNodeIds: string[];
};

export const lodTransitionCounts = (
  transition: LodTransitionGroup,
  residentGaussianCount: (nodeId: string) => number | undefined,
  stagedGaussianCount: (nodeId: string) => number | undefined,
) => ({
  add: transition.addNodeIds.reduce(
    (total, nodeId) =>
      total +
      (residentGaussianCount(nodeId) === undefined
        ? (stagedGaussianCount(nodeId) ?? 0)
        : 0),
    0,
  ),
  remove: transition.removeNodeIds.reduce(
    (total, nodeId) => total + (residentGaussianCount(nodeId) ?? 0),
    0,
  ),
});

export const completeLodTargetPlan = (
  currentNodeIds: Iterable<string>,
  targetNodeIds: Iterable<string>,
  residentGaussianCount: (nodeId: string) => number | undefined,
  stagedGaussianCount: (nodeId: string) => number | undefined,
) => {
  const target = new Set(targetNodeIds);
  const addNodeIds: string[] = [];
  let gaussianCount = 0;
  let complete = true;
  for (const nodeId of target) {
    const resident = residentGaussianCount(nodeId);
    const staged = stagedGaussianCount(nodeId);
    if (resident === undefined && staged === undefined) {
      complete = false;
      continue;
    }
    gaussianCount += resident ?? staged ?? 0;
    if (resident === undefined) addNodeIds.push(nodeId);
  }
  return {
    complete,
    gaussianCount,
    addNodeIds,
    removeNodeIds: [...currentNodeIds].filter((nodeId) => !target.has(nodeId)),
  };
};

export const planLodTransitions = (
  manifest: GsTileManifest,
  currentNodeIds: Iterable<string>,
  targetNodeIds: Iterable<string>,
): LodTransitionGroup[] => {
  const current = new Set(currentNodeIds);
  const target = new Set(targetNodeIds);
  const parents = new Map<string, string>();
  for (const node of manifest.nodes) {
    for (const child of node.children ?? []) parents.set(child, node.id);
  }
  const isAncestor = (ancestor: string, descendant: string) => {
    let cursor = parents.get(descendant);
    while (cursor !== undefined) {
      if (cursor === ancestor) return true;
      cursor = parents.get(cursor);
    }
    return false;
  };
  const groups = new Map<string, LodTransitionGroup>();
  for (const nodeId of target) {
    if (current.has(nodeId)) continue;
    let ancestor = parents.get(nodeId);
    while (ancestor !== undefined && !current.has(ancestor)) {
      ancestor = parents.get(ancestor);
    }
    if (ancestor !== undefined) {
      const key = `refine:${ancestor}`;
      const group = groups.get(key) ?? {
        addNodeIds: [],
        removeNodeIds: [ancestor],
      };
      group.addNodeIds.push(nodeId);
      groups.set(key, group);
      continue;
    }
    const descendants = [...current].filter((candidate) =>
      isAncestor(nodeId, candidate),
    );
    if (descendants.length > 0) {
      groups.set(`coarsen:${nodeId}`, {
        addNodeIds: [nodeId],
        removeNodeIds: descendants,
      });
      continue;
    }
    groups.set(`add:${nodeId}`, {
      addNodeIds: [nodeId],
      removeNodeIds: [],
    });
  }
  return [...groups.values()];
};

export const prioritizeLodLoads = (
  transitions: readonly LodTransitionGroup[],
  targetNodeIds: readonly string[],
  residentNodeIds: Iterable<string>,
) => {
  const resident = new Set(residentNodeIds);
  const targetRank = new Map(
    targetNodeIds.map((nodeId, index) => [nodeId, index]),
  );
  const scheduled = new Set<string>();
  const ordered: string[] = [];
  const groups = transitions
    .map((transition) => {
      const missing = transition.addNodeIds
        .filter((nodeId) => !resident.has(nodeId))
        .sort(
          (left, right) =>
            (targetRank.get(left) ?? Number.MAX_SAFE_INTEGER) -
            (targetRank.get(right) ?? Number.MAX_SAFE_INTEGER),
        );
      return {
        missing,
        rank: Math.min(
          ...missing.map(
            (nodeId) => targetRank.get(nodeId) ?? Number.MAX_SAFE_INTEGER,
          ),
        ),
      };
    })
    .filter((group) => group.missing.length > 0)
    .sort((left, right) => left.rank - right.rank);
  for (const group of groups) {
    for (const nodeId of group.missing) {
      if (scheduled.has(nodeId)) continue;
      scheduled.add(nodeId);
      ordered.push(nodeId);
    }
  }
  for (const nodeId of targetNodeIds) {
    if (resident.has(nodeId) || scheduled.has(nodeId)) continue;
    scheduled.add(nodeId);
    ordered.push(nodeId);
  }
  return ordered;
};

export type GsTileGpuAssembly = "tiled" | "merged" | "incremental";
export type GsTileOpacityMode =
  "base" | "directional" | "directional-no-reveal";
export type GsTileSortMode = "gpu" | "cpu";

export type PlayCanvasResidentBackendOptions = {
  /** Hard safety gate. Hierarchical LOD must be used beyond this resident baseline. */
  maximumResidentGaussians?: number;
  initialResidentGaussians?: number;
  maximumProjectedErrorPixels?: number;
  background?: [number, number, number];
  verticalFovDegrees?: number;
  transformPrecision?: "packed" | "float32";
  maximumGaussianScale?: number;
  includeSiblingLeaves?: boolean;
  retainOffscreenCoverage?: boolean;
  opacityMode?: GsTileOpacityMode;
  sortMode?: GsTileSortMode;
  radialSorting?: boolean;
  referencePlyUrl?: string;
  referencePlyParts?: number;
  referencePlyTransformMode?: "native" | "full-stream";
  referencePlyOpacityMode?: "native" | "directional";
  referencePlyConstructionMode?: "loader" | "manual";
  debugTiles?: "off" | "lod" | "id";
  gpuAssembly?: GsTileGpuAssembly;
  lodUpdateDelayMilliseconds?: number;
};

/** Keep the exact monolithic renderer as the safe production default. */
export const gstileGpuAssembly = (
  requested: string | null | undefined,
): GsTileGpuAssembly =>
  requested === "tiled" || requested === "incremental" ? requested : "merged";

export const gstileOpacityMode = (
  requested: string | null | undefined,
): GsTileOpacityMode => {
  if (requested === "base") return "base";
  if (requested === "directional-no-reveal") {
    return "directional-no-reveal";
  }
  return "directional";
};

const gstileOpacityModeUniform = (mode: GsTileOpacityMode) =>
  mode === "base" ? 0 : mode === "directional" ? 1 : 2;

export const gstileSortMode = (
  requested: string | null | undefined,
): GsTileSortMode => (requested === "cpu" ? "cpu" : "gpu");

/** Selection identity is set-based; screen-priority ordering is not content. */
export const gstileLodSelectionKey = (nodeIds: readonly string[]) =>
  [...nodeIds].sort((left, right) => left.localeCompare(right)).join("\0");

export const gstileVerticalFovDegrees = (
  requested: string | number | null | undefined,
) => {
  if (requested === null || requested === undefined || requested === "") {
    return 42;
  }
  const value = typeof requested === "number" ? requested : Number(requested);
  return Number.isFinite(value) ? Math.min(Math.max(value, 20), 80) : 42;
};

const DEFAULT_LOD_UPDATE_DELAY_MILLISECONDS = 120;

export const gstileLodUpdateDelayMilliseconds = (
  requested: number | null | undefined,
) => {
  const value = requested ?? DEFAULT_LOD_UPDATE_DELAY_MILLISECONDS;
  if (!Number.isSafeInteger(value) || value < 0 || value > 5_000) {
    throw new Error(
      "lodUpdateDelayMilliseconds must be an integer from 0 to 5000",
    );
  }
  return value;
};

/** Match PlayCanvas' validated PLY loader unless float32 streams are explicit. */
export const gstileTransformPrecision = (
  requested: string | null | undefined,
): "packed" | "float32" => (requested === "float32" ? "float32" : "packed");

type GsplatQualitySettings = {
  antiAlias: boolean;
  alphaClip: number;
  alphaClipForward: number;
  colorUpdateAngle: number;
  dataFormat: string;
  minContribution: number;
  minPixelSize: number;
  radialSorting: boolean;
  renderer: number;
};

export const configureHighQualityGsplatRendering = (
  settings: GsplatQualitySettings,
  values: { dataFormat: string; renderer: number },
) => {
  settings.dataFormat = values.dataFormat;
  settings.renderer = values.renderer;
  // Directional opacity depends on the camera position. A zero threshold makes
  // PlayCanvas run its color-only work-buffer pass for every non-zero camera
  // translation; geometry and transforms remain untouched.
  settings.colorUpdateAngle = 0;
  // DroneGS `fastgs` exports already encode the training-time footprint in
  // each Gaussian and explicitly disable compensated anti-aliasing. The
  // PlayCanvas compensation is only valid for splats trained/exported with
  // that option; forcing it here changes opacity and softens the reconstruction.
  settings.antiAlias = false;
  settings.minContribution = 0.05;
  settings.minPixelSize = 0.5;
  settings.alphaClip = 1 / 255;
  settings.alphaClipForward = 1 / 255;
  // This viewer uses an ordinary perspective camera which orbits by changing
  // position. PlayCanvas documents radial sorting for cubemap/fisheye views;
  // directional depth sorting is more accurate during camera translation.
  // Radial ordering made the centre look sharp while misordering overlapping
  // facade splats toward both screen edges, perceived as persistent blurry
  // tiles even when the selected cut contained exact leaves only.
  settings.radialSorting = false;
};

// The qualification RTX 4070 stays just below its 8 GiB dedicated budget at
// 7.5M while retaining shared-memory headroom for transient cut replacement.
// Source size is deliberately independent: a 100M bundle remains out-of-core.
const DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS = 7_500_000;
const DEFAULT_INITIAL_RESIDENT_GAUSSIANS = 1_250_000;
const DEFAULT_MAXIMUM_PROJECTED_ERROR_PIXELS = 2;
const OPACITY_STREAM_NAMES = [
  "droneOpacity0",
  "droneOpacity1",
  "droneOpacity2",
  "droneOpacity3",
] as const;
const FULL_ROTATION_STREAM = "droneRotationFull";
const FULL_SCALE_STREAM = "droneScaleFull";
const REFERENCE_FULL_TRANSFORM_MODIFIER_GLSL = /* glsl */ `
void modifySplatCenter(inout vec3 center) {}
void modifySplatRotationScale(
    vec3 originalCenter,
    vec3 modifiedCenter,
    inout vec4 rotation,
    inout vec3 scale
) {
    rotation = loadDroneRotationFull().yzwx;
    scale = loadDroneScaleFull().xyz;
}
void modifySplatColor(vec3 center, inout vec4 color) {}
`;
const REFERENCE_FULL_TRANSFORM_MODIFIER_WGSL = /* wgsl */ `
fn modifySplatCenter(center: ptr<function, vec3f>) {}
fn modifySplatRotationScale(
    originalCenter: vec3f,
    modifiedCenter: vec3f,
    rotation: ptr<function, vec4f>,
    scale: ptr<function, vec3f>
) {
    (*rotation) = loadDroneRotationFull().yzwx;
    (*scale) = loadDroneScaleFull().xyz;
}
fn modifySplatColor(center: vec3f, color: ptr<function, vec4f>) {}
`;

const DEFAULT_VIEW_FRAME: GaussianViewFrame = {
  kind: "facade",
  right: [1, 0, 0],
  up: [0, 1, 0],
  outward: [0, 0, 1],
};

const sha256 = async (content: ArrayBuffer) => {
  const digest = await crypto.subtle.digest("SHA-256", content);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
};

const centerOf = (minimum: Vec3, maximum: Vec3): Vec3 => [
  (minimum[0] + maximum[0]) / 2,
  (minimum[1] + maximum[1]) / 2,
  (minimum[2] + maximum[2]) / 2,
];

const dot = (left: readonly number[], right: readonly number[]) =>
  left[0] * right[0] + left[1] * right[1] + left[2] * right[2];

const combine = (
  first: readonly number[],
  firstScale: number,
  second: readonly number[],
  secondScale: number,
  third: readonly number[],
  thirdScale: number,
): Vec3 => [
  first[0] * firstScale + second[0] * secondScale + third[0] * thirdScale,
  first[1] * firstScale + second[1] * secondScale + third[1] * thirdScale,
  first[2] * firstScale + second[2] * secondScale + third[2] * thirdScale,
];

export const coordinateFrameCameraPosition = (
  cameraPosition: readonly number[],
  origin: readonly number[],
): Vec3 => [
  cameraPosition[0] + origin[0],
  cameraPosition[1] + origin[1],
  cameraPosition[2] + origin[2],
];

export const orbitCameraBasis = (
  frame: GaussianViewFrame,
  yaw: number,
  pitch: number,
) => {
  const sinYaw = Math.sin(yaw);
  const cosYaw = Math.cos(yaw);
  const sinPitch = Math.sin(pitch);
  const cosPitch = Math.cos(pitch);
  return {
    offset: combine(
      frame.outward,
      cosYaw * cosPitch,
      frame.right,
      sinYaw * cosPitch,
      frame.up,
      sinPitch,
    ),
    right: combine(frame.right, cosYaw, frame.outward, -sinYaw, frame.up, 0),
    up: combine(
      frame.outward,
      -cosYaw * sinPitch,
      frame.right,
      -sinYaw * sinPitch,
      frame.up,
      cosPitch,
    ),
  };
};

export const fitOrbitDistanceInFrame = (
  minimum: Vec3,
  maximum: Vec3,
  frame: GaussianViewFrame,
  verticalFovDegrees: number,
  aspectRatio: number,
  padding = 1.08,
) => {
  const center = centerOf(minimum, maximum);
  let halfWidth = 0;
  let halfHeight = 0;
  let halfDepth = 0;
  for (const x of [minimum[0], maximum[0]]) {
    for (const y of [minimum[1], maximum[1]]) {
      for (const z of [minimum[2], maximum[2]]) {
        const relative = [x - center[0], y - center[1], z - center[2]];
        halfWidth = Math.max(halfWidth, Math.abs(dot(relative, frame.right)));
        halfHeight = Math.max(halfHeight, Math.abs(dot(relative, frame.up)));
        halfDepth = Math.max(halfDepth, Math.abs(dot(relative, frame.outward)));
      }
    }
  }
  const tangentY = Math.tan((verticalFovDegrees * Math.PI) / 360);
  const tangentX = tangentY * Math.max(aspectRatio, 1e-6);
  return Math.max(
    halfDepth + padding * Math.max(halfWidth / tangentX, halfHeight / tangentY),
    0.01,
  );
};

export const fitOrbitDistance = (
  minimum: Vec3,
  maximum: Vec3,
  verticalFovDegrees: number,
  aspectRatio: number,
  padding = 1.08,
) => {
  return fitOrbitDistanceInFrame(
    minimum,
    maximum,
    DEFAULT_VIEW_FRAME,
    verticalFovDegrees,
    aspectRatio,
    padding,
  );
};

export const panOrbitTarget = (
  target: Vec3,
  yaw: number,
  pitch: number,
  distance: number,
  deltaX: number,
  deltaY: number,
  viewportHeight: number,
  verticalFovDegrees: number,
  frame: GaussianViewFrame = DEFAULT_VIEW_FRAME,
): Vec3 => {
  const scale =
    (2 * distance * Math.tan((verticalFovDegrees * Math.PI) / 360)) /
    Math.max(viewportHeight, 1);
  const { right, up } = orbitCameraBasis(frame, yaw, pitch);
  return [
    target[0] - right[0] * deltaX * scale + up[0] * deltaY * scale,
    target[1] - right[1] * deltaX * scale + up[1] * deltaY * scale,
    target[2] - right[2] * deltaX * scale + up[2] * deltaY * scale,
  ];
};

export const lodProxyCoverage = (
  node: GsTileNode,
  inflateReplacementProxy = true,
) => {
  const representation = node.tile ?? node.lodTile;
  const isProxy = node.tile === undefined && node.lodTile !== undefined;
  if (!representation || !isProxy || !inflateReplacementProxy) {
    return { multiplier: 1, maximumScale: Number.MAX_VALUE };
  }
  const sampleCount = Math.max(representation.recordCount, 1);
  const populationRatio = Math.max(node.gaussianCount / sampleCount, 1);
  const surfaceSpacing =
    Math.hypot(
      node.bounds.max[0] - node.bounds.min[0],
      node.bounds.max[1] - node.bounds.min[1],
      node.bounds.max[2] - node.bounds.min[2],
    ) / Math.sqrt(sampleCount);
  return {
    multiplier: populationRatio,
    maximumScale: Math.max(surfaceSpacing, 1e-6),
  };
};

const releasePlyPropertyStorage = (data: import("playcanvas").GSplatData) => {
  // GSplatResource has already uploaded every property. Keep the PLY element
  // schema/count used by numSplats, but release the large CPU coefficient arrays.
  for (const element of data.elements) {
    for (const property of element.properties) {
      property.storage = new Float32Array(0);
    }
  }
};

type DebugTileMode = "off" | "lod" | "id";

const hsvToRgb = (hue: number, saturation: number, value: number): Vec3 => {
  const chroma = value * saturation;
  const sector = (((hue % 1) + 1) % 1) * 6;
  const intermediate = chroma * (1 - Math.abs((sector % 2) - 1));
  const offset = value - chroma;
  const [red, green, blue] =
    sector < 1
      ? [chroma, intermediate, 0]
      : sector < 2
        ? [intermediate, chroma, 0]
        : sector < 3
          ? [0, chroma, intermediate]
          : sector < 4
            ? [0, intermediate, chroma]
            : sector < 5
              ? [intermediate, 0, chroma]
              : [chroma, 0, intermediate];
  return [red + offset, green + offset, blue + offset];
};

const hashTileId = (id: string) => {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

export const debugTileColor = (
  node: Pick<GsTileNode, "id" | "children">,
  mode: DebugTileMode,
  minimumLeafDepth: number,
  maximumDepth: number,
): Vec3 => {
  if (mode === "off") return [1, 1, 1];
  if (mode === "lod") {
    const depth = Math.max(node.id.length - 1, 0);
    if (node.children?.length) {
      // Red is reserved exclusively for a selected hierarchy proxy.
      return [1, 0.01, 0.01];
    }
    // Leaves use a non-red yellow-to-violet ramp by absolute tree depth.
    const span = Math.max(maximumDepth - minimumLeafDepth, 1);
    const progress = Math.min(
      Math.max((depth - minimumLeafDepth) / span, 0),
      1,
    );
    return hsvToRgb(0.16 + progress * 0.62, 0.86, 1);
  }
  const nodeId = node.id;
  const hash = hashTileId(nodeId);
  return hsvToRgb(
    (hash % 4093) / 4093,
    0.72 + (((hash >>> 12) & 255) / 255) * 0.23,
    0.88 + (((hash >>> 20) & 255) / 255) * 0.12,
  );
};

export class PlayCanvasResidentBackend implements GaussianRenderBackend {
  readonly id = "playcanvas-webgpu-resident-exact-v1";

  readonly #maximumResidentGaussians: number;
  readonly #initialResidentGaussians: number;
  readonly #maximumProjectedErrorPixels: number;
  readonly #background: [number, number, number];
  readonly #initialVerticalFovDegrees: number;
  readonly #useFloat32Transforms: boolean;
  readonly #maximumGaussianScale: number;
  readonly #includeSiblingLeaves: boolean;
  readonly #retainOffscreenCoverage: boolean;
  readonly #opacityMode: GsTileOpacityMode;
  readonly #sortMode: GsTileSortMode;
  readonly #radialSorting: boolean;
  readonly #referencePlyUrl: string | null;
  readonly #referencePlyParts: number;
  readonly #referencePlyTransformMode: "native" | "full-stream";
  readonly #referencePlyOpacityMode: "native" | "directional";
  readonly #referencePlyConstructionMode: "loader" | "manual";
  readonly #debugTiles: DebugTileMode;
  readonly #gpuAssembly: GsTileGpuAssembly;
  readonly #lodUpdateDelayMilliseconds: number;
  #pc: Pc | null = null;
  #app: PcApplication | null = null;
  #camera: PcEntity | null = null;
  #entities: PcEntity[] = [];
  #resources: PcResource[] = [];
  #canvas: HTMLCanvasElement | null = null;
  #residentGaussians = 0;
  #residentBytes = 0;
  #selectedNodes = 0;
  #targetGaussians = 0;
  #targetNodes = 0;
  #pendingNodes = 0;
  #maximumSelectedErrorPixels = 0;
  #effectiveMaximumErrorPixels = 0;
  #selectedExactNodes = 0;
  #selectedProxyNodes = 0;
  #selectedFullDepthNodes = 0;
  #selectedShallowLeafNodes = 0;
  #selectedInternalNodes = 0;
  #selectedLeafDepthCounts: number[] = [];
  #maximumSelectedProxyScreenRadiusPixels = 0;
  #lodState: GaussianRenderStatistics["lodState"] = "steady";
  #lastTimestampMs: number | null = null;
  #lastRenderedFrameTelemetry: GsTileFrameTelemetry = {
    frameCpuMs: null,
    frameGpuMs: null,
    workBufferUploadPercent: null,
    gpuPasses: [],
  };
  #target: Vec3 = [0, 0, 0];
  #yaw = 0;
  #pitch = 0;
  #distance = 1;
  #viewFrame = DEFAULT_VIEW_FRAME;
  #coordinateOrigin: Vec3 = [0, 0, 0];
  #pointerId: number | null = null;
  #pointerX = 0;
  #pointerY = 0;
  #pointerMode: "orbit" | "pan" | null = null;
  #cameraDirty = true;
  #removeInputListeners: (() => void) | null = null;
  #loadedTiles = new Map<string, LoadedTile>();
  #mergedArena: MergedArenaState | null = null;
  #retiredMergedStaging: LoadedTile[] = [];
  #releaseMergedArenaCpuSources = false;
  #lodManifest: GsTileManifest | null = null;
  #lodManifestUrl = "";
  #lodScheduler: GsTileRangeScheduler | null = null;
  #lodPackUrls: ReadonlyMap<string, string> | undefined;
  #lodSignal: AbortSignal | null = null;
  #lodSyncController: AbortController | null = null;
  #decodeWorkerPool: GsTileDecodeWorkerPool | null = null;
  #decodeWorkerPoolUnavailable = false;
  #lodGeneration = 0;
  #lodSelectionKey = "";
  #lodPendingKey = "";
  #lodUpdateTimer: ReturnType<typeof setTimeout> | null = null;
  #viewportWidth = 1;
  #viewportHeight = 1;
  #lodUsesMomentMatchedProxies = false;
  #minimumLodLeafDepth = 0;
  #maximumLodDepth = 0;
  #verifiedPackBuffers = new WeakSet<ArrayBuffer>();
  #forceWorkBufferRewriteFrames = 0;
  #renderFramesRemaining = 1;
  #lodTotalMs: number | null = null;
  #lodLoadMs: number | null = null;
  #lodCommitMs: number | null = null;
  #lodFetchServiceMs: number | null = null;
  #lodSha256ServiceMs: number | null = null;
  #lodDecodeCpuMs: number | null = null;
  #lodDecodeWorkerServiceMs: number | null = null;
  #lodDecodeWorkerFallbacks: number | null = null;
  #lodResourceCreateMs: number | null = null;
  #lodResourceColorMs: number | null = null;
  #lodResourceTransformMs: number | null = null;
  #lodResourceShMs: number | null = null;
  #lodStreamUploadMs: number | null = null;
  #lodSceneAttachMs: number | null = null;
  #lodAddedGaussians = 0;
  #lodRemovedGaussians = 0;
  #lodReusedGaussians = 0;
  #debugTraceEnabled = false;
  #debugSnapshotElement: HTMLScriptElement | null = null;
  #lastDebugSnapshotTimestampMs = -Infinity;

  constructor(options: PlayCanvasResidentBackendOptions = {}) {
    this.#debugTraceEnabled =
      typeof location !== "undefined" &&
      new URLSearchParams(location.search).get("dev") === "1";
    this.#maximumResidentGaussians =
      options.maximumResidentGaussians ?? DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS;
    this.#initialResidentGaussians = Math.min(
      options.initialResidentGaussians ?? DEFAULT_INITIAL_RESIDENT_GAUSSIANS,
      this.#maximumResidentGaussians,
    );
    this.#maximumProjectedErrorPixels =
      options.maximumProjectedErrorPixels ??
      DEFAULT_MAXIMUM_PROJECTED_ERROR_PIXELS;
    this.#background = options.background ?? [0.035, 0.055, 0.05];
    this.#initialVerticalFovDegrees = gstileVerticalFovDegrees(
      options.verticalFovDegrees,
    );
    this.#useFloat32Transforms = options.transformPrecision === "float32";
    this.#maximumGaussianScale =
      options.maximumGaussianScale ?? Number.MAX_VALUE;
    this.#includeSiblingLeaves = options.includeSiblingLeaves ?? false;
    this.#retainOffscreenCoverage = options.retainOffscreenCoverage ?? true;
    this.#opacityMode = options.opacityMode ?? "directional";
    this.#sortMode = options.sortMode ?? "gpu";
    this.#radialSorting = options.radialSorting ?? false;
    this.#referencePlyUrl = options.referencePlyUrl ?? null;
    this.#referencePlyParts = options.referencePlyParts ?? 1;
    this.#referencePlyTransformMode =
      options.referencePlyTransformMode ?? "native";
    this.#referencePlyOpacityMode = options.referencePlyOpacityMode ?? "native";
    this.#referencePlyConstructionMode =
      options.referencePlyConstructionMode ?? "loader";
    this.#debugTiles = options.debugTiles ?? "off";
    // A single merged GSplat resource is the fidelity baseline. PlayCanvas'
    // unified multi-resource path can retain stale allocation data across LOD
    // replacements, which presents as tile-aligned oversized/blurry splats.
    this.#gpuAssembly = gstileGpuAssembly(options.gpuAssembly);
    this.#lodUpdateDelayMilliseconds = gstileLodUpdateDelayMilliseconds(
      options.lodUpdateDelayMilliseconds,
    );
    if (
      !Number.isSafeInteger(this.#referencePlyParts) ||
      this.#referencePlyParts < 1 ||
      this.#referencePlyParts > 256
    ) {
      throw new Error("referencePlyParts must be an integer between 1 and 256");
    }
    if (
      !Number.isSafeInteger(this.#maximumResidentGaussians) ||
      this.#maximumResidentGaussians < 1
    ) {
      throw new Error("maximumResidentGaussians must be a positive integer");
    }
    if (
      !Number.isSafeInteger(this.#initialResidentGaussians) ||
      this.#initialResidentGaussians < 1
    ) {
      throw new Error("initialResidentGaussians must be a positive integer");
    }
    if (
      !Number.isFinite(this.#maximumProjectedErrorPixels) ||
      this.#maximumProjectedErrorPixels <= 0
    ) {
      throw new Error("maximumProjectedErrorPixels must be positive");
    }
    if (
      !Number.isFinite(this.#maximumGaussianScale) ||
      this.#maximumGaussianScale <= 0
    ) {
      throw new Error("maximumGaussianScale must be positive and finite");
    }
  }

  async initialize(canvas: HTMLCanvasElement) {
    if (this.#app)
      throw new Error("PlayCanvas GSTile backend is already initialized");
    if (!("gpu" in navigator)) {
      throw new GaussianBackendUnavailable(
        "Le viewer GSTile haute qualité nécessite WebGPU.",
      );
    }
    const pc = await import("playcanvas");
    const device = await pc.createGraphicsDevice(canvas, {
      deviceTypes: [pc.DEVICETYPE_WEBGPU],
      antialias: false,
      depth: true,
      powerPreference: "high-performance",
    });
    if (!device.isWebGPU) {
      device.destroy();
      throw new GaussianBackendUnavailable(
        "PlayCanvas n’a pas obtenu de périphérique WebGPU; le tri GPU global est indisponible.",
      );
    }

    const app = new pc.Application(canvas, { graphicsDevice: device });
    if (this.#debugTraceEnabled && device.gpuProfiler) {
      device.gpuProfiler.enabled = true;
    }
    configureHighQualityGsplatRendering(app.scene.gsplat, {
      dataFormat: pc.GSPLATDATA_LARGE,
      renderer:
        this.#sortMode === "cpu"
          ? pc.GSPLAT_RENDERER_RASTER_CPU_SORT
          : pc.GSPLAT_RENDERER_RASTER_GPU_SORT,
    });
    app.scene.gsplat.radialSorting = this.#radialSorting;

    const camera = new pc.Entity("GSTile camera");
    camera.addComponent("camera", {
      clearColor: new pc.Color(
        this.#background[0],
        this.#background[1],
        this.#background[2],
      ),
      nearClip: 0.01,
      farClip: 1_000_000,
      fov: this.#initialVerticalFovDegrees,
    });
    app.root.addChild(camera);

    this.#pc = pc;
    this.#app = app;
    this.#camera = camera;
    this.#canvas = canvas;
    if (this.#debugTraceEnabled) {
      (
        globalThis as typeof globalThis & {
          __gstileDebugSnapshot?: () => unknown;
        }
      ).__gstileDebugSnapshot = () => this.#debugSnapshot();
      const snapshotElement = document.createElement("script");
      snapshotElement.id = "gstile-debug-snapshot";
      snapshotElement.type = "application/json";
      snapshotElement.textContent = "{}";
      document.body.appendChild(snapshotElement);
      this.#debugSnapshotElement = snapshotElement;
    }
    this.#installOrbitInput(canvas);
  }

  async loadBundle(
    manifestUrl: string,
    manifest: GsTileManifest,
    scheduler: GsTileRangeScheduler,
    signal: AbortSignal,
    packUrls?: ReadonlyMap<string, string>,
    recommendedView?: GaussianViewFrame | null,
  ) {
    const pc = this.#pc;
    const app = this.#app;
    if (!pc || !app)
      throw new Error("PlayCanvas GSTile backend is not initialized");
    const hasLod = isGsTileLodProfile(manifest.profile);
    if (
      !hasLod &&
      manifest.source.gaussianCount > this.#maximumResidentGaussians
    ) {
      throw new GaussianBackendUnavailable(
        `Le profil résident est limité à ${this.#maximumResidentGaussians.toLocaleString()} splats; ` +
          `${manifest.source.gaussianCount.toLocaleString()} exigent le LOD hiérarchique.`,
      );
    }

    const root = manifest.nodes.find((node) => node.id === manifest.root);
    if (!root) throw new Error("GSTile root node is missing");
    const origin = manifest.coordinateFrame.origin;
    this.#coordinateOrigin = [...origin];
    const target = centerOf(root.bounds.min, root.bounds.max);
    this.#target = [
      target[0] - origin[0],
      target[1] - origin[1],
      target[2] - origin[2],
    ];
    this.#viewFrame = recommendedView ?? DEFAULT_VIEW_FRAME;
    this.#distance = fitOrbitDistanceInFrame(
      root.bounds.min,
      root.bounds.max,
      this.#viewFrame,
      this.#camera?.camera?.fov ?? this.#initialVerticalFovDegrees,
      this.#viewportWidth / this.#viewportHeight,
    );
    this.#cameraDirty = true;
    this.#updateCameraPose();

    if (this.#referencePlyUrl) {
      await this.#loadReferencePly(
        pc,
        app,
        this.#referencePlyUrl,
        this.#referencePlyParts,
        origin,
        signal,
      );
      return;
    }

    if (hasLod) {
      this.#lodUsesMomentMatchedProxies =
        manifest.profile === GSTILE_MOMENT_LOD_PROFILE ||
        manifest.profile === GSTILE_ADAPTIVE_LOD_PROFILE;
      this.#lodManifest = manifest;
      this.#maximumLodDepth = Math.max(
        ...manifest.nodes.map((node) => Math.max(node.id.length - 1, 0)),
      );
      const leafDepths = manifest.nodes
        .filter((node) => !node.children?.length)
        .map((node) => Math.max(node.id.length - 1, 0));
      this.#minimumLodLeafDepth =
        leafDepths.length > 0 ? Math.min(...leafDepths) : this.#maximumLodDepth;
      this.#lodManifestUrl = manifestUrl;
      this.#lodScheduler = scheduler;
      this.#lodPackUrls = packUrls;
      this.#lodSignal = signal;
      await this.#synchronizeLod(signal, this.#initialResidentGaussians);
      this.#scheduleLodUpdate();
      return;
    }

    const tiledNodes = manifest.nodes.filter(
      (node): node is GsTileNode & { tile: NonNullable<GsTileNode["tile"]> } =>
        node.tile !== undefined,
    );
    const nodesByPack = new Map<string, typeof tiledNodes>();
    for (const node of tiledNodes) {
      const nodes = nodesByPack.get(node.tile.pack) ?? [];
      nodes.push(node);
      nodesByPack.set(node.tile.pack, nodes);
    }

    for (const pack of manifest.packs) {
      signal.throwIfAborted();
      const nodes = nodesByPack.get(pack.id);
      if (!nodes?.length) continue;
      const url =
        packUrls?.get(pack.id) ?? resolveGsTilePackUrl(manifestUrl, pack.path);
      const range = { start: 0, length: pack.byteLength };
      const immutableIdentity = `sha256:${pack.sha256.toLowerCase()}`;
      const content = await scheduler.fetch(
        url,
        range,
        signal,
        immutableIdentity,
      );
      const actualSha256 = await sha256(content);
      if (actualSha256 !== pack.sha256.toLowerCase()) {
        throw new Error(`GSTile pack ${pack.id} failed SHA-256 validation`);
      }
      scheduler.persistVerified(immutableIdentity, range, content);
      for (const node of nodes) {
        signal.throwIfAborted();
        if (node.tile.sha256.toLowerCase() !== actualSha256) {
          throw new Error(
            `GSTile node ${node.id} references an unexpected pack hash`,
          );
        }
        const decoded = decodeGsTilePackTile(
          content,
          node.tile.byteOffset,
          node.tile.byteLength,
          node.tile.recordCount,
          node.tile.quantization,
        );
        this.#addTileResource(pc, app, decoded, origin, node);
      }
      this.#residentBytes += pack.byteLength;
    }
    if (this.#residentGaussians !== manifest.source.gaussianCount) {
      throw new Error(
        `GSTile resident count mismatch (${this.#residentGaussians}/${manifest.source.gaussianCount})`,
      );
    }
    this.#updateCameraPose();
  }

  setCamera(camera: GaussianCameraState) {
    const pc = this.#pc;
    const entity = this.#camera;
    if (!pc || !entity) return;
    const view = new pc.Mat4();
    for (let index = 0; index < 16; index += 1) {
      view.data[index] = camera.view[index];
    }
    const world = view.clone().invert();
    entity.setPosition(world.getTranslation());
    entity.setRotation(new pc.Quat().setFromMat4(world));
    const projectionY = camera.projection[5];
    if (entity.camera && Number.isFinite(projectionY) && projectionY > 0) {
      entity.camera.fov = gstileVerticalFovDegrees(
        (2 * Math.atan(1 / projectionY) * 180) / Math.PI,
      );
    }
    this.#cameraDirty = false;
    this.#updateOpacityCameraUniform();
    this.#scheduleLodUpdate();
    this.#requestRender();
  }

  render(timestampMs: number): GaussianRenderStatistics {
    const app = this.#app;
    if (!app) return this.#statistics(null);
    const start = performance.now();
    const deltaSeconds = Math.min(
      Math.max(
        ((this.#lastTimestampMs ?? timestampMs) - timestampMs) / -1000,
        0,
      ),
      0.1,
    );
    this.#lastTimestampMs = timestampMs;
    if (this.#cameraDirty) this.#updateCameraPose();
    const pc = this.#pc;
    const forceWorkBufferRewrite =
      this.#forceWorkBufferRewriteFrames > 0 && pc !== null;
    // CPU sorting is completed asynchronously by a worker. Keep advancing the
    // PlayCanvas manager in this explicit diagnostic mode so its pending order
    // can be applied after an atomic cut replacement. The production GPU path
    // remains render-on-demand and therefore idle when the camera is steady.
    if (
      !forceWorkBufferRewrite &&
      this.#renderFramesRemaining === 0 &&
      this.#sortMode !== "cpu"
    ) {
      this.#updateDebugSnapshot(timestampMs);
      return this.#statistics(0, false);
    }
    if (forceWorkBufferRewrite) {
      for (const entity of this.#entities) {
        if (entity.gsplat) {
          entity.gsplat.workBufferUpdate = pc.WORKBUFFER_UPDATE_ALWAYS;
        }
      }
      // This also forces PlayCanvas to rebuild the unified allocation map,
      // rather than relying only on the placement diff generated by the cut.
      app.scene.gsplat.dirty = true;
    }
    app.update(deltaSeconds);
    app.render();
    if (this.#releaseMergedArenaCpuSources && this.#mergedArena) {
      releasePlayCanvasTextureCpuSources(
        this.#mergedArena.container.streams.textures.values(),
      );
      this.#releaseMergedArenaCpuSources = false;
    }
    // Keep GPU-copy sources alive until the frame consuming the copied arena
    // streams has been submitted. Destroying them during the LOD commit can
    // invalidate deferred WebGPU work on some drivers.
    for (const retired of this.#retiredMergedStaging) {
      this.#destroyUnregisteredTile(retired);
    }
    this.#retiredMergedStaging.length = 0;
    this.#updateDebugSnapshot(timestampMs);
    this.#renderFramesRemaining = Math.max(this.#renderFramesRemaining - 1, 0);
    if (forceWorkBufferRewrite) {
      this.#forceWorkBufferRewriteFrames -= 1;
      if (this.#forceWorkBufferRewriteFrames === 0) {
        for (const entity of this.#entities) {
          if (entity.gsplat) {
            entity.gsplat.workBufferUpdate = pc.WORKBUFFER_UPDATE_AUTO;
          }
        }
      }
    }
    return this.#statistics(performance.now() - start);
  }

  resize(width: number, height: number, devicePixelRatio: number) {
    const app = this.#app;
    if (!app) return;
    const maximumRatio = Math.min(Math.max(devicePixelRatio, 1), 2);
    const pixelWidth = Math.max(1, Math.round(width * maximumRatio));
    const pixelHeight = Math.max(1, Math.round(height * maximumRatio));
    this.#viewportWidth = pixelWidth;
    this.#viewportHeight = pixelHeight;
    if (
      app.graphicsDevice.width === pixelWidth &&
      app.graphicsDevice.height === pixelHeight
    ) {
      return;
    }
    app.graphicsDevice.resizeCanvas(pixelWidth, pixelHeight);
    this.#scheduleLodUpdate();
    this.#requestRender();
  }

  dispose() {
    this.#lodGeneration += 1;
    this.#lodSyncController?.abort(
      new DOMException("GSTile backend disposed", "AbortError"),
    );
    this.#lodSyncController = null;
    this.#decodeWorkerPool?.dispose(
      new DOMException("GSTile backend disposed", "AbortError"),
    );
    this.#decodeWorkerPool = null;
    if (this.#lodUpdateTimer !== null) clearTimeout(this.#lodUpdateTimer);
    this.#lodUpdateTimer = null;
    this.#removeInputListeners?.();
    this.#removeInputListeners = null;
    for (const retired of this.#retiredMergedStaging) {
      this.#destroyUnregisteredTile(retired);
    }
    this.#retiredMergedStaging.length = 0;
    for (const entity of this.#entities) entity.destroy();
    for (const resource of this.#resources) resource.destroy();
    this.#entities = [];
    this.#resources = [];
    this.#loadedTiles.clear();
    this.#mergedArena = null;
    this.#camera?.destroy();
    this.#camera = null;
    this.#app?.destroy();
    this.#app = null;
    this.#pc = null;
    this.#canvas = null;
    this.#lodManifest = null;
    this.#lodManifestUrl = "";
    this.#lodScheduler = null;
    this.#lodPackUrls = undefined;
    this.#lodSignal = null;
    this.#lodSelectionKey = "";
    this.#lodPendingKey = "";
    this.#targetGaussians = 0;
    this.#targetNodes = 0;
    this.#pendingNodes = 0;
    this.#maximumSelectedErrorPixels = 0;
    this.#effectiveMaximumErrorPixels = 0;
    this.#selectedExactNodes = 0;
    this.#selectedProxyNodes = 0;
    this.#selectedFullDepthNodes = 0;
    this.#selectedShallowLeafNodes = 0;
    this.#selectedInternalNodes = 0;
    this.#selectedLeafDepthCounts = [];
    this.#maximumSelectedProxyScreenRadiusPixels = 0;
    this.#lodState = "steady";
    this.#lodUsesMomentMatchedProxies = false;
    this.#minimumLodLeafDepth = 0;
    this.#maximumLodDepth = 0;
    this.#coordinateOrigin = [0, 0, 0];
    this.#verifiedPackBuffers = new WeakSet<ArrayBuffer>();
    this.#forceWorkBufferRewriteFrames = 0;
    this.#renderFramesRemaining = 0;
    this.#lodTotalMs = null;
    this.#lodLoadMs = null;
    this.#lodCommitMs = null;
    this.#lodFetchServiceMs = null;
    this.#lodSha256ServiceMs = null;
    this.#lodDecodeCpuMs = null;
    this.#lodDecodeWorkerServiceMs = null;
    this.#lodDecodeWorkerFallbacks = null;
    this.#lodResourceCreateMs = null;
    this.#lodResourceColorMs = null;
    this.#lodResourceTransformMs = null;
    this.#lodResourceShMs = null;
    this.#lodStreamUploadMs = null;
    this.#lodSceneAttachMs = null;
    this.#lodAddedGaussians = 0;
    this.#lodRemovedGaussians = 0;
    this.#lodReusedGaussians = 0;
    if (this.#debugTraceEnabled) {
      delete (
        globalThis as typeof globalThis & {
          __gstileDebugSnapshot?: () => unknown;
        }
      ).__gstileDebugSnapshot;
    }
    this.#debugSnapshotElement?.remove();
    this.#debugSnapshotElement = null;
    this.#lastDebugSnapshotTimestampMs = -Infinity;
  }

  async #loadReferencePly(
    pc: Pc,
    app: PcApplication,
    url: string,
    partCount: number,
    origin: Vec3,
    signal: AbortSignal,
  ) {
    const loadPart = async (part: number) => {
      signal.throwIfAborted();
      const partUrl = new URL(url, location.href);
      if (partCount > 1) {
        partUrl.searchParams.set("part", String(part));
        partUrl.searchParams.set("parts", String(partCount));
      }
      const label = partCount > 1 ? ` ${part + 1}/${partCount}` : "";
      const asset = new pc.Asset(
        `GSTile exact cut reference${label}`,
        "gsplat",
        {
          url: partUrl.toString(),
          filename:
            partCount > 1
              ? `gstile-reference-${part}-of-${partCount}.ply`
              : "gstile-reference-cut-minimal.ply",
        },
        { reorder: false },
      );
      app.assets.add(asset);
      const resource = await new Promise<PcResource>((resolve, reject) => {
        const cleanup = () => {
          signal.removeEventListener("abort", abort);
          asset.off("load", loaded);
          asset.off("error", failed);
        };
        const loaded = () => {
          cleanup();
          const value = asset.resource as PcResource | null;
          if (value) resolve(value);
          else
            reject(
              new Error(
                "PlayCanvas loaded a reference PLY without a GSplat resource",
              ),
            );
        };
        const failed = (error: unknown) => {
          cleanup();
          reject(error instanceof Error ? error : new Error(String(error)));
        };
        const abort = () => {
          cleanup();
          asset.unload();
          reject(signal.reason);
        };
        signal.addEventListener("abort", abort, { once: true });
        asset.once("load", loaded);
        asset.once("error", failed);
        app.assets.load(asset);
      });
      signal.throwIfAborted();
      return { part, resource };
    };

    const loadedParts: Array<{ part: number; resource: PcResource }> = [];
    const concurrency = Math.min(8, partCount);
    for (let start = 0; start < partCount; start += concurrency) {
      const batch = Array.from(
        { length: Math.min(concurrency, partCount - start) },
        (_, offset) => loadPart(start + offset),
      );
      loadedParts.push(...(await Promise.all(batch)));
    }
    loadedParts.sort((left, right) => left.part - right.part);

    let totalSplats = 0;
    for (const { part, resource: loaderResource } of loadedParts) {
      const directionalOpacity =
        this.#referencePlyOpacityMode === "directional";
      const loaderData =
        loaderResource.gsplatData as import("playcanvas").GSplatData;
      const loaderProperty = (name: string) => {
        const storage = loaderData.getProp(name);
        if (!(storage instanceof Float32Array)) {
          throw new Error(`Reference PLY property ${name} is unavailable`);
        }
        return storage;
      };
      let resource = loaderResource;
      let manualData: import("playcanvas").GSplatData | null = null;
      let manualDecoded: DecodedGsTile | null = null;
      if (this.#referencePlyConstructionMode === "manual") {
        const count = loaderResource.numSplats;
        const interleave = (names: readonly string[]) => {
          const columns = names.map(loaderProperty);
          const result = new Float32Array(count * columns.length);
          for (let row = 0; row < count; row += 1) {
            for (let column = 0; column < columns.length; column += 1) {
              result[row * columns.length + column] = columns[column][row];
            }
          }
          return result;
        };
        manualDecoded = {
          header: null as unknown as DecodedGsTile["header"],
          count,
          position: interleave(["x", "y", "z"]),
          colorDc: interleave(["f_dc_0", "f_dc_1", "f_dc_2"]),
          // The minimal reference fixture intentionally carries only DC color.
          // Zero SH coefficients preserve that image while exercising the same
          // SH3 GSplatData/GSplatResource construction path as production tiles.
          colorSh: new Float32Array(count * 45),
          opacityLogit: loaderProperty("opacity").slice(),
          logScale: interleave(["scale_0", "scale_1", "scale_2"]),
          rotation: interleave(["rot_0", "rot_1", "rot_2", "rot_3"]),
          opacitySh: interleave(
            Array.from(
              { length: 15 },
              (_, coefficient) => `opacity_sh_${coefficient}`,
            ),
          ),
          sourceId: new BigUint64Array(count),
        };
        manualData = new pc.GSplatData([
          {
            name: "vertex",
            count,
            properties: gsTileToPlyProperties(manualDecoded),
          },
        ]);
        resource = new pc.GSplatResource(app.graphicsDevice, manualData);
      }
      const referenceData =
        resource.gsplatData as import("playcanvas").GSplatData;
      const property = (name: string) => {
        const storage = referenceData.getProp(name);
        if (!(storage instanceof Float32Array)) {
          throw new Error(`Reference PLY property ${name} is unavailable`);
        }
        return storage;
      };
      if (this.#referencePlyTransformMode === "full-stream") {
        resource.format.addExtraStreams([
          { name: FULL_ROTATION_STREAM, format: pc.PIXELFORMAT_RGBA32F },
          { name: FULL_SCALE_STREAM, format: pc.PIXELFORMAT_RGBA32F },
        ]);
        const rw = property("rot_0");
        const rx = property("rot_1");
        const ry = property("rot_2");
        const rz = property("rot_3");
        const sx = property("scale_0");
        const sy = property("scale_1");
        const sz = property("scale_2");
        const rotation = resource.getTexture(FULL_ROTATION_STREAM);
        const scale = resource.getTexture(FULL_SCALE_STREAM);
        if (!rotation || !scale) {
          throw new Error(
            "PlayCanvas did not allocate reference transform streams",
          );
        }
        const rotationData = rotation.lock() as Float32Array;
        const scaleData = scale.lock() as Float32Array;
        for (let splat = 0; splat < resource.numSplats; splat += 1) {
          const offset = splat * 4;
          rotationData[offset] = rw[splat];
          rotationData[offset + 1] = rx[splat];
          rotationData[offset + 2] = ry[splat];
          rotationData[offset + 3] = rz[splat];
          scaleData[offset] = Math.exp(sx[splat]);
          scaleData[offset + 1] = Math.exp(sy[splat]);
          scaleData[offset + 2] = Math.exp(sz[splat]);
        }
        rotation.unlock();
        scale.unlock();
      }
      if (directionalOpacity) {
        resource.format.addExtraStreams(
          OPACITY_STREAM_NAMES.map((name) => ({
            name,
            format: pc.PIXELFORMAT_RGBA32F,
          })),
        );
        const manualOpacityStreams = manualDecoded
          ? gsTileOpacityStreams(manualDecoded)
          : null;
        const opacityProperties = manualOpacityStreams
          ? null
          : [
              property("opacity"),
              ...Array.from({ length: 15 }, (_, coefficient) =>
                loaderProperty(`opacity_sh_${coefficient}`),
              ),
            ];
        OPACITY_STREAM_NAMES.forEach((name, streamIndex) => {
          const texture = resource.getTexture(name);
          if (!texture) throw new Error(`PlayCanvas did not allocate ${name}`);
          const destination = texture.lock() as Float32Array;
          if (manualOpacityStreams) {
            destination.set(manualOpacityStreams[streamIndex]);
          } else if (opacityProperties) {
            for (let splat = 0; splat < resource.numSplats; splat += 1) {
              const target = splat * 4;
              for (let channel = 0; channel < 4; channel += 1) {
                destination[target + channel] =
                  opacityProperties[streamIndex * 4 + channel][splat];
              }
            }
          }
          texture.unlock();
        });
      }
      const entity = new pc.Entity(
        partCount > 1
          ? `GSTile exact cut reference PLY ${part + 1}/${partCount}`
          : "GSTile exact cut reference PLY",
      );
      entity.setPosition(-origin[0], -origin[1], -origin[2]);
      entity.addComponent("gsplat", { unified: true });
      if (!entity.gsplat)
        throw new Error("PlayCanvas GSplat component is unavailable");
      entity.gsplat.resource = resource;
      if (directionalOpacity) {
        const fullTransform = this.#referencePlyTransformMode === "full-stream";
        entity.gsplat.setWorkBufferModifier({
          glsl: DRONEGS_OPACITY_MODIFIER_GLSL.replace(
            "// DRONEGS_FULL_TRANSFORM_GLSL",
            fullTransform
              ? "rotation = loadDroneRotationFull().yzwx;\n    scale = loadDroneScaleFull().xyz;"
              : "",
          ),
          wgsl: DRONEGS_OPACITY_MODIFIER_WGSL.replace(
            "// DRONEGS_FULL_TRANSFORM_WGSL",
            fullTransform
              ? "(*rotation) = loadDroneRotationFull().yzwx;\n    (*scale) = loadDroneScaleFull().xyz;"
              : "",
          ),
        });
        entity.gsplat.setParameter("uDroneLodScaleMultiplier", 1);
        entity.gsplat.setParameter("uDroneLodMaximumScale", 1.0e20);
        entity.gsplat.setParameter("uDroneDebugTileColor", [0, 0, 0]);
        entity.gsplat.setParameter("uDroneDebugTileMix", 0);
      } else if (this.#referencePlyTransformMode === "full-stream") {
        entity.gsplat.setWorkBufferModifier({
          glsl: REFERENCE_FULL_TRANSFORM_MODIFIER_GLSL,
          wgsl: REFERENCE_FULL_TRANSFORM_MODIFIER_WGSL,
        });
      }
      app.root.addChild(entity);
      if (manualData) releasePlyPropertyStorage(manualData);
      this.#entities.push(entity);
      this.#resources.push(resource);
      totalSplats += resource.numSplats;
    }

    this.#residentGaussians = totalSplats;
    this.#selectedNodes = partCount;
    this.#targetGaussians = totalSplats;
    this.#targetNodes = partCount;
    this.#selectedExactNodes = partCount;
    this.#selectedFullDepthNodes = partCount;
    this.#lodState = "steady";
    this.#updateCameraPose();
  }

  #addTileResource(
    pc: Pc,
    app: PcApplication,
    tile: DecodedGsTile,
    origin: Vec3,
    node: GsTileNode,
  ) {
    const loaded = this.#createTileResource(pc, app, tile, origin, node, true);
    this.#registerTile(node.id, loaded);
  }

  #createTileResource(
    pc: Pc,
    app: PcApplication,
    tile: DecodedGsTile | GsTilePlayCanvasColumns,
    origin: Vec3,
    node: GsTileNode,
    enabled: boolean,
    byteLength = 0,
    useManifestRenderBounds = true,
    arenaActiveGaussianCount?: number,
  ): LoadedTile {
    const resourceStarted = performance.now();
    const properties =
      "properties" in tile ? tile.properties : gsTileToPlyProperties(tile);
    class DroneGsMergedStagingData extends pc.GSplatData {
      override getCenters() {
        return "properties" in tile && tile.centerStream
          ? tile.centerStream
          : super.getCenters();
      }

      override calcAabb(
        result: import("playcanvas").BoundingBox,
        predicate?: (index: number) => boolean,
      ) {
        if (!("properties" in tile) || !tile.centerStream || predicate) {
          return super.calcAabb(result, predicate);
        }
        if (!tile.bounds.valid) return false;
        const { minimum, maximum } = tile.bounds;
        result.center.set(
          (minimum[0] + maximum[0]) * 0.5,
          (minimum[1] + maximum[1]) * 0.5,
          (minimum[2] + maximum[2]) * 0.5,
        );
        result.halfExtents.set(
          (maximum[0] - minimum[0]) * 0.5,
          (maximum[1] - minimum[1]) * 0.5,
          (maximum[2] - minimum[2]) * 0.5,
        );
        return true;
      }
    }
    const Data = "properties" in tile
      ? DroneGsMergedStagingData
      : pc.GSplatData;
    const data = new Data([
      {
        name: "vertex",
        count: tile.count,
        properties,
      },
    ]);
    const resourceStageMs = { color: 0, transform: 0, sh: 0 };
    const Resource =
      "properties" in tile
        ? class DroneGsMergedStagingResource extends pc.GSplatResource {
            override updateColorData(
              gsplatData: import("playcanvas").GSplatData,
            ) {
              const started = performance.now();
              if (tile.colorStream) {
                adoptGsTileNativeRgbaStreams(
                  this.streams,
                  this.format,
                  ["splatColor"],
                  [tile.colorStream],
                );
              } else {
                super.updateColorData(gsplatData);
              }
              resourceStageMs.color += performance.now() - started;
            }

            override updateTransformData(
              gsplatData: import("playcanvas").GSplatData,
            ) {
              const started = performance.now();
              if (tile.transformStreams) {
                adoptGsTileNativeRgbaStreams(
                  this.streams,
                  this.format,
                  ["transformA", "transformB"],
                  tile.transformStreams,
                );
                resourceStageMs.transform += performance.now() - started;
                return;
              }
              const property = (name: string) => {
                const storage = gsplatData.getProp(name);
                if (!(storage instanceof Float32Array)) {
                  throw new Error(`GSTile transform property ${name} is missing`);
                }
                return storage;
              };
              const transformA = this.getTexture("transformA");
              const transformB = this.getTexture("transformB");
              if (!transformA || !transformB) {
                throw new Error("GSTile native transform streams are missing");
              }
              const outputA = transformA.lock() as Uint32Array;
              const outputB = transformB.lock() as Uint16Array;
              try {
                packGsTileNativeTransforms(
                  {
                    position: [property("x"), property("y"), property("z")],
                    centerStream: tile.centerStream,
                    logScale: [
                      property("scale_0"),
                      property("scale_1"),
                      property("scale_2"),
                    ],
                    rotation: [
                      property("rot_0"),
                      property("rot_1"),
                      property("rot_2"),
                      property("rot_3"),
                    ],
                  },
                  outputA,
                  outputB,
                  pc.FloatPacking.float2Half,
                  globalThis.Float16Array,
                  {
                    activeCount: arenaActiveGaussianCount ?? tile.count,
                    rotationIsNormalized: true,
                  },
                );
              } finally {
                transformA.unlock();
                transformB.unlock();
              }
              resourceStageMs.transform += performance.now() - started;
            }

            override updateSHData(
              gsplatData: import("playcanvas").GSplatData,
            ) {
              const started = performance.now();
              const names = [
                "splatSH_1to3",
                "splatSH_4to7",
                "splatSH_8to11",
                "splatSH_12to15",
              ] as const;
              try {
                if (tile.shStreams) {
                  adoptGsTileNativeRgbaStreams(
                    this.streams,
                    this.format,
                    names,
                    tile.shStreams,
                  );
                  return;
                }
                const textures = names.map((name) => this.getTexture(name));
                if (textures.some((texture) => !texture)) {
                  throw new Error("GSTile native SH3 streams are missing");
                }
                const streams = textures.map((texture) =>
                  texture!.lock(),
                ) as unknown as [
                  Uint32Array,
                  Uint32Array,
                  Uint32Array,
                  Uint32Array,
                ];
                try {
                  const properties = Array.from(
                    { length: 45 },
                    (_, coefficient) => {
                      const storage = gsplatData.getProp(
                        `f_rest_${coefficient}`,
                      );
                      if (!(storage instanceof Float32Array)) {
                        throw new Error(
                          `GSTile SH property f_rest_${coefficient} is missing`,
                        );
                      }
                      return storage;
                    },
                  );
                  packGsTileNativeSh(properties, streams);
                } finally {
                  textures.forEach((texture) => texture!.unlock());
                }
              } finally {
                resourceStageMs.sh += performance.now() - started;
              }
            }
          }
        : pc.GSplatResource;
    const resource = new Resource(app.graphicsDevice, data);
    const arenaResource =
      arenaActiveGaussianCount === undefined
        ? undefined
        : (configurePlayCanvasGsplatArenaResource(
            resource,
            data,
            tile.count,
            arenaActiveGaussianCount,
          ) as PcArenaResource);
    const resourceCreateMs = performance.now() - resourceStarted;
    const streamUploadStarted = performance.now();
    if (useManifestRenderBounds) {
      // Individual GSTile resources need the producer's conservative support
      // for interval culling. A merged cut is deliberately different: it must
      // keep the native AABB computed from its actual splats, exactly like the
      // known-good PLY path, because PlayCanvas also derives the GPU sort range
      // from this AABB.
      const renderBounds = node.renderBounds ?? node.bounds;
      resource.aabb.center.set(
        (renderBounds.min[0] + renderBounds.max[0]) * 0.5,
        (renderBounds.min[1] + renderBounds.max[1]) * 0.5,
        (renderBounds.min[2] + renderBounds.max[2]) * 0.5,
      );
      resource.aabb.halfExtents.set(
        (renderBounds.max[0] - renderBounds.min[0]) * 0.5,
        (renderBounds.max[1] - renderBounds.min[1]) * 0.5,
        (renderBounds.max[2] - renderBounds.min[2]) * 0.5,
      );
    }
    resource.format.addExtraStreams(
      [
        ...OPACITY_STREAM_NAMES,
        ...(this.#useFloat32Transforms
          ? [FULL_ROTATION_STREAM, FULL_SCALE_STREAM]
          : []),
      ].map((name) => ({
        name,
        format: pc.PIXELFORMAT_RGBA32F,
      })),
    );
    if (this.#useFloat32Transforms) {
      const rotation = resource.getTexture(FULL_ROTATION_STREAM);
      const scale = resource.getTexture(FULL_SCALE_STREAM);
      if (!rotation || !scale)
        throw new Error("PlayCanvas did not allocate full transform streams");
      const fullRotation = rotation.lock() as Float32Array;
      if ("properties" in tile) {
        for (let record = 0; record < tile.count; record += 1) {
          for (let component = 0; component < 4; component += 1) {
            fullRotation[record * 4 + component] =
              tile.rotation[component][record];
          }
        }
      } else {
        fullRotation.set(tile.rotation);
      }
      rotation.unlock();
      const linearScale = scale.lock() as Float32Array;
      if ("properties" in tile) {
        for (let record = 0; record < tile.count; record += 1) {
          for (let axis = 0; axis < 3; axis += 1) {
            linearScale[record * 4 + axis] = Math.exp(
              tile.logScale[axis][record],
            );
          }
        }
      } else {
        for (let record = 0; record < tile.count; record += 1) {
          for (let axis = 0; axis < 3; axis += 1) {
            linearScale[record * 4 + axis] = Math.exp(
              tile.logScale[record * 3 + axis],
            );
          }
        }
      }
      scale.unlock();
    }
    if ("properties" in tile) {
      adoptGsTileNativeRgbaStreams(
        resource.streams,
        resource.format,
        OPACITY_STREAM_NAMES,
        tile.opacityStreams,
      );
    } else {
      const opacityStreams = gsTileOpacityStreams(tile);
      OPACITY_STREAM_NAMES.forEach((name, index) => {
        const texture = resource.getTexture(name);
        if (!texture) throw new Error(`PlayCanvas did not allocate ${name}`);
        const destination = texture.lock() as Float32Array;
        destination.set(opacityStreams[index]);
        texture.unlock();
      });
    }
    const streamUploadMs = performance.now() - streamUploadStarted;

    const sceneAttachStarted = performance.now();
    const entity = this.#createTileEntity(
      pc,
      app,
      resource,
      origin,
      node,
      enabled,
    );
    releasePlyPropertyStorage(data);
    const sceneAttachMs = performance.now() - sceneAttachStarted;
    return {
      entity,
      resource,
      arenaResource,
      gaussianCount: arenaActiveGaussianCount ?? tile.count,
      byteLength,
      resourceCreateMs,
      resourceColorMs: resourceStageMs.color,
      resourceTransformMs: resourceStageMs.transform,
      resourceShMs: resourceStageMs.sh,
      streamUploadMs,
      sceneAttachMs,
    };
  }

  #createTileEntity(
    pc: Pc,
    app: PcApplication,
    resource: PcResource,
    origin: Vec3,
    node: GsTileNode,
    enabled: boolean,
  ) {
    const entity = new pc.Entity(`GSTile ${node.id}`);
    entity.enabled = enabled;
    entity.setPosition(-origin[0], -origin[1], -origin[2]);
    entity.addComponent("gsplat", { unified: true });
    if (!entity.gsplat)
      throw new Error("PlayCanvas GSplat component is unavailable");
    entity.gsplat.resource = resource;
    entity.gsplat.setWorkBufferModifier(
      this.#useFloat32Transforms
        ? {
            glsl: DRONEGS_OPACITY_MODIFIER_GLSL.replace(
              "// DRONEGS_FULL_TRANSFORM_GLSL",
              "rotation = loadDroneRotationFull().yzwx;\n    scale = loadDroneScaleFull().xyz;",
            ),
            wgsl: DRONEGS_OPACITY_MODIFIER_WGSL.replace(
              "// DRONEGS_FULL_TRANSFORM_WGSL",
              "(*rotation) = loadDroneRotationFull().yzwx;\n    (*scale) = loadDroneScaleFull().xyz;",
            ),
          }
        : {
            glsl: DRONEGS_OPACITY_MODIFIER_GLSL,
            wgsl: DRONEGS_OPACITY_MODIFIER_WGSL,
          },
    );
    entity.gsplat.setParameter(
      "uDroneOpacityMode",
      gstileOpacityModeUniform(this.#opacityMode),
    );
    // Incremental mode preserves each decoded resource exactly as it appears
    // in the known-good monolithic cut. Proxy support inflation belongs to the
    // legacy tiled renderer and is deliberately disabled here.
    const coverage =
      this.#gpuAssembly === "incremental"
        ? { multiplier: 1, maximumScale: Number.POSITIVE_INFINITY }
        : lodProxyCoverage(node, !this.#lodUsesMomentMatchedProxies);
    entity.gsplat.setParameter("uDroneLodScaleMultiplier", coverage.multiplier);
    entity.gsplat.setParameter(
      "uDroneLodMaximumScale",
      Math.min(coverage.maximumScale, this.#maximumGaussianScale),
    );
    entity.gsplat.setParameter(
      "uDroneDebugTileColor",
      debugTileColor(
        node,
        this.#debugTiles,
        this.#minimumLodLeafDepth,
        this.#maximumLodDepth,
      ),
    );
    entity.gsplat.setParameter(
      "uDroneDebugTileMix",
      this.#debugTiles === "off" ? 0 : 0.42,
    );
    const cameraPosition = this.#camera?.getPosition();
    if (cameraPosition) {
      entity.gsplat.setParameter(
        "uDroneCameraPosition",
        coordinateFrameCameraPosition(
          [cameraPosition.x, cameraPosition.y, cameraPosition.z],
          origin,
        ),
      );
      entity.gsplat.workBufferUpdate = pc.WORKBUFFER_UPDATE_ONCE;
    }
    app.root.addChild(entity);
    return entity;
  }

  #copyGsplatResourceRange(
    source: PcResource,
    destination: PcArenaResource,
    sourceOffset: number,
    destinationOffset: number,
    count: number,
  ) {
    for (const stream of source.format.resourceStreams) {
      const sourceTexture = source.getTexture(stream.name);
      const destinationTexture = destination.getTexture(stream.name);
      if (!sourceTexture || !destinationTexture) {
        throw new Error(`GSTile arena stream ${stream.name} is unavailable`);
      }
      const copies = planLinearTextureCopies(
        sourceTexture.width,
        sourceTexture.height,
        sourceOffset,
        destinationTexture.width,
        destinationTexture.height,
        destinationOffset,
        count,
      );
      for (const options of copies) {
        if (!destinationTexture.copy(sourceTexture, options)) {
          throw new Error(`GSTile arena stream ${stream.name} copy failed`);
        }
      }
    }
  }

  #copyMergedArenaCenters(
    columns: GsTilePlayCanvasColumns,
    sourceOffset: number,
    destination: PcArenaResource,
    destinationOffset: number,
    count: number,
  ) {
    const centers = destination.centers;
    for (let record = 0; record < count; record += 1) {
      const source = sourceOffset + record;
      const target = (destinationOffset + record) * 3;
      centers[target] = columns.position[0][source];
      centers[target + 1] = columns.position[1][source];
      centers[target + 2] = columns.position[2][source];
    }
  }

  #copyMergedArenaNode(
    source: PcResource,
    columns: GsTilePlayCanvasColumns,
    sourceOffset: number,
    destination: PcArenaResource,
    slot: MergedArenaSlot,
  ) {
    let sourceCursor = sourceOffset;
    for (const span of slot.spans) {
      this.#copyGsplatResourceRange(
        source,
        destination,
        sourceCursor,
        span.offset,
        span.count,
      );
      this.#copyMergedArenaCenters(
        columns,
        sourceCursor,
        destination,
        span.offset,
        span.count,
      );
      sourceCursor += span.count;
    }
    if (sourceCursor !== sourceOffset + slot.count) {
      throw new Error("GSTile merged arena slot span count does not match");
    }
  }

  #setMergedArenaBounds(resource: PcResource, bounds: MergedArenaBounds) {
    resource.aabb.center.set(
      (bounds.min[0] + bounds.max[0]) * 0.5,
      (bounds.min[1] + bounds.max[1]) * 0.5,
      (bounds.min[2] + bounds.max[2]) * 0.5,
    );
    resource.aabb.halfExtents.set(
      (bounds.max[0] - bounds.min[0]) * 0.5,
      (bounds.max[1] - bounds.min[1]) * 0.5,
      (bounds.max[2] - bounds.min[2]) * 0.5,
    );
    resource.mesh?.aabb.copy(resource.aabb);
  }

  #setMergedArenaIntervals(
    entity: PcEntity,
    slots: ReadonlyMap<string, MergedArenaSlot>,
  ) {
    if (!entity.gsplat) {
      throw new Error("PlayCanvas GSplat component is unavailable");
    }
    entity.gsplat.setActiveSplatIntervals(
      mergedArenaActiveSpans(slots).map((span) => ({
        start: span.offset,
        count: span.count,
      })),
    );
  }

  #mergedArenaSlotsAreContiguous(
    slots: ReadonlyMap<string, MergedArenaSlot>,
    usedSplats: number,
  ) {
    const spans = mergedArenaActiveSpans(slots);
    return (
      spans.length === 1 &&
      spans[0].offset === 0 &&
      spans[0].count === usedSplats
    );
  }

  #submitGpuCopies(app: PcApplication) {
    (app.graphicsDevice as unknown as { submit?: () => void }).submit?.();
  }

  #registerTile(nodeId: string, loaded: LoadedTile) {
    if (this.#loadedTiles.has(nodeId)) {
      throw new Error(`GSTile node ${nodeId} is already resident`);
    }
    this.#loadedTiles.set(nodeId, loaded);
    this.#resources.push(loaded.resource);
    this.#entities.push(loaded.entity);
    this.#residentGaussians += loaded.gaussianCount;
    this.#residentBytes += loaded.byteLength;
    this.#selectedNodes = this.#loadedTiles.size;
  }

  #destroyUnregisteredTile(loaded: LoadedTile) {
    loaded.entity.destroy();
    loaded.resource.destroy();
  }

  #removeTile(nodeId: string) {
    const loaded = this.#loadedTiles.get(nodeId);
    if (!loaded) return;
    this.#loadedTiles.delete(nodeId);
    this.#entities = this.#entities.filter(
      (entity) => entity !== loaded.entity,
    );
    this.#resources = this.#resources.filter(
      (resource) => resource !== loaded.resource,
    );
    this.#residentGaussians -= loaded.gaussianCount;
    this.#residentBytes -= loaded.byteLength;
    this.#selectedNodes = this.#loadedTiles.size;
    loaded.entity.destroy();
    loaded.resource.destroy();
  }

  #resetUnifiedGsplatWorld(pc: Pc, app: PcApplication) {
    const director = (
      app.renderer as unknown as {
        gsplatDirector?: ResettableGsplatDirector | null;
      }
    ).gsplatDirector;
    const destroyed = resetPlayCanvasGsplatManagers(director);
    const layer = app.scene.layers.getLayerById(
      pc.LAYERID_WORLD,
    ) as unknown as { gsplatPlacementsDirty: boolean } | null;
    if (layer) layer.gsplatPlacementsDirty = true;
    return destroyed;
  }

  #lodSelection(maximumResidentGaussians = this.#maximumResidentGaussians) {
    const manifest = this.#lodManifest;
    const camera = this.#camera;
    const canvas = this.#canvas;
    if (!manifest || !camera?.camera || !canvas) return null;
    const position = camera.getPosition();
    const forward = camera.forward;
    const up = camera.up;
    const origin = manifest.coordinateFrame.origin;
    return selectGsTileLod(manifest, {
      cameraPosition: [
        position.x + origin[0],
        position.y + origin[1],
        position.z + origin[2],
      ],
      cameraDirection: [forward.x, forward.y, forward.z],
      cameraUp: [up.x, up.y, up.z],
      verticalFovRadians: (camera.camera.fov * Math.PI) / 180,
      viewportWidth: Math.max(canvas.width, 1),
      viewportHeight: Math.max(canvas.height, 1),
      maximumResidentGaussians,
      maximumProjectedErrorPixels: this.#maximumProjectedErrorPixels,
      includeSiblingLeaves: this.#includeSiblingLeaves,
      retainOffscreenCoverage: this.#retainOffscreenCoverage,
    });
  }

  async #fetchVerifiedLodTile(
    node: GsTileNode,
    signal: AbortSignal,
    timings: LodLoadTimings,
  ) {
    const pc = this.#pc;
    const app = this.#app;
    const manifest = this.#lodManifest;
    const scheduler = this.#lodScheduler;
    if (!pc || !app || !manifest || !scheduler) {
      throw new Error("PlayCanvas GSTile LOD backend is not initialized");
    }
    const tile = node.tile ?? node.lodTile;
    if (!tile) throw new Error(`GSTile node ${node.id} has no representation`);
    const pack = manifest.packs.find((candidate) => candidate.id === tile.pack);
    if (!pack)
      throw new Error(`GSTile node ${node.id} references a missing pack`);
    const url =
      this.#lodPackUrls?.get(pack.id) ??
      resolveGsTilePackUrl(this.#lodManifestUrl, pack.path);
    const range = { start: 0, length: pack.byteLength };
    const immutableIdentity = `sha256:${pack.sha256.toLowerCase()}`;
    const fetchStarted = performance.now();
    const content = await scheduler.fetch(
      url,
      range,
      signal,
      immutableIdentity,
    );
    timings.fetchServiceMs += performance.now() - fetchStarted;
    if (!this.#verifiedPackBuffers.has(content)) {
      const sha256Started = performance.now();
      const actualSha256 = await sha256(content);
      timings.sha256ServiceMs += performance.now() - sha256Started;
      if (
        actualSha256 !== pack.sha256.toLowerCase() ||
        actualSha256 !== tile.sha256.toLowerCase()
      ) {
        throw new Error(`GSTile pack ${pack.id} failed SHA-256 validation`);
      }
      scheduler.persistVerified(immutableIdentity, range, content);
      this.#verifiedPackBuffers.add(content);
    }
    signal.throwIfAborted();
    return { pc, app, manifest, tile, pack, content };
  }

  async #yieldBeforeLodDecode(signal: AbortSignal) {
    // Cached packs can all resolve in one microtask checkpoint. Yield one task
    // before each synchronous Q96 decode so camera input and rendering remain
    // responsive and an obsolete cut can be aborted between packs.
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    signal.throwIfAborted();
  }

  #ensureDecodeWorkerPool() {
    if (
      this.#decodeWorkerPoolUnavailable ||
      this.#useFloat32Transforms ||
      typeof Worker === "undefined" ||
      typeof globalThis.Float16Array !== "function"
    ) {
      return null;
    }
    if (!this.#decodeWorkerPool) {
      try {
        this.#decodeWorkerPool = new GsTileDecodeWorkerPool();
      } catch {
        this.#decodeWorkerPoolUnavailable = true;
        return null;
      }
    }
    return this.#decodeWorkerPool;
  }

  async #loadLodTile(
    node: GsTileNode,
    signal: AbortSignal,
    timings: LodLoadTimings,
  ): Promise<PreparedTile> {
    const { pc, app, manifest, tile, pack, content } =
      await this.#fetchVerifiedLodTile(node, signal, timings);
    await this.#yieldBeforeLodDecode(signal);
    const decodeStarted = performance.now();
    const decoded = decodeSha256VerifiedGsTilePackTile(
      content,
      tile.byteOffset,
      tile.byteLength,
      tile.recordCount,
      tile.quantization,
    );
    timings.decodeCpuMs += performance.now() - decodeStarted;
    signal.throwIfAborted();
    return {
      pc,
      app,
      decoded,
      origin: manifest.coordinateFrame.origin,
      node,
      byteLength: pack.byteLength,
    };
  }

  async #loadLodTileIntoPlayCanvasColumns(
    node: GsTileNode,
    signal: AbortSignal,
    destination: GsTilePlayCanvasColumns,
    recordOffset: number,
    timings: LodLoadTimings,
    decodeWorkerPool: GsTileDecodeWorkerPool | null,
  ) {
    const { tile, pack, content } = await this.#fetchVerifiedLodTile(
      node,
      signal,
      timings,
    );
    await this.#yieldBeforeLodDecode(signal);
    const workerStarted =
      destination.transformStreams && decodeWorkerPool
        ? performance.now()
        : null;
    const nativeDecodePromise =
      destination.transformStreams && decodeWorkerPool
        ? decodeWorkerPool.decode(
            content,
            tile.byteOffset,
            tile.byteLength,
            tile.recordCount,
            tile.quantization,
            signal,
          )
        : null;
    if (!nativeDecodePromise) {
      const decodeStarted = performance.now();
      decodeSha256VerifiedGsTilePackTileIntoPlayCanvasColumns(
        content,
        tile.byteOffset,
        tile.byteLength,
        tile.recordCount,
        tile.quantization,
        destination,
        recordOffset,
      );
      timings.decodeCpuMs += performance.now() - decodeStarted;
      signal.throwIfAborted();
      return { byteLength: pack.byteLength, bounds: null };
    }
    let nativeResult;
    try {
      nativeResult = await nativeDecodePromise;
    } catch (error) {
      if (signal.aborted) throw error;
      timings.decodeWorkerFallbacks += 1;
      this.#decodeWorkerPool?.dispose(error);
      this.#decodeWorkerPool = null;
      this.#decodeWorkerPoolUnavailable = true;
      const fallbackStarted = performance.now();
      nativeResult = decodeGsTileNativePayload(
        content.slice(tile.byteOffset, tile.byteOffset + tile.byteLength),
        tile.recordCount,
        tile.quantization,
      );
      timings.decodeCpuMs += performance.now() - fallbackStarted;
    }
    if (workerStarted !== null) {
      timings.decodeWorkerServiceMs += performance.now() - workerStarted;
    }
    const copyStarted = performance.now();
    copyGsTileNativeResult(destination, recordOffset, nativeResult);
    timings.decodeCpuMs += performance.now() - copyStarted;
    const nativeBounds: MergedArenaBounds = {
      min: [...nativeResult.bounds.minimum],
      max: [...nativeResult.bounds.maximum],
    };
    signal.throwIfAborted();
    return { byteLength: pack.byteLength, bounds: nativeBounds };
  }

  async #synchronizeLod(
    signal: AbortSignal,
    maximumResidentGaussians = this.#maximumResidentGaussians,
  ) {
    const manifest = this.#lodManifest;
    const selection = this.#lodSelection(maximumResidentGaussians);
    if (!manifest || !selection) return;
    const key = gstileLodSelectionKey(selection.selectedNodeIds);
    this.#targetGaussians = selection.residentGaussians;
    this.#targetNodes = selection.selectedNodeIds.length;
    this.#maximumSelectedErrorPixels = Math.max(
      selection.maximumSelectedErrorPixels,
      selection.unresolvedMaximumErrorPixels,
    );
    this.#effectiveMaximumErrorPixels = selection.effectiveMaximumErrorPixels;
    this.#selectedExactNodes = selection.selectedExactNodes;
    this.#selectedProxyNodes = selection.selectedProxyNodes;
    this.#selectedFullDepthNodes = selection.selectedFullDepthNodes;
    this.#selectedShallowLeafNodes = selection.selectedShallowLeafNodes;
    this.#selectedInternalNodes = selection.selectedInternalNodes;
    this.#selectedLeafDepthCounts = selection.selectedLeafDepthCounts;
    this.#maximumSelectedProxyScreenRadiusPixels =
      selection.maximumSelectedProxyScreenRadiusPixels;
    if (key === this.#lodSelectionKey) {
      this.#pendingNodes = 0;
      this.#lodState = selection.budgetLimited ? "budget-limited" : "steady";
      return;
    }
    if (key === this.#lodPendingKey) return;

    this.#lodSyncController?.abort(
      new DOMException("Superseded GSTile LOD selection", "AbortError"),
    );
    const controller = new AbortController();
    const generation = ++this.#lodGeneration;
    this.#lodSyncController = controller;
    this.#lodPendingKey = key;
    const lodStarted = performance.now();
    this.#pendingNodes = selection.selectedNodeIds.filter((nodeId) =>
      this.#gpuAssembly === "merged"
        ? !this.#mergedArena?.slots.has(nodeId)
        : !this.#loadedTiles.has(nodeId),
    ).length;
    this.#lodState = "refining";
    const abort = () => controller.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    const desired = new Set(selection.selectedNodeIds);
    const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
    const staged = new Map<string, PreparedTile>();
    const mergedAssembly = this.#gpuAssembly === "merged";
    const decodeWorkerPool = mergedAssembly
      ? this.#ensureDecodeWorkerPool()
      : null;
    const mergedOffsets = new Map<string, number>();
    const selectedArenaNodes = mergedAssembly
      ? selection.selectedNodeIds.map((nodeId) => {
          const node = nodes.get(nodeId);
          const tile = node?.tile ?? node?.lodTile;
          if (!node || !tile) {
            throw new Error(`GSTile merged target node ${nodeId} is missing`);
          }
          return { id: nodeId, count: tile.recordCount };
        })
      : [];
    const mergedPlan = mergedAssembly
      ? planMergedArenaSlots(
          this.#maximumResidentGaussians,
          this.#mergedArena?.slots ?? new Map(),
          selectedArenaNodes,
        )
      : null;
    const rebuildMergedArena = mergedAssembly && !this.#mergedArena;
    const mergedLoadNodeIds = mergedAssembly
      ? rebuildMergedArena
        ? selection.selectedNodeIds
        : (mergedPlan?.addedNodeIds ?? [])
      : [];
    let mergedGaussianCount = 0;
    if (mergedAssembly && mergedPlan) {
      for (const nodeId of mergedLoadNodeIds) {
        const node = nodes.get(nodeId);
        const tile = node?.tile ?? node?.lodTile;
        if (!node || !tile) {
          throw new Error(`GSTile merged target node ${nodeId} is missing`);
        }
        mergedOffsets.set(nodeId, mergedGaussianCount);
        mergedGaussianCount += tile.recordCount;
      }
    }
    let mergedColumns =
      mergedAssembly && mergedGaussianCount > 0
        ? allocateGsTilePlayCanvasColumns(
            rebuildMergedArena
              ? this.#maximumResidentGaussians
              : mergedGaussianCount,
            {
              color: true,
              centerBounds: true,
              sh: true,
              transform: decodeWorkerPool !== null,
            },
          )
        : null;
    let mergedStaging: LoadedTile | null = null;
    let mergedByteLength = 0;
    const mergedNodeByteLengths = new Map<string, number>();
    const mergedNodeBounds = new Map<string, MergedArenaBounds>();
    const loadTimings: LodLoadTimings = {
      fetchServiceMs: 0,
      sha256ServiceMs: 0,
      decodeCpuMs: 0,
      decodeWorkerServiceMs: 0,
      decodeWorkerFallbacks: 0,
    };
    const transitions = planLodTransitions(
      manifest,
      this.#loadedTiles.keys(),
      selection.selectedNodeIds,
    );
    const loadNodeIds = mergedAssembly
      ? mergedLoadNodeIds
      : prioritizeLodLoads(
          transitions,
          selection.selectedNodeIds,
          this.#loadedTiles.keys(),
        );
    try {
      signal.throwIfAborted();
      const loadStarted = performance.now();
      const loadResults = await Promise.allSettled(
        loadNodeIds.map(async (nodeId) => {
          if (!mergedAssembly && this.#loadedTiles.has(nodeId)) return;
          const node = nodes.get(nodeId);
          if (!node) throw new Error(`GSTile LOD node ${nodeId} is missing`);
          try {
            const recordOffset = mergedOffsets.get(nodeId);
            if (mergedColumns && recordOffset === undefined) {
              throw new Error(`GSTile merged offset ${nodeId} is missing`);
            }
            if (mergedColumns) {
              const loaded = await this.#loadLodTileIntoPlayCanvasColumns(
                node,
                controller.signal,
                mergedColumns,
                recordOffset ?? 0,
                loadTimings,
                decodeWorkerPool,
              );
              mergedNodeByteLengths.set(nodeId, loaded.byteLength);
              if (loaded.bounds) mergedNodeBounds.set(nodeId, loaded.bounds);
              mergedByteLength += loaded.byteLength;
            } else {
              const loaded = await this.#loadLodTile(
                node,
                controller.signal,
                loadTimings,
              );
              staged.set(nodeId, loaded);
            }
          } finally {
            if (generation === this.#lodGeneration) {
              this.#pendingNodes = Math.max(0, this.#pendingNodes - 1);
            }
          }
        }),
      );
      const failedLoad = loadResults.find(
        (result): result is PromiseRejectedResult =>
          result.status === "rejected",
      );
      if (failedLoad) {
        controller.abort(failedLoad.reason);
        throw failedLoad.reason;
      }
      this.#lodLoadMs = performance.now() - loadStarted;
      this.#lodFetchServiceMs = loadTimings.fetchServiceMs;
      this.#lodSha256ServiceMs = loadTimings.sha256ServiceMs;
      this.#lodDecodeCpuMs = loadTimings.decodeCpuMs;
      this.#lodDecodeWorkerServiceMs = decodeWorkerPool
        ? loadTimings.decodeWorkerServiceMs
        : null;
      this.#lodDecodeWorkerFallbacks = decodeWorkerPool
        ? loadTimings.decodeWorkerFallbacks
        : null;
      controller.signal.throwIfAborted();
      if (generation !== this.#lodGeneration) {
        throw new DOMException("Stale GSTile LOD selection", "AbortError");
      }

      const residentSelectedIds = mergedAssembly
        ? rebuildMergedArena
          ? []
          : (mergedPlan?.reusedNodeIds ?? [])
        : selection.selectedNodeIds.filter((nodeId) =>
            this.#loadedTiles.has(nodeId),
          );
      const removedNodeIds = mergedAssembly
        ? (mergedPlan?.removedNodeIds ?? [])
        : [...this.#loadedTiles.keys()].filter(
            (nodeId) => !desired.has(nodeId),
          );
      this.#lodAddedGaussians = mergedColumns
        ? mergedGaussianCount
        : [...staged.values()].reduce(
            (total, prepared) => total + prepared.decoded.count,
            0,
          );
      this.#lodRemovedGaussians = removedNodeIds.reduce(
        (total, nodeId) =>
          total +
          (mergedAssembly
            ? (this.#mergedArena?.slots.get(nodeId)?.count ?? 0)
            : (this.#loadedTiles.get(nodeId)?.gaussianCount ?? 0)),
        0,
      );
      this.#lodReusedGaussians = residentSelectedIds.reduce(
        (total, nodeId) =>
          total +
          (mergedAssembly
            ? (this.#mergedArena?.slots.get(nodeId)?.count ?? 0)
            : (this.#loadedTiles.get(nodeId)?.gaussianCount ?? 0)),
        0,
      );
      const commitStarted = performance.now();
      let resourceCreateMs = 0;
      let resourceColorMs = 0;
      let resourceTransformMs = 0;
      let resourceShMs = 0;
      let streamUploadMs = 0;
      let sceneAttachMs = 0;

      if (mergedAssembly) {
        const columnarCut = mergedColumns;
        const pc = this.#pc;
        const app = this.#app;
        if (!mergedPlan || !pc || !app) {
          throw new Error("GSTile merged target is empty");
        }
        const nextBounds = rebuildMergedArena
          ? new Map<string, MergedArenaBounds>()
          : new Map(this.#mergedArena?.bounds);
        const nextByteLengths = rebuildMergedArena
          ? new Map<string, number>()
          : new Map(this.#mergedArena?.byteLengths);
        for (const nodeId of mergedPlan.removedNodeIds) {
          nextBounds.delete(nodeId);
          nextByteLengths.delete(nodeId);
        }
        for (const nodeId of mergedLoadNodeIds) {
          const node = nodes.get(nodeId);
          const tile = node?.tile ?? node?.lodTile;
          const offset = mergedOffsets.get(nodeId);
          if (!columnarCut || !tile || offset === undefined) {
            throw new Error(`GSTile merged staging node ${nodeId} is missing`);
          }
          nextBounds.set(
            nodeId,
            mergedNodeBounds.get(nodeId) ??
              calculateMergedArenaBounds(
                columnarCut.position,
                columnarCut.logScale,
                offset,
                tile.recordCount,
                columnarCut.centerStream,
              ),
          );
          nextByteLengths.set(nodeId, mergedNodeByteLengths.get(nodeId) ?? 0);
        }
        const mergedBounds = mergeMergedArenaBounds(
          selection.selectedNodeIds.map((nodeId) => {
            const bounds = nextBounds.get(nodeId);
            if (!bounds) {
              throw new Error(`GSTile merged AABB ${nodeId} is missing`);
            }
            return bounds;
          }),
        );
        if (columnarCut?.centerStream) {
          columnarCut.bounds.minimum = [...mergedBounds.min];
          columnarCut.bounds.maximum = [...mergedBounds.max];
          columnarCut.bounds.valid = true;
        }
        const mergedNode: GsTileNode = {
          id: "rmerged",
          bounds: mergedBounds,
          renderBounds: mergedBounds,
          gaussianCount: mergedPlan.usedSplats,
        };
        if (columnarCut) {
          mergedStaging = this.#createTileResource(
            pc,
            app,
            columnarCut,
            manifest.coordinateFrame.origin,
            mergedNode,
            false,
            mergedByteLength,
            false,
            rebuildMergedArena ? mergedPlan.usedSplats : undefined,
          );
          resourceCreateMs += mergedStaging.resourceCreateMs;
          resourceColorMs += mergedStaging.resourceColorMs;
          resourceTransformMs += mergedStaging.resourceTransformMs;
          resourceShMs += mergedStaging.resourceShMs;
          streamUploadMs += mergedStaging.streamUploadMs;
          sceneAttachMs += mergedStaging.sceneAttachMs;
        }

        if (rebuildMergedArena) {
          if (!mergedStaging || !columnarCut) {
            throw new Error("GSTile merged arena staging resource is missing");
          }
          if (this.#mergedArena) {
            this.#mergedArena.loaded.entity.enabled = false;
          }
          this.#removeTile("__merged__");
          this.#mergedArena = null;
          this.#resetUnifiedGsplatWorld(pc, app);
          const container = mergedStaging.arenaResource;
          if (!container) {
            throw new Error("GSTile promoted arena resource is missing");
          }
          const contiguousArena = this.#mergedArenaSlotsAreContiguous(
            mergedPlan.slots,
            mergedPlan.usedSplats,
          );
          container.update(
            contiguousArena ? mergedPlan.usedSplats : container.maxSplats,
            true,
          );
          this.#setMergedArenaBounds(container, mergedBounds);
          const attachStarted = performance.now();
          const entity = mergedStaging.entity;
          // PlayCanvas creates the unified placement when the entity becomes
          // active. Enable synchronously before configuring its source ranges;
          // no frame can be presented in the middle of this commit.
          entity.enabled = true;
          if (!contiguousArena) {
            this.#setMergedArenaIntervals(entity, mergedPlan.slots);
          }
          sceneAttachMs += performance.now() - attachStarted;
          const byteLength = [...nextByteLengths.values()].reduce(
            (total, value) => total + value,
            0,
          );
          const loaded: LoadedTile = {
            entity,
            resource: container,
            gaussianCount: mergedPlan.usedSplats,
            byteLength,
            resourceCreateMs: 0,
            resourceColorMs: 0,
            resourceTransformMs: 0,
            resourceShMs: 0,
            streamUploadMs: 0,
            sceneAttachMs: 0,
          };
          this.#registerTile("__merged__", loaded);
          this.#mergedArena = {
            loaded,
            container,
            slots: mergedPlan.slots,
            bounds: nextBounds,
            byteLengths: nextByteLengths,
          };
          this.#releaseMergedArenaCpuSources = true;
          mergedStaging = null;
        } else {
          const arena = this.#mergedArena;
          if (!arena) throw new Error("GSTile merged arena is unavailable");
          if (mergedStaging && columnarCut) {
            for (const nodeId of mergedPlan.addedNodeIds) {
              const sourceOffset = mergedOffsets.get(nodeId);
              const slot = mergedPlan.slots.get(nodeId);
              if (sourceOffset === undefined || !slot) {
                throw new Error(
                  `GSTile merged arena slot ${nodeId} is missing`,
                );
              }
              this.#copyMergedArenaNode(
                mergedStaging.resource,
                columnarCut,
                sourceOffset,
                arena.container,
                slot,
              );
            }
          }
          const previousGaussians = arena.loaded.gaussianCount;
          const previousBytes = arena.loaded.byteLength;
          const byteLength = [...nextByteLengths.values()].reduce(
            (total, value) => total + value,
            0,
          );
          arena.container.update(arena.container.maxSplats, true);
          this.#setMergedArenaBounds(arena.container, mergedBounds);
          this.#setMergedArenaIntervals(arena.loaded.entity, mergedPlan.slots);
          // PlayCanvas can clear the global streaming dirty flag before the
          // layer reconciliation observes changed non-octree intervals. Drop
          // only the derived unified manager so the next render rebuilds its
          // allocation, sorter and work-buffer from the persistent arena.
          this.#resetUnifiedGsplatWorld(pc, app);
          this.#submitGpuCopies(app);
          if (mergedStaging) {
            this.#retiredMergedStaging.push(mergedStaging);
            mergedStaging = null;
          }
          arena.loaded.gaussianCount = mergedPlan.usedSplats;
          arena.loaded.byteLength = byteLength;
          arena.slots = mergedPlan.slots;
          arena.bounds = nextBounds;
          arena.byteLengths = nextByteLengths;
          this.#residentGaussians += mergedPlan.usedSplats - previousGaussians;
          this.#residentBytes += byteLength - previousBytes;
        }
        mergedColumns = null;
        this.#selectedNodes = selection.selectedNodeIds.length;
        staged.clear();
      } else {
        // A GSTile proxy has no independently blendable seam skirt. Committing
        // ready branches progressively therefore creates a visible checkerboard
        // of coarse and exact Gaussian representations. Keep the previous
        // complete cut on the GPU while the next cut is decoded in CPU memory,
        // then replace the complete cut between two rendered frames.
        const finalPlan = completeLodTargetPlan(
          this.#loadedTiles.keys(),
          desired,
          (nodeId) => this.#loadedTiles.get(nodeId)?.gaussianCount,
          (nodeId) => staged.get(nodeId)?.decoded.count,
        );
        if (!finalPlan.complete) {
          throw new Error("GSTile complete LOD target is not ready");
        }
        if (finalPlan.gaussianCount > this.#maximumResidentGaussians) {
          throw new Error(
            "GSTile complete LOD target exceeds the resident budget",
          );
        }
        for (const nodeId of finalPlan.removeNodeIds) {
          const loaded = this.#loadedTiles.get(nodeId);
          if (loaded) loaded.entity.enabled = false;
        }
        for (const nodeId of finalPlan.removeNodeIds) this.#removeTile(nodeId);
        for (const nodeId of finalPlan.addNodeIds) {
          const prepared = staged.get(nodeId);
          if (!prepared) {
            throw new Error(`GSTile prepared target node ${nodeId} is missing`);
          }
          const loaded = this.#createTileResource(
            prepared.pc,
            prepared.app,
            prepared.decoded,
            prepared.origin,
            prepared.node,
            false,
            prepared.byteLength,
            this.#gpuAssembly !== "incremental",
          );
          resourceCreateMs += loaded.resourceCreateMs;
          resourceColorMs += loaded.resourceColorMs;
          resourceTransformMs += loaded.resourceTransformMs;
          resourceShMs += loaded.resourceShMs;
          streamUploadMs += loaded.streamUploadMs;
          sceneAttachMs += loaded.sceneAttachMs;
          loaded.entity.enabled = true;
          this.#registerTile(nodeId, loaded);
          staged.delete(nodeId);
        }
      }
      this.#lodResourceCreateMs = resourceCreateMs;
      this.#lodResourceColorMs = resourceColorMs;
      this.#lodResourceTransformMs = resourceTransformMs;
      this.#lodResourceShMs = resourceShMs;
      this.#lodStreamUploadMs = streamUploadMs;
      this.#lodSceneAttachMs = sceneAttachMs;
      this.#lodSelectionKey = "";
      this.#lodSelectionKey = key;
      this.#pendingNodes = 0;
      this.#lodState = selection.budgetLimited ? "budget-limited" : "steady";
      // Publish an arena cut in the same task that mutates its textures. This
      // keeps the old work-buffer from indexing new arena contents when rAF is
      // throttled and removes a full-frame transition lag in active tabs. One
      // forced rebuild is sufficient because the placement and interval setter
      // both mark the unified world dirty.
      this.#forceWorkBufferRewriteFrames = mergedAssembly ? 1 : 0;
      this.#requestRender(mergedAssembly ? 1 : 2);
      if (mergedAssembly) this.render(performance.now());
      this.#lodCommitMs = performance.now() - commitStarted;
      this.#lodTotalMs = performance.now() - lodStarted;
    } catch (error) {
      mergedColumns = null;
      if (mergedStaging) {
        this.#destroyUnregisteredTile(mergedStaging);
        mergedStaging = null;
      }
      staged.clear();
      if (!(error instanceof DOMException && error.name === "AbortError"))
        this.#lodState = "error";
      throw error;
    } finally {
      signal.removeEventListener("abort", abort);
      if (generation === this.#lodGeneration) {
        this.#lodPendingKey = "";
        this.#lodSyncController = null;
      }
    }
  }

  #scheduleLodUpdate() {
    const signal = this.#lodSignal;
    if (!this.#lodManifest || !signal || signal.aborted) return;
    if (this.#lodSyncController && !this.#lodSyncController.signal.aborted) {
      this.#lodSyncController.abort(
        new DOMException(
          "GSTile interaction superseded refinement",
          "AbortError",
        ),
      );
      this.#lodPendingKey = "";
      this.#pendingNodes = 0;
      if (this.#loadedTiles.size > 0) this.#lodState = "steady";
    }
    if (this.#lodUpdateTimer !== null) clearTimeout(this.#lodUpdateTimer);
    this.#lodUpdateTimer = setTimeout(() => {
      this.#lodUpdateTimer = null;
      this.#requestRender();
      void this.#synchronizeLod(signal).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError")
          return;
        console.error(
          "GSTile LOD update failed; keeping the previous complete representation",
          error,
        );
      });
    }, this.#lodUpdateDelayMilliseconds);
  }

  #debugSnapshot() {
    type DebugBlock = { offset: number; size: number };
    type DebugSplat = {
      resource: { id: number; numSplats: number };
      placementId: number;
      allocId: number;
      activeSplats: number;
      intervalAllocIds: number[];
    };
    type DebugWorld = {
      currentVersion: number;
      lastWorldStateVersion: number;
      getState: (version: number) =>
        | {
            version: number;
            sortedBefore: boolean;
            fullRebuild: boolean;
            totalActiveSplats: number;
            textureSize: number;
            splats: DebugSplat[];
          }
        | undefined;
      _allocationMap: Map<number, DebugBlock>;
      _allocator: {
        capacity: number;
        usedSize: number;
        freeSize: number;
        fragmentation: number;
      };
    };
    type DebugManager = { world: DebugWorld };
    type DebugLayerData = { gsplatManager?: DebugManager | null };
    type DebugCameraData = { layersMap: Map<unknown, DebugLayerData> };
    type DebugDirector = { camerasMap: Map<unknown, DebugCameraData> };
    type DebugPlacement = {
      id: number;
      allocId: number;
      dirtyVersion: number;
      resource: PcResource | null;
    };

    const app = this.#app;
    const pc = this.#pc;
    const manifest = this.#lodManifest;
    const selectedNodeIds = this.#lodSelectionKey
      ? this.#lodSelectionKey.split("\0")
      : [];
    const nodeById = new Map(manifest?.nodes.map((node) => [node.id, node]));
    const layer =
      app && pc ? app.scene.layers.getLayerById(pc.LAYERID_WORLD) : null;
    const director = (
      app?.renderer as unknown as { gsplatDirector?: DebugDirector | null }
    )?.gsplatDirector;
    const worlds: Array<{
      world: DebugWorld;
      state: NonNullable<ReturnType<DebugWorld["getState"]>>;
    }> = [];
    for (const cameraData of director?.camerasMap.values() ?? []) {
      for (const layerData of cameraData.layersMap.values()) {
        const world = layerData.gsplatManager?.world;
        const state = world?.getState(world.currentVersion);
        if (world && state) worlds.push({ world, state });
      }
    }
    const splatsByResource = new Map<number, DebugSplat[]>();
    for (const { state } of worlds) {
      for (const splat of state.splats) {
        const values = splatsByResource.get(splat.resource.id) ?? [];
        values.push(splat);
        splatsByResource.set(splat.resource.id, values);
      }
    }
    const allocationFor = (allocId: number) => {
      for (const { world } of worlds) {
        const block = world._allocationMap.get(allocId);
        if (block) return { offset: block.offset, size: block.size };
      }
      return null;
    };
    const loaded = [...this.#loadedTiles.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([nodeId, value]) => {
        const component = value.entity.gsplat;
        const placement = (
          component as unknown as { _placement?: DebugPlacement | null }
        )?._placement;
        const worldSplats = splatsByResource.get(value.resource.id) ?? [];
        const allocIds = [
          ...new Set(
            worldSplats.flatMap((splat) =>
              splat.intervalAllocIds.length > 0
                ? splat.intervalAllocIds
                : [splat.allocId],
            ),
          ),
        ];
        const node = nodeById.get(nodeId);
        const representation = node?.tile ?? node?.lodTile;
        return {
          nodeId,
          depth: nodeId.length - 1,
          terminal: !node?.children?.length,
          expectedCount: representation?.recordCount ?? null,
          expectedPack: representation?.pack ?? null,
          expectedSha256: representation?.sha256 ?? null,
          residentCount: value.gaussianCount,
          entityName: value.entity.name,
          entityEnabled: value.entity.enabled,
          resourceId: value.resource.id,
          resourceCount: value.resource.numSplats,
          resourceTextureSize: [
            value.resource.textureDimensions.x,
            value.resource.textureDimensions.y,
          ],
          resourceStreams: [...value.resource.streams.textures.keys()],
          componentResourceId: component?.resource?.id ?? null,
          placementId: placement?.id ?? null,
          placementAllocId: placement?.allocId ?? null,
          placementResourceId: placement?.resource?.id ?? null,
          placementDirtyVersion: placement?.dirtyVersion ?? null,
          presentInWorldLayer: placement
            ? (layer?.gsplatPlacementsSet.has(placement as never) ?? false)
            : false,
          worldSplatCount: worldSplats.length,
          worldActiveSplats: worldSplats.reduce(
            (total, splat) => total + splat.activeSplats,
            0,
          ),
          allocations: allocIds.map((allocId) => ({
            allocId,
            block: allocationFor(allocId),
          })),
        };
      });
    const loadedIds = new Set(loaded.map((entry) => entry.nodeId));
    const selectedIds = new Set(selectedNodeIds);
    return {
      generatedAt: new Date().toISOString(),
      selection: {
        selectedNodeIds,
        selectedCount: selectedNodeIds.length,
        loadedCount: loaded.length,
        missingFromLoaded: selectedNodeIds.filter((id) => !loadedIds.has(id)),
        extraInLoaded: loaded
          .map((entry) => entry.nodeId)
          .filter((id) => !selectedIds.has(id)),
      },
      resident: {
        gaussianCount: this.#residentGaussians,
        resourceCount: this.#resources.length,
        entityCount: this.#entities.length,
        layerPlacementCount: layer?.gsplatPlacements.length ?? null,
      },
      performance: {
        lodTotalMs: this.#lodTotalMs,
        lodLoadMs: this.#lodLoadMs,
        lodCommitMs: this.#lodCommitMs,
        lodFetchServiceMs: this.#lodFetchServiceMs,
        lodSha256ServiceMs: this.#lodSha256ServiceMs,
        lodDecodeCpuMs: this.#lodDecodeCpuMs,
        lodDecodeWorkerServiceMs: this.#lodDecodeWorkerServiceMs,
        lodDecodeWorkerFallbacks: this.#lodDecodeWorkerFallbacks,
        lodResourceCreateMs: this.#lodResourceCreateMs,
        lodResourceColorMs: this.#lodResourceColorMs,
        lodResourceTransformMs: this.#lodResourceTransformMs,
        lodResourceShMs: this.#lodResourceShMs,
        lodStreamUploadMs: this.#lodStreamUploadMs,
        lodSceneAttachMs: this.#lodSceneAttachMs,
        lodAddedGaussians: this.#lodAddedGaussians,
        lodRemovedGaussians: this.#lodRemovedGaussians,
        lodReusedGaussians: this.#lodReusedGaussians,
        frameGpuPasses: this.#lastRenderedFrameTelemetry.gpuPasses,
        rangeScheduler: this.#lodScheduler?.statistics() ?? null,
      },
      worlds: worlds.map(({ world, state }) => ({
        currentVersion: world.currentVersion,
        lastWorldStateVersion: world.lastWorldStateVersion,
        stateVersion: state.version,
        sortedBefore: state.sortedBefore,
        fullRebuild: state.fullRebuild,
        totalActiveSplats: state.totalActiveSplats,
        textureSize: state.textureSize,
        splatCount: state.splats.length,
        resourceIds: state.splats.map((splat) => splat.resource.id),
        allocationCount: world._allocationMap.size,
        allocatorCapacity: world._allocator.capacity,
        allocatorUsed: world._allocator.usedSize,
        allocatorFree: world._allocator.freeSize,
        allocatorFragmentation: world._allocator.fragmentation,
      })),
      loaded,
    };
  }

  #statistics(
    currentFrameCpuMs: number | null,
    rendered = true,
  ): GaussianRenderStatistics {
    const gpuTimings = this.#app?.stats.gpu;
    const current: GsTileFrameTelemetry = {
      frameCpuMs: currentFrameCpuMs,
      frameGpuMs:
        rendered && this.#debugTraceEnabled && gpuTimings
        ? [...gpuTimings.values()].reduce((total, value) => total + value, 0)
        : null,
      workBufferUploadPercent: rendered && this.#app
        ? this.#app.stats.frame.gsplatBufferCopy
        : null,
      gpuPasses:
        rendered && this.#debugTraceEnabled
          ? captureGsTileGpuPassTelemetry(gpuTimings)
          : [],
    };
    this.#lastRenderedFrameTelemetry = retainGsTileFrameTelemetry(
      this.#lastRenderedFrameTelemetry,
      current,
      rendered,
    );
    const frameTelemetry = this.#lastRenderedFrameTelemetry;
    return {
      lodState: this.#lodState,
      residentGaussians: this.#residentGaussians,
      residentBytes: this.#residentBytes,
      selectedNodes: this.#selectedNodes,
      targetGaussians: this.#targetGaussians,
      targetNodes: this.#targetNodes,
      pendingNodes: this.#pendingNodes,
      maximumSelectedErrorPixels: this.#maximumSelectedErrorPixels,
      effectiveMaximumErrorPixels: this.#effectiveMaximumErrorPixels,
      selectedExactNodes: this.#selectedExactNodes,
      selectedProxyNodes: this.#selectedProxyNodes,
      selectedFullDepthNodes: this.#selectedFullDepthNodes,
      selectedShallowLeafNodes: this.#selectedShallowLeafNodes,
      selectedInternalNodes: this.#selectedInternalNodes,
      selectedLeafDepthCounts: this.#selectedLeafDepthCounts,
      maximumSelectedProxyScreenRadiusPixels:
        this.#maximumSelectedProxyScreenRadiusPixels,
      maximumResidentGaussians: this.#maximumResidentGaussians,
      verticalFovDegrees: this.#camera?.camera?.fov ?? null,
      frameCpuMs: frameTelemetry.frameCpuMs,
      frameGpuMs: frameTelemetry.frameGpuMs,
      workBufferUploadPercent: frameTelemetry.workBufferUploadPercent,
      lodTotalMs: this.#lodTotalMs,
      lodLoadMs: this.#lodLoadMs,
      lodCommitMs: this.#lodCommitMs,
      lodFetchServiceMs: this.#lodFetchServiceMs,
      lodSha256ServiceMs: this.#lodSha256ServiceMs,
      lodDecodeCpuMs: this.#lodDecodeCpuMs,
      lodDecodeWorkerServiceMs: this.#lodDecodeWorkerServiceMs,
      lodDecodeWorkerFallbacks: this.#lodDecodeWorkerFallbacks,
      lodResourceCreateMs: this.#lodResourceCreateMs,
      lodResourceColorMs: this.#lodResourceColorMs,
      lodResourceTransformMs: this.#lodResourceTransformMs,
      lodResourceShMs: this.#lodResourceShMs,
      lodStreamUploadMs: this.#lodStreamUploadMs,
      lodSceneAttachMs: this.#lodSceneAttachMs,
      lodAddedGaussians: this.#lodAddedGaussians,
      lodRemovedGaussians: this.#lodRemovedGaussians,
      lodReusedGaussians: this.#lodReusedGaussians,
    };
  }

  #requestRender(frames = 2) {
    this.#renderFramesRemaining = Math.max(this.#renderFramesRemaining, frames);
  }

  #updateDebugSnapshot(timestampMs: number) {
    if (
      !this.#debugSnapshotElement ||
      timestampMs - this.#lastDebugSnapshotTimestampMs < 1_000
    ) {
      return;
    }
    this.#debugSnapshotElement.textContent = JSON.stringify(
      this.#debugSnapshot(),
    );
    this.#lastDebugSnapshotTimestampMs = timestampMs;
  }

  #updateCameraPose() {
    const camera = this.#camera;
    if (!camera) return;
    const basis = orbitCameraBasis(this.#viewFrame, this.#yaw, this.#pitch);
    camera.setPosition(
      this.#target[0] + this.#distance * basis.offset[0],
      this.#target[1] + this.#distance * basis.offset[1],
      this.#target[2] + this.#distance * basis.offset[2],
    );
    camera.lookAt(
      this.#target[0],
      this.#target[1],
      this.#target[2],
      basis.up[0],
      basis.up[1],
      basis.up[2],
    );
    this.#cameraDirty = false;
    this.#updateOpacityCameraUniform();
    this.#scheduleLodUpdate();
    this.#requestRender();
  }

  #updateOpacityCameraUniform() {
    const camera = this.#camera;
    if (!camera) return;
    const position = camera.getPosition();
    const value = coordinateFrameCameraPosition(
      [position.x, position.y, position.z],
      this.#coordinateOrigin,
    );
    for (const entity of this.#entities) {
      entity.gsplat?.setParameter("uDroneCameraPosition", value);
    }
  }

  #installOrbitInput(canvas: HTMLCanvasElement) {
    const onPointerDown = (event: PointerEvent) => {
      if (this.#pointerId !== null) return;
      if (event.button !== 0 && event.button !== 1 && event.button !== 2)
        return;
      event.preventDefault();
      this.#pointerId = event.pointerId;
      this.#pointerMode =
        event.button === 1 || event.button === 2 || event.shiftKey
          ? "pan"
          : "orbit";
      this.#pointerX = event.clientX;
      this.#pointerY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
    };
    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerId !== this.#pointerId) return;
      const dx = event.clientX - this.#pointerX;
      const dy = event.clientY - this.#pointerY;
      this.#pointerX = event.clientX;
      this.#pointerY = event.clientY;
      if (this.#pointerMode === "pan") {
        this.#target = panOrbitTarget(
          this.#target,
          this.#yaw,
          this.#pitch,
          this.#distance,
          dx,
          dy,
          canvas.clientHeight,
          this.#camera?.camera?.fov ?? this.#initialVerticalFovDegrees,
          this.#viewFrame,
        );
      } else {
        this.#yaw -= dx * 0.005;
        this.#pitch = Math.max(
          -Math.PI * 0.49,
          Math.min(Math.PI * 0.49, this.#pitch + dy * 0.005),
        );
      }
      this.#cameraDirty = true;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (event.pointerId !== this.#pointerId) return;
      this.#pointerId = null;
      this.#pointerMode = null;
      canvas.releasePointerCapture(event.pointerId);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const camera = this.#camera?.camera;
      if (event.altKey && camera) {
        camera.fov = gstileVerticalFovDegrees(
          camera.fov + event.deltaY * 0.025,
        );
        this.#scheduleLodUpdate();
        this.#requestRender();
        return;
      }
      this.#distance = Math.max(
        0.01,
        this.#distance * Math.exp(event.deltaY * 0.001),
      );
      this.#cameraDirty = true;
    };
    const onContextMenu = (event: MouseEvent) => event.preventDefault();
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("contextmenu", onContextMenu);
    this.#removeInputListeners = () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerUp);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("contextmenu", onContextMenu);
    };
  }
}

export const createPlayCanvasResidentBackend = (
  options: PlayCanvasResidentBackendOptions = {},
) => new PlayCanvasResidentBackend(options);
