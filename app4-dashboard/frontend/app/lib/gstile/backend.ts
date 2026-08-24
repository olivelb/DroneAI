import type { GsTileManifest } from "./contracts";
import type { GsTileRangeScheduler } from "./range-source";

export type GaussianViewFrame = {
  kind: "facade";
  right: readonly [number, number, number];
  up: readonly [number, number, number];
  outward: readonly [number, number, number];
};

export type GaussianCameraState = {
  view: Float64Array;
  projection: Float64Array;
  viewportWidth: number;
  viewportHeight: number;
};

export type GaussianRenderStatistics = {
  lodState: "steady" | "refining" | "budget-limited" | "error";
  residentGaussians: number;
  residentBytes: number;
  selectedNodes: number;
  targetGaussians: number;
  targetNodes: number;
  pendingNodes: number;
  maximumSelectedErrorPixels: number;
  effectiveMaximumErrorPixels: number;
  selectedExactNodes: number;
  selectedProxyNodes: number;
  selectedFullDepthNodes: number;
  selectedShallowLeafNodes: number;
  selectedInternalNodes: number;
  selectedLeafDepthCounts: number[];
  maximumSelectedProxyScreenRadiusPixels: number;
  maximumResidentGaussians: number;
  verticalFovDegrees: number | null;
  frameCpuMs: number | null;
  frameGpuMs: number | null;
  workBufferUploadPercent: number | null;
  lodTotalMs: number | null;
  lodLoadMs: number | null;
  lodCommitMs: number | null;
  lodFetchServiceMs: number | null;
  lodSha256ServiceMs: number | null;
  lodDecodeCpuMs: number | null;
  lodResourceCreateMs: number | null;
  lodResourceColorMs: number | null;
  lodResourceTransformMs: number | null;
  lodResourceShMs: number | null;
  lodStreamUploadMs: number | null;
  lodSceneAttachMs: number | null;
  lodAddedGaussians: number;
  lodRemovedGaussians: number;
  lodReusedGaussians: number;
};

export interface GaussianRenderBackend {
  readonly id: string;
  initialize(canvas: HTMLCanvasElement): Promise<void>;
  loadBundle(
    manifestUrl: string,
    manifest: GsTileManifest,
    scheduler: GsTileRangeScheduler,
    signal: AbortSignal,
    packUrls?: ReadonlyMap<string, string>,
    recommendedView?: GaussianViewFrame | null,
  ): Promise<void>;
  setCamera(camera: GaussianCameraState): void;
  render(timestampMs: number): GaussianRenderStatistics;
  resize(width: number, height: number, devicePixelRatio: number): void;
  dispose(): void;
}

export class GaussianBackendUnavailable extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GaussianBackendUnavailable";
  }
}
