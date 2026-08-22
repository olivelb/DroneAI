import type { GsTileManifest } from "./contracts";
import type { GsTileRangeScheduler } from "./range-source";

export type GaussianCameraState = {
  view: Float64Array;
  projection: Float64Array;
  viewportWidth: number;
  viewportHeight: number;
};

export type GaussianRenderStatistics = {
  residentGaussians: number;
  residentBytes: number;
  selectedNodes: number;
  frameCpuMs: number | null;
  frameGpuMs: number | null;
};

export interface GaussianRenderBackend {
  readonly id: string;
  initialize(canvas: HTMLCanvasElement): Promise<void>;
  loadBundle(
    manifestUrl: string,
    manifest: GsTileManifest,
    scheduler: GsTileRangeScheduler,
    signal: AbortSignal,
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
