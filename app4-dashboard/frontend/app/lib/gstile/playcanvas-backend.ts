import type {
  GaussianCameraState,
  GaussianRenderBackend,
  GaussianRenderStatistics,
} from "./backend";
import { GaussianBackendUnavailable } from "./backend";
import type { GsTileManifest, GsTileNode, Vec3 } from "./contracts";
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

type Pc = typeof import("playcanvas");
type PcApplication = import("playcanvas").Application;
type PcEntity = import("playcanvas").Entity;
type PcResource = import("playcanvas").GSplatResource;

export type PlayCanvasResidentBackendOptions = {
  /** Hard safety gate. Hierarchical LOD must be used beyond this resident baseline. */
  maximumResidentGaussians?: number;
  background?: [number, number, number];
};

const DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS = 2_000_000;
const OPACITY_STREAM_NAMES = [
  "droneOpacity0",
  "droneOpacity1",
  "droneOpacity2",
  "droneOpacity3",
] as const;

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

const diagonalOf = (minimum: Vec3, maximum: Vec3) =>
  Math.hypot(
    maximum[0] - minimum[0],
    maximum[1] - minimum[1],
    maximum[2] - minimum[2],
  );

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
  #lastTimestampMs: number | null = null;
  #target: Vec3 = [0, 0, 0];
  #yaw = 0;
  #pitch = 0;
  #distance = 1;
  #pointerId: number | null = null;
  #pointerX = 0;
  #pointerY = 0;
  #cameraDirty = true;
  #removeInputListeners: (() => void) | null = null;

  constructor(options: PlayCanvasResidentBackendOptions = {}) {
    this.#maximumResidentGaussians =
      options.maximumResidentGaussians ?? DEFAULT_MAXIMUM_RESIDENT_GAUSSIANS;
    this.#background = options.background ?? [0.035, 0.055, 0.05];
    if (
      !Number.isSafeInteger(this.#maximumResidentGaussians) ||
      this.#maximumResidentGaussians < 1
    ) {
      throw new Error("maximumResidentGaussians must be a positive integer");
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
    app.scene.gsplat.dataFormat = pc.GSPLATDATA_LARGE;
    app.scene.gsplat.renderer = pc.GSPLAT_RENDERER_RASTER_GPU_SORT;
    app.scene.gsplat.colorUpdateAngle = 0;

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
  ) {
    const pc = this.#pc;
    const app = this.#app;
    if (!pc || !app) throw new Error("PlayCanvas GSTile backend is not initialized");
    if (manifest.source.gaussianCount > this.#maximumResidentGaussians) {
      throw new GaussianBackendUnavailable(
        `Le profil résident est limité à ${this.#maximumResidentGaussians.toLocaleString()} splats; ` +
          `${manifest.source.gaussianCount.toLocaleString()} exigent le LOD hiérarchique.`,
      );
    }

    const root = manifest.nodes.find((node) => node.id === manifest.root);
    if (!root) throw new Error("GSTile root node is missing");
    const origin = manifest.coordinateFrame.origin;
    const target = centerOf(root.bounds.min, root.bounds.max);
    this.#target = [
      target[0] - origin[0],
      target[1] - origin[1],
      target[2] - origin[2],
    ];
    this.#distance = Math.max(diagonalOf(root.bounds.min, root.bounds.max), 1);
    this.#cameraDirty = true;
    this.#updateCameraPose();

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
        this.#addTileResource(pc, app, decoded, origin, node.id);
        this.#residentGaussians += decoded.count;
        this.#selectedNodes += 1;
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
    app.resizeCanvas(
      Math.max(1, Math.round(width * maximumRatio)),
      Math.max(1, Math.round(height * maximumRatio)),
    );
  }

  dispose() {
    this.#removeInputListeners?.();
    this.#removeInputListeners = null;
    for (const entity of this.#entities) entity.destroy();
    for (const resource of this.#resources) resource.destroy();
    this.#entities = [];
    this.#resources = [];
    this.#camera?.destroy();
    this.#camera = null;
    this.#app?.destroy();
    this.#app = null;
    this.#pc = null;
    this.#canvas = null;
  }

  #addTileResource(
    pc: Pc,
    app: PcApplication,
    tile: DecodedGsTile,
    origin: Vec3,
    nodeId: string,
  ) {
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

    const entity = new pc.Entity(`GSTile ${nodeId}`);
    entity.setPosition(-origin[0], -origin[1], -origin[2]);
    entity.addComponent("gsplat", { unified: true });
    if (!entity.gsplat) throw new Error("PlayCanvas GSplat component is unavailable");
    entity.gsplat.resource = resource;
    entity.gsplat.setWorkBufferModifier({
      glsl: DRONEGS_OPACITY_MODIFIER_GLSL,
      wgsl: DRONEGS_OPACITY_MODIFIER_WGSL,
    });
    app.root.addChild(entity);
    this.#resources.push(resource);
    this.#entities.push(entity);
    releasePlyPropertyStorage(data);
  }

  #statistics(frameCpuMs: number | null): GaussianRenderStatistics {
    return {
      residentGaussians: this.#residentGaussians,
      residentBytes: this.#residentBytes,
      selectedNodes: this.#selectedNodes,
      frameCpuMs,
      frameGpuMs: null,
    };
  }

  #updateCameraPose() {
    const camera = this.#camera;
    if (!camera) return;
    const cosPitch = Math.cos(this.#pitch);
    camera.setPosition(
      this.#target[0] + this.#distance * Math.sin(this.#yaw) * cosPitch,
      this.#target[1] + this.#distance * Math.sin(this.#pitch),
      this.#target[2] + this.#distance * Math.cos(this.#yaw) * cosPitch,
    );
    camera.lookAt(this.#target[0], this.#target[1], this.#target[2]);
    this.#cameraDirty = false;
    this.#updateOpacityCameraUniform();
  }

  #updateOpacityCameraUniform() {
    const camera = this.#camera;
    if (!camera) return;
    const position = camera.getPosition();
    const value = [position.x, position.y, position.z];
    for (const entity of this.#entities) {
      entity.gsplat?.setParameter("uDroneCameraPosition", value);
    }
  }

  #installOrbitInput(canvas: HTMLCanvasElement) {
    const onPointerDown = (event: PointerEvent) => {
      if (this.#pointerId !== null) return;
      this.#pointerId = event.pointerId;
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
      this.#yaw -= dx * 0.005;
      this.#pitch = Math.max(
        -Math.PI * 0.49,
        Math.min(Math.PI * 0.49, this.#pitch + dy * 0.005),
      );
      this.#cameraDirty = true;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (event.pointerId !== this.#pointerId) return;
      this.#pointerId = null;
      canvas.releasePointerCapture(event.pointerId);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      this.#distance = Math.max(0.01, this.#distance * Math.exp(event.deltaY * 0.001));
      this.#cameraDirty = true;
    };
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    this.#removeInputListeners = () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }
}

export const createPlayCanvasResidentBackend = (
  options: PlayCanvasResidentBackendOptions = {},
) => new PlayCanvasResidentBackend(options);
