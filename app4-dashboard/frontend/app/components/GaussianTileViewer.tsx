"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { apiCredentials } from "../lib/api-client";
import type {
  GaussianRenderBackend,
  GaussianRenderStatistics,
} from "../lib/gstile/backend";
import { decodeGsTileManifest } from "../lib/gstile/contracts";
import {
  createPlayCanvasResidentBackend,
  gstileGpuAssembly,
  gstileOpacityMode,
  gstileSortMode,
  gstileTransformPrecision,
  gstileVerticalFovDegrees,
} from "../lib/gstile/playcanvas-backend";
import { decodeGsTileViewerDescriptor } from "../lib/gstile/descriptor";
import { GsTileRangeScheduler } from "../lib/gstile/range-source";

export type GaussianTileViewerProps = {
  manifestUrl?: string;
  descriptorUrl?: string;
  createBackend?: () => GaussianRenderBackend;
  className?: string;
};

const defaultBackendFactory = () => {
  const search = new URLSearchParams(window.location.search);
  const scaleOption = search.get("gstileMaxScale");
  const debugTilesOption = search.get("gstileDebugTiles");
  const debugTiles =
    debugTilesOption === "id" || debugTilesOption === "lod"
      ? debugTilesOption : "off";
  const assemblyOption = search.get("gstileGpuAssembly");
  const gpuAssembly = gstileGpuAssembly(assemblyOption);
  const maximumGaussianScale =
    scaleOption === null || scaleOption === "none"
      ? Number.MAX_VALUE
      : Number(scaleOption);
  return createPlayCanvasResidentBackend({
    transformPrecision: gstileTransformPrecision(search.get("gstileTransform")),
    verticalFovDegrees: gstileVerticalFovDegrees(search.get("gstileFov")),
    maximumGaussianScale,
    includeSiblingLeaves: search.get("gstileSiblingLeaves") === "1",
    retainOffscreenCoverage: search.get("gstileCoverage") !== "0",
    opacityMode: gstileOpacityMode(search.get("gstileOpacity")),
    sortMode: gstileSortMode(search.get("gstileSort")),
    radialSorting: search.get("gstileRadialSort") === "1",
    debugTiles,
    gpuAssembly,
  });
};

const emptyStatistics: GaussianRenderStatistics = {
  lodState: "steady",
  residentGaussians: 0,
  residentBytes: 0,
  selectedNodes: 0,
  targetGaussians: 0,
  targetNodes: 0,
  pendingNodes: 0,
  maximumSelectedErrorPixels: 0,
  effectiveMaximumErrorPixels: 0,
  selectedExactNodes: 0,
  selectedProxyNodes: 0,
  selectedFullDepthNodes: 0,
  selectedShallowLeafNodes: 0,
  selectedInternalNodes: 0,
  selectedLeafDepthCounts: [],
  maximumSelectedProxyScreenRadiusPixels: 0,
  maximumResidentGaussians: 0,
  verticalFovDegrees: null,
  frameCpuMs: null,
  frameGpuMs: null,
  workBufferUploadPercent: null,
  lodTotalMs: null,
  lodLoadMs: null,
  lodCommitMs: null,
  lodAddedGaussians: 0,
  lodRemovedGaussians: 0,
  lodReusedGaussians: 0,
};

const formatCount = (value: number) =>
  new Intl.NumberFormat(undefined, { notation: "compact" }).format(value);

/**
 * Lifecycle shell shared by every GSTile renderer.
 *
 * GPU resources, request queues and camera state intentionally live in the
 * backend, outside React state. React receives only throttled diagnostics.
 */
