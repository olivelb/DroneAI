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
  decodeGsTilePackTile,
  gsTileOpacityStreams,
  gsTileToPlyProperties,
  type DecodedGsTile,
} from "./decode";
import {
  DRONEGS_OPACITY_MODIFIER_GLSL,
  DRONEGS_OPACITY_MODIFIER_WGSL,
} from "./playcanvas-opacity";
import type { GsTileRangeScheduler } from "./range-source";
import { selectGsTileLod } from "./lod-selection";

type Pc = typeof import("playcanvas");
type PcApplication = import("playcanvas").Application;
type PcEntity = import("playcanvas").Entity;
type PcResource = import("playcanvas").GSplatResource;
type LoadedTile = {
  entity: PcEntity;
  resource: PcResource;
  gaussianCount: number;
  byteLength: number;
};
type PreparedTile = {
  pc: Pc;
  app: PcApplication;
  decoded: DecodedGsTile;
  origin: Vec3;
  node: GsTileNode;
  byteLength: number;
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


export type PlayCanvasResidentBackendOptions = {
  /** Hard safety gate. Hierarchical LOD must be used beyond this resident baseline. */
  maximumResidentGaussians?: number;
  maximumProjectedErrorPixels?: number;
  background?: [number, number, number];
};

type GsplatQualitySettings = {
  antiAlias: boolean;
  alphaClip: number;
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
  settings.colorUpdateAngle = 0;
  settings.antiAlias = true;
  settings.minContribution = 0.05;
  settings.minPixelSize = 0.5;
  settings.alphaClip = 1 / 255;
  settings.radialSorting = true;
};

const DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS = 6_000_000;
const DEFAULT_MAXIMUM_PROJECTED_ERROR_PIXELS = 2;
const OPACITY_STREAM_NAMES = [
  "droneOpacity0",
  "droneOpacity1",
  "droneOpacity2",
  "droneOpacity3",
] as const;

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

export const lodProxyCoverage = (node: GsTileNode, inflateReplacementProxy = true) => {
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

export class PlayCanvasResidentBackend implements GaussianRenderBackend {
  readonly id = "playcanvas-webgpu-resident-exact-v1";

  readonly #maximumResidentGaussians: number;
  readonly #maximumProjectedErrorPixels: number;
  readonly #background: [number, number, number];
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
  #lodState: GaussianRenderStatistics["lodState"] = "steady";
  #lastTimestampMs: number | null = null;
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
  #lodManifest: GsTileManifest | null = null;
  #lodManifestUrl = "";
  #lodScheduler: GsTileRangeScheduler | null = null;
  #lodPackUrls: ReadonlyMap<string, string> | undefined;
  #lodSignal: AbortSignal | null = null;
  #lodSyncController: AbortController | null = null;
  #lodGeneration = 0;
  #lodSelectionKey = "";
  #lodPendingKey = "";
  #lodUpdateTimer: ReturnType<typeof setTimeout> | null = null;
  #viewportWidth = 1;
  #viewportHeight = 1;
  #lodUsesMomentMatchedProxies = false;
  #verifiedPackBuffers = new WeakSet<ArrayBuffer>();

  constructor(options: PlayCanvasResidentBackendOptions = {}) {
    this.#maximumResidentGaussians =
      options.maximumResidentGaussians ?? DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS;
    this.#maximumProjectedErrorPixels =
      options.maximumProjectedErrorPixels ??
      DEFAULT_MAXIMUM_PROJECTED_ERROR_PIXELS;
    this.#background = options.background ?? [0.035, 0.055, 0.05];
    if (
      !Number.isSafeInteger(this.#maximumResidentGaussians) ||
      this.#maximumResidentGaussians < 1
    ) {
      throw new Error("maximumResidentGaussians must be a positive integer");
    }
    if (
      !Number.isFinite(this.#maximumProjectedErrorPixels) ||
      this.#maximumProjectedErrorPixels <= 0
    ) {
      throw new Error("maximumProjectedErrorPixels must be positive");
    }
  }

  async initialize(canvas: HTMLCanvasElement) {
    if (this.#app) throw new Error("PlayCanvas GSTile backend is already initialized");
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
    configureHighQualityGsplatRendering(app.scene.gsplat, {
      dataFormat: pc.GSPLATDATA_LARGE,
      renderer: pc.GSPLAT_RENDERER_RASTER_GPU_SORT,
    });

    const camera = new pc.Entity("GSTile camera");
    camera.addComponent("camera", {
      clearColor: new pc.Color(
        this.#background[0],
        this.#background[1],
        this.#background[2],
      ),
      nearClip: 0.01,
      farClip: 1_000_000,
      fov: 55,
    });
    app.root.addChild(camera);

    this.#pc = pc;
    this.#app = app;
    this.#camera = camera;
    this.#canvas = canvas;
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
    if (!pc || !app) throw new Error("PlayCanvas GSTile backend is not initialized");
    const hasLod = isGsTileLodProfile(manifest.profile);
    if (!hasLod && manifest.source.gaussianCount > this.#maximumResidentGaussians) {
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
      this.#camera?.camera?.fov ?? 55,
      this.#viewportWidth / this.#viewportHeight,
    );
    this.#cameraDirty = true;
    this.#updateCameraPose();

    if (hasLod) {
      this.#lodUsesMomentMatchedProxies =
        manifest.profile === GSTILE_MOMENT_LOD_PROFILE ||
        manifest.profile === GSTILE_ADAPTIVE_LOD_PROFILE;
      this.#lodManifest = manifest;
      this.#lodManifestUrl = manifestUrl;
      this.#lodScheduler = scheduler;
      this.#lodPackUrls = packUrls;
      this.#lodSignal = signal;
      await this.#synchronizeLod(signal);
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
      const content = await scheduler.fetch(
        url,
        { start: 0, length: pack.byteLength },
        signal,
      );
      const actualSha256 = await sha256(content);
      if (actualSha256 !== pack.sha256.toLowerCase()) {
        throw new Error(`GSTile pack ${pack.id} failed SHA-256 validation`);
      }
      for (const node of nodes) {
        signal.throwIfAborted();
        if (node.tile.sha256.toLowerCase() !== actualSha256) {
          throw new Error(`GSTile node ${node.id} references an unexpected pack hash`);
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
      entity.camera.fov = (2 * Math.atan(1 / projectionY) * 180) / Math.PI;
    }
    this.#cameraDirty = false;
    this.#updateOpacityCameraUniform();
    this.#scheduleLodUpdate();
  }

  render(timestampMs: number): GaussianRenderStatistics {
    const app = this.#app;
    if (!app) return this.#statistics(null);
    const start = performance.now();
    const deltaSeconds = Math.min(
      Math.max(((this.#lastTimestampMs ?? timestampMs) - timestampMs) / -1000, 0),
      0.1,
    );
    this.#lastTimestampMs = timestampMs;
    if (this.#cameraDirty) this.#updateCameraPose();
    app.update(deltaSeconds);
    app.render();
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
  }

  dispose() {
    this.#lodGeneration += 1;
    this.#lodSyncController?.abort(
      new DOMException("GSTile backend disposed", "AbortError"),
    );
    this.#lodSyncController = null;
    if (this.#lodUpdateTimer !== null) clearTimeout(this.#lodUpdateTimer);
    this.#lodUpdateTimer = null;
    this.#removeInputListeners?.();
    this.#removeInputListeners = null;
    for (const entity of this.#entities) entity.destroy();
    for (const resource of this.#resources) resource.destroy();
    this.#entities = [];
    this.#resources = [];
    this.#loadedTiles.clear();
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
    this.#lodState = "steady";
    this.#lodUsesMomentMatchedProxies = false;
    this.#coordinateOrigin = [0, 0, 0];
    this.#verifiedPackBuffers = new WeakSet<ArrayBuffer>();
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
    tile: DecodedGsTile,
    origin: Vec3,
    node: GsTileNode,
    enabled: boolean,
    byteLength = 0,
  ): LoadedTile {
    const data = new pc.GSplatData([
      {
        name: "vertex",
        count: tile.count,
        properties: gsTileToPlyProperties(tile),
      },
    ]);
    const resource = new pc.GSplatResource(app.graphicsDevice, data);
    resource.format.addExtraStreams(
      OPACITY_STREAM_NAMES.map((name) => ({
        name,
        format: pc.PIXELFORMAT_RGBA32F,
      })),
    );
    const opacityStreams = gsTileOpacityStreams(tile);
    OPACITY_STREAM_NAMES.forEach((name, index) => {
      const texture = resource.getTexture(name);
      if (!texture) throw new Error(`PlayCanvas did not allocate ${name}`);
      const destination = texture.lock() as Float32Array;
      destination.set(opacityStreams[index]);
      texture.unlock();
    });

    const entity = new pc.Entity(`GSTile ${node.id}`);
    entity.enabled = enabled;
    entity.setPosition(-origin[0], -origin[1], -origin[2]);
    entity.addComponent("gsplat", { unified: true });
    if (!entity.gsplat) throw new Error("PlayCanvas GSplat component is unavailable");
    entity.gsplat.resource = resource;
    entity.gsplat.setWorkBufferModifier({
      glsl: DRONEGS_OPACITY_MODIFIER_GLSL,
      wgsl: DRONEGS_OPACITY_MODIFIER_WGSL,
    });
    const coverage = lodProxyCoverage(node, !this.#lodUsesMomentMatchedProxies);
    entity.gsplat.setParameter(
      "uDroneLodScaleMultiplier",
      coverage.multiplier,
    );
    entity.gsplat.setParameter("uDroneLodMaximumScale", coverage.maximumScale);
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
    releasePlyPropertyStorage(data);
    return {
      entity,
      resource,
      gaussianCount: tile.count,
      byteLength,
    };
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
    this.#entities = this.#entities.filter((entity) => entity !== loaded.entity);
    this.#resources = this.#resources.filter(
      (resource) => resource !== loaded.resource,
    );
    this.#residentGaussians -= loaded.gaussianCount;
    this.#residentBytes -= loaded.byteLength;
    this.#selectedNodes = this.#loadedTiles.size;
    loaded.entity.destroy();
    loaded.resource.destroy();
  }

  #lodSelection() {
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
      maximumResidentGaussians: this.#maximumResidentGaussians,
      maximumProjectedErrorPixels: this.#maximumProjectedErrorPixels,
    });
  }

  async #loadLodTile(
    node: GsTileNode,
    signal: AbortSignal,
  ): Promise<PreparedTile> {
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
    if (!pack) throw new Error(`GSTile node ${node.id} references a missing pack`);
    const url =
      this.#lodPackUrls?.get(pack.id) ??
      resolveGsTilePackUrl(this.#lodManifestUrl, pack.path);
    const content = await scheduler.fetch(
      url,
      { start: 0, length: pack.byteLength },
      signal,
    );
    if (!this.#verifiedPackBuffers.has(content)) {
      const actualSha256 = await sha256(content);
      if (
        actualSha256 !== pack.sha256.toLowerCase() ||
        actualSha256 !== tile.sha256.toLowerCase()
      ) {
        throw new Error(`GSTile pack ${pack.id} failed SHA-256 validation`);
      }
      this.#verifiedPackBuffers.add(content);
    }
    signal.throwIfAborted();
    const decoded = decodeGsTilePackTile(
      content,
      tile.byteOffset,
      tile.byteLength,
      tile.recordCount,
      tile.quantization,
    );
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

  async #synchronizeLod(signal: AbortSignal) {
    const manifest = this.#lodManifest;
    const selection = this.#lodSelection();
    if (!manifest || !selection) return;
    const key = selection.selectedNodeIds.join("\0");
    this.#targetGaussians = selection.residentGaussians;
    this.#targetNodes = selection.selectedNodeIds.length;
    this.#maximumSelectedErrorPixels = Math.max(
      selection.maximumSelectedErrorPixels,
      selection.unresolvedMaximumErrorPixels,
    );
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
    this.#pendingNodes = selection.selectedNodeIds.filter((nodeId) => !this.#loadedTiles.has(nodeId)).length;
    this.#lodState = "refining";
    const abort = () => controller.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    const desired = new Set(selection.selectedNodeIds);
    const nodes = new Map(manifest.nodes.map((node) => [node.id, node]));
    const staged = new Map<string, PreparedTile>();
    const transitions = planLodTransitions(
      manifest,
      this.#loadedTiles.keys(),
      selection.selectedNodeIds,
    );
    const loadNodeIds = prioritizeLodLoads(
      transitions,
      selection.selectedNodeIds,
      this.#loadedTiles.keys(),
    );
    try {
      signal.throwIfAborted();
      const loadResults = await Promise.allSettled(
        loadNodeIds.map(async (nodeId) => {
          if (this.#loadedTiles.has(nodeId)) return;
          const node = nodes.get(nodeId);
          if (!node) throw new Error(`GSTile LOD node ${nodeId} is missing`);
          try {
            const loaded = await this.#loadLodTile(node, controller.signal);
            staged.set(nodeId, loaded);
          } finally {
            if (generation === this.#lodGeneration) {
              this.#pendingNodes = Math.max(0, this.#pendingNodes - 1);
            }
          }
        }),
      );
      const failedLoad = loadResults.find(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (failedLoad) {
        controller.abort(failedLoad.reason);
        throw failedLoad.reason;
      }
      controller.signal.throwIfAborted();
      if (generation !== this.#lodGeneration) {
        throw new DOMException("Stale GSTile LOD selection", "AbortError");
      }

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
        throw new Error("GSTile complete LOD target exceeds the resident budget");
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
        );
        loaded.entity.enabled = true;
        this.#registerTile(nodeId, loaded);
        staged.delete(nodeId);
      }
      this.#lodSelectionKey = "";
      this.#updateOpacityCameraUniform();
      this.#lodSelectionKey = key;
      this.#pendingNodes = 0;
      this.#lodState = selection.budgetLimited ? "budget-limited" : "steady";
      this.#updateOpacityCameraUniform();
    } catch (error) {
      staged.clear();
      if (!(error instanceof DOMException && error.name === "AbortError")) this.#lodState = "error";
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
    if (this.#lodUpdateTimer !== null) clearTimeout(this.#lodUpdateTimer);
    this.#lodUpdateTimer = setTimeout(() => {
      this.#lodUpdateTimer = null;
      void this.#synchronizeLod(signal).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        console.error(
          "GSTile LOD update failed; keeping the previous complete representation",
          error,
        );
      });
    }, 250);
  }

  #statistics(frameCpuMs: number | null): GaussianRenderStatistics {
    return {
      lodState: this.#lodState,
      residentGaussians: this.#residentGaussians,
      residentBytes: this.#residentBytes,
      selectedNodes: this.#selectedNodes,
      targetGaussians: this.#targetGaussians,
      targetNodes: this.#targetNodes,
      pendingNodes: this.#pendingNodes,
      maximumSelectedErrorPixels: this.#maximumSelectedErrorPixels,
      frameCpuMs,
      frameGpuMs: null,
    };
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
      this.#target[0], this.#target[1], this.#target[2],
      basis.up[0], basis.up[1], basis.up[2],
    );
    this.#cameraDirty = false;
    this.#updateOpacityCameraUniform();
    this.#scheduleLodUpdate();
  }

  #updateOpacityCameraUniform() {
    const pc = this.#pc;
    const camera = this.#camera;
    if (!pc || !camera) return;
    const position = camera.getPosition();
    const value = coordinateFrameCameraPosition(
      [position.x, position.y, position.z],
      this.#coordinateOrigin,
    );
    for (const entity of this.#entities) {
      entity.gsplat?.setParameter("uDroneCameraPosition", value);
      if (entity.gsplat) {
        entity.gsplat.workBufferUpdate = pc.WORKBUFFER_UPDATE_ONCE;
      }
    }
  }

  #installOrbitInput(canvas: HTMLCanvasElement) {
    const onPointerDown = (event: PointerEvent) => {
      if (this.#pointerId !== null) return;
      if (event.button !== 0 && event.button !== 1 && event.button !== 2) return;
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
          this.#camera?.camera?.fov ?? 55,
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
      this.#distance = Math.max(0.01, this.#distance * Math.exp(event.deltaY * 0.001));
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