export default function GaussianTileViewer({
  manifestUrl,
  descriptorUrl,
  createBackend = defaultBackendFactory,
  className = "",
}: GaussianTileViewerProps) {
  const searchParams = useSearchParams();
  const backendQueryKey = searchParams.toString();
  const assemblyOption = gstileGpuAssembly(
    searchParams.get("gstileGpuAssembly"),
  );
  const mergedGpuMode = assemblyOption === "merged";
  const incrementalGpuMode = assemblyOption === "incremental";
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState("Initialisation…");
  const [error, setError] = useState<string | null>(null);
  const [statistics, setStatistics] =
    useState<GaussianRenderStatistics>(emptyStatistics);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const controller = new AbortController();
    // Six requests saturate the usual per-origin HTTP/1.1 connection pool
    // without the burst memory of decoding an unbounded LOD cut concurrently.
    const scheduler = new GsTileRangeScheduler(6);
    const backend = createBackend();
    setStatistics(emptyStatistics);
    let animation = 0;
    let lastDiagnostics = 0;
    let disposed = false;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      backend.resize(rect.width, rect.height, window.devicePixelRatio || 1);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const frame = (timestamp: number) => {
      if (disposed) return;
      const next = backend.render(timestamp);
      if (timestamp - lastDiagnostics >= 500) {
        setStatistics(next);
        lastDiagnostics = timestamp;
      }
      animation = requestAnimationFrame(frame);
    };

    void (async () => {
      try {
        if ((manifestUrl ? 1 : 0) + (descriptorUrl ? 1 : 0) !== 1) {
          throw new Error("Le viewer requiert une source GSTile unique");
        }
        const sourceUrl = descriptorUrl ?? manifestUrl!;
        setError(null);
        setStatus("Chargement du manifeste…");
        const response = await fetch(sourceUrl, {
          signal: controller.signal,
          credentials: descriptorUrl ? apiCredentials() : "same-origin",
        });
        if (!response.ok) {
          throw new Error(`Manifeste GSTile indisponible (HTTP ${response.status})`);
        }
        const payload: unknown = await response.json();
        const descriptor = descriptorUrl
          ? decodeGsTileViewerDescriptor(payload)
          : null;
        const manifest = descriptor?.manifest ?? decodeGsTileManifest(payload);
        setStatus("Initialisation du moteur…");
        await backend.initialize(canvas);
        resize();
        setStatus("Chargement progressif…");
        await backend.loadBundle(
          sourceUrl,
          manifest,
          scheduler,
          controller.signal,
          descriptor?.packUrls,
          descriptor?.recommendedView,
        );
        if (controller.signal.aborted) return;
        setStatus("Prêt");
        animation = requestAnimationFrame(frame);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setStatus("Échec");
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();

    return () => {
      disposed = true;
      controller.abort(new DOMException("Viewer disposed", "AbortError"));
      cancelAnimationFrame(animation);
      observer.disconnect();
      backend.dispose();
    };
  }, [backendQueryKey, createBackend, descriptorUrl, manifestUrl]);

  const displayStatus =
    status === "Prêt" && statistics.lodState === "refining"
      ? "Préparation atomique…"
      : status === "Prêt" && statistics.lodState === "budget-limited"
        ? "Limite GPU atteinte"
        : status;

  return (
    <div
      className={`relative min-h-80 overflow-hidden rounded-2xl bg-[#101816] ${className}`}
      data-testid="gstile-viewer"
      data-status={status}
      data-lod-state={statistics.lodState}
      data-resident-gaussians={statistics.residentGaussians}
      data-selected-nodes={statistics.selectedNodes}
      data-target-gaussians={statistics.targetGaussians}
      data-target-nodes={statistics.targetNodes}
      data-pending-nodes={statistics.pendingNodes}
      data-selected-exact-nodes={statistics.selectedExactNodes}
      data-selected-proxy-nodes={statistics.selectedProxyNodes}
      data-selected-full-depth-nodes={statistics.selectedFullDepthNodes}
      data-selected-shallow-leaf-nodes={statistics.selectedShallowLeafNodes}
      data-selected-internal-nodes={statistics.selectedInternalNodes}
      data-selected-leaf-depth-counts={statistics.selectedLeafDepthCounts.join(",")}
      data-maximum-proxy-radius-pixels={statistics.maximumSelectedProxyScreenRadiusPixels}
      data-frame-cpu-ms={statistics.frameCpuMs ?? ""}
      data-frame-gpu-ms={statistics.frameGpuMs ?? ""}
      data-work-buffer-upload-percent={statistics.workBufferUploadPercent ?? ""}
      data-vertical-fov-degrees={statistics.verticalFovDegrees ?? ""}
      data-lod-total-ms={statistics.lodTotalMs ?? ""}
      data-lod-load-ms={statistics.lodLoadMs ?? ""}
      data-lod-commit-ms={statistics.lodCommitMs ?? ""}
      data-lod-reused-gaussians={statistics.lodReusedGaussians}
    >
      <canvas ref={canvasRef} className="block h-full min-h-80 w-full" />
      <div className="pointer-events-none absolute left-3 top-3 rounded-xl border border-white/10 bg-black/55 px-3 py-2 text-[11px] text-white/80 backdrop-blur">
        <div className="font-semibold text-white">
          {mergedGpuMode
            ? "GSTile MERGED"
            : incrementalGpuMode
              ? "GSTile INCREMENTAL"
              : "GSTile"} · {displayStatus}
        </div>
        {!error && (
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] text-white/60">
            <span>
              {formatCount(statistics.residentGaussians)} / {formatCount(statistics.targetGaussians || statistics.residentGaussians)} splats
            </span>
            <span>{statistics.selectedNodes} / {statistics.targetNodes || statistics.selectedNodes} tuiles</span>
            {(statistics.selectedExactNodes > 0 || statistics.selectedProxyNodes > 0) && (
              <span>
                <span className="text-violet-300">
                  {statistics.selectedExactNodes} terminales
                </span>
                {" · "}
                <span className="text-red-300">
                  {statistics.selectedInternalNodes} internes/proxys
                </span>
              </span>
            )}
            {statistics.selectedLeafDepthCounts.some((count) => count > 0) && (
              <span className="text-white/55">
                feuilles {statistics.selectedLeafDepthCounts
                  .map((count, depth) => count > 0 ? `L${depth}:${count}` : "")
                  .filter(Boolean)
                  .join(" ")}
              </span>
            )}
            {statistics.pendingNodes > 0 && <span>{statistics.pendingNodes} en attente</span>}
            {statistics.maximumSelectedErrorPixels > 0 && (
              <span>erreur {statistics.maximumSelectedErrorPixels.toFixed(1)} px</span>
            )}
            {statistics.maximumSelectedProxyScreenRadiusPixels > 0 && (
              <span>
                proxy max Ø {(statistics.maximumSelectedProxyScreenRadiusPixels * 2).toFixed(0)} px
              </span>
            )}
            {statistics.effectiveMaximumErrorPixels > 0 && (
              <span>SSE {statistics.effectiveMaximumErrorPixels.toFixed(2)} px</span>
            )}
            {statistics.maximumResidentGaussians > 0 && (
              <span>budget {formatCount(statistics.maximumResidentGaussians)}</span>
            )}
            {statistics.verticalFovDegrees !== null && (
              <span>FOV {statistics.verticalFovDegrees.toFixed(0)}°</span>
            )}
            {statistics.frameGpuMs !== null && (
              <span>{statistics.frameGpuMs.toFixed(1)} ms GPU</span>
            )}
            {statistics.frameCpuMs !== null && (
              <span>{statistics.frameCpuMs.toFixed(1)} ms CPU</span>
            )}
            {statistics.workBufferUploadPercent !== null && (
              <span>upload {statistics.workBufferUploadPercent.toFixed(1)}%</span>
            )}
            {statistics.lodTotalMs !== null && (
              <span>
                LOD {statistics.lodTotalMs.toFixed(0)} ms
                {statistics.lodLoadMs !== null &&
                  ` · load ${statistics.lodLoadMs.toFixed(0)}`}
                {statistics.lodCommitMs !== null &&
                  ` · commit ${statistics.lodCommitMs.toFixed(0)}`}
              </span>
            )}
            {statistics.lodReusedGaussians > 0 && (
              <span>
                reuse {formatCount(statistics.lodReusedGaussians)} splats
              </span>
            )}
          </div>
        )}
        <div className="mt-1 text-[9px] text-white/45">
          Rotation : clic gauche · Déplacement : clic droit ou Maj+glisser · Zoom : molette · FOV : Alt+molette
        </div>
      </div>
      {error && (
        <div className="absolute inset-x-4 bottom-4 rounded-xl border border-red-300/30 bg-red-950/85 p-3 text-xs text-red-100">
          {error}
        </div>
      )}
    </div>
  );
}
