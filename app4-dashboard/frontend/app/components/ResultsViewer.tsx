"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { Map as MapIcon, Layers, Eye, Download, Box } from "lucide-react";
import { useStore } from "../lib/store";
import { getFileUrl, getPreviewUrl, getApiBaseUrl, fetchBrowse } from "../lib/api";

type ViewerLayer = "ortho" | "depth" | "annotated";

const LAYER_META: Record<ViewerLayer, { label: string; s3Suffix: string }> = {
  ortho: { label: "Orthomosaic", s3Suffix: "orthomosaic.tif" },
  depth: { label: "Depth Map", s3Suffix: "orthomosaic.height.tif" },
  annotated: { label: "Annotated", s3Suffix: "orthomosaic_annotated.tif" },
};

function ImageViewer({ src, alt, depthMode }: { src: string; alt: string; depthMode?: boolean }) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const imgRef = React.useRef<HTMLImageElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [baseScale, setBaseScale] = useState(1);

  // Compute the scale that fits the image inside the container
  const computeFit = React.useCallback(() => {
    const container = containerRef.current;
    const img = imgRef.current;
    if (!container || !img || !img.naturalWidth) return;
    const cw = container.clientWidth;
    const ch = container.clientHeight;
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;
    const scale = Math.min(cw / iw, ch / ih, 1); // don't upscale beyond 1:1
    setBaseScale(scale);
  }, []);

  // Recompute fit on load and on resize
  useEffect(() => {
    if (!loaded) return;
    computeFit();
    const observer = new ResizeObserver(() => computeFit());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [loaded, computeFit]);

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.max(0.1, Math.min(20, z * (e.deltaY < 0 ? 1.15 : 0.87))));
  };

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setDragging(false);
  };

  const fitToView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  // Effective scale = baseScale * zoom
  const effectiveScale = baseScale * zoom;

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-gray-400">
        <div className="text-center">
          <MapIcon size={36} className="mx-auto mb-2 text-gray-300" />
          <p className="text-sm">File not available yet</p>
          <p className="mt-1 text-xs text-gray-300">Run the pipeline to generate this output</p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full touch-none cursor-grab overflow-hidden bg-gray-900 active:cursor-grabbing"
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={() => setDragging(false)}
    >
      {!loaded && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-600 border-t-blue-400" />
        </div>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        src={src}
        alt={alt}
        onLoad={() => setLoaded(true)}
        onError={() => setError(true)}
        draggable={false}
        className={`absolute left-1/2 top-1/2 max-w-none select-none transition-opacity ${loaded ? "opacity-100" : "opacity-0"}`}
        style={{
          transform: `translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${effectiveScale})`,
          transformOrigin: "center center",
          filter: depthMode ? "hue-rotate(0deg)" : undefined,
        }}
      />
      <div className="absolute bottom-3 right-3 flex gap-1.5 rounded-lg bg-black/60 px-3 py-1.5 text-xs text-white backdrop-blur">
        <button type="button" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(20, z * 1.5))} className="min-h-8 min-w-8 hover:text-blue-300">+</button>
        <span className="mx-1 text-gray-400">|</span>
        <button type="button" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.1, z / 1.5))} className="min-h-8 min-w-8 hover:text-blue-300">−</button>
        <span className="mx-1 text-gray-400">|</span>
        <button type="button" onClick={fitToView} className="min-h-8 px-1 hover:text-blue-300">Fit</button>
        <span className="mx-1 text-gray-400">|</span>
        <button type="button" onClick={() => { setZoom(1 / baseScale); setPan({ x: 0, y: 0 }); }} className="min-h-8 px-1 hover:text-blue-300">1:1</button>
        <span className="mx-1 text-gray-400">|</span>
        <span>{Math.round(effectiveScale * 100)}%</span>
      </div>
      {depthMode && (
        <div className="absolute bottom-3 left-3 flex items-center gap-2 rounded-lg bg-black/60 px-3 py-1.5 text-xs text-white backdrop-blur">
          <div className="h-2 w-24 rounded-full" style={{ background: "linear-gradient(90deg, #3b82f6, #22d3ee, #22c55e, #eab308, #ef4444)" }} />
          <span className="text-gray-300">Low → High</span>
        </div>
      )}
    </div>
  );
}

export default function ResultsViewer() {
  const { activeMission, missions } = useStore();
  const [activeLayer, setActiveLayer] = useState<ViewerLayer>("ortho");
  const [selectedVol, setSelectedVol] = useState<string | null>(null);
  const [availableFiles, setAvailableFiles] = useState<string[]>([]);

  const sortedMissions = useMemo(
    () => Object.values(missions).sort((a, b) => b.updated_at - a.updated_at),
    [missions],
  );

  const mission = selectedVol ? missions[selectedVol] : activeMission;
  const missionId = mission?.vol_id ?? sortedMissions[0]?.vol_id ?? null;

  // Fetch available files for the selected mission (top-level + key subdirs)
  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;

    const loadFiles = async () => {
      try {
        const prefix = `missions/${missionId}/`;
        const [top, sparse, gaussian] = await Promise.all([
          fetchBrowse(prefix).catch(() => []),
          fetchBrowse(`${prefix}colmap/sparse/0/`).catch(() => []),
          fetchBrowse(`${prefix}gaussian/`).catch(() => []),
        ]);
        const all = [...(top as Record<string, string>[]), ...(sparse as Record<string, string>[]), ...(gaussian as Record<string, string>[])];
        const paths = all.map((d) => d.path ?? d.name ?? "");
        if (!cancelled) setAvailableFiles(paths);
      } catch {
        if (!cancelled) setAvailableFiles([]);
      }
    };

    void loadFiles();
    return () => { cancelled = true; };
  }, [missionId]);

  const hasFile = useCallback(
    (suffix: string) => availableFiles.some((f) => f.endsWith(suffix)),
    [availableFiles],
  );

  const effectiveLayer = useMemo<ViewerLayer>(() => {
    if (hasFile(LAYER_META[activeLayer].s3Suffix)) return activeLayer;
    return (Object.entries(LAYER_META) as [ViewerLayer, { s3Suffix: string }][])
      .find(([, meta]) => hasFile(meta.s3Suffix))?.[0] ?? activeLayer;
  }, [activeLayer, hasFile]);

  // Use the preview endpoint (TIF→PNG) instead of the raw file
  const imageUrl = useMemo(() => {
    if (!missionId) return null;
    const suffix = LAYER_META[effectiveLayer].s3Suffix;
    const cmap = effectiveLayer === "depth" ? "depth" : "";
    return getPreviewUrl(`missions/${missionId}/${suffix}`, 4096, cmap);
  }, [missionId, effectiveLayer]);

  // Detect point cloud files
  const hasPointCloud = hasFile("points3D.bin") || hasFile("points3D.ply");
  const hasGaussianPly = availableFiles.some((f) => f.includes("gaussian/") && f.endsWith(".ply"));
  const gaussianPlyKey = availableFiles.find((f) => f.includes("gaussian/") && f.endsWith("final.ply"))
    ?? availableFiles.find((f) => f.includes("gaussian/") && f.endsWith(".ply"));

  if (!missionId || sortedMissions.length === 0) {
    return (
      <div className="surface flex min-h-[520px] items-center justify-center text-[#87938f]">
        <div className="text-center">
          <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-[#edf3f1] text-[#78908a]">
            <MapIcon size={26} />
          </span>
          <p className="mt-4 text-sm font-bold text-[#485651]">No mission results yet</p>
          <p className="mt-1 text-xs text-[#8a9692]">Launch the pipeline to generate maps and point clouds.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <section className="surface p-5 sm:p-6">
        <div className="eyebrow">Stage 05 · Delivery</div>
        <h2 className="mt-2 text-2xl font-bold tracking-[-0.035em] text-[#17201e]">
          Mission products
        </h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-[#6f7c78]">
          Inspect georeferenced rasters, compare derived layers and download the
          sparse or Gaussian scene for downstream GIS and 3D workflows.
        </p>
      </section>

      <div className="flex min-h-[620px] flex-col gap-4 lg:h-[calc(100vh-250px)] lg:flex-row">
      {/* Sidebar */}
      <div className="grid shrink-0 gap-3 sm:grid-cols-3 lg:block lg:w-72 lg:space-y-4 lg:overflow-y-auto">
        {/* Mission selector */}
        <div className="surface p-4">
          <h3 className="eyebrow mb-3">Mission</h3>
          <select
            value={selectedVol ?? missionId}
            onChange={(e) => setSelectedVol(e.target.value)}
            className="input-control min-h-11 font-mono"
          >
            {sortedMissions.map((m) => (
              <option key={m.vol_id} value={m.vol_id}>{m.vol_id}</option>
            ))}
          </select>
        </div>

        {/* Layer selector */}
        <div className="surface p-4">
          <h3 className="eyebrow mb-3">
            <Layers size={13} className="mr-1.5 inline" /> Layer
          </h3>
          <div className="space-y-1.5">
            {(Object.entries(LAYER_META) as [ViewerLayer, typeof LAYER_META[ViewerLayer]][]).map(([key, meta]) => {
              const available = hasFile(meta.s3Suffix);
              return (
                <button
                  key={key}
                  onClick={() => available && setActiveLayer(key)}
                  disabled={!available}
                  className={`flex w-full items-center gap-2 rounded-xl border px-3 py-2.5 text-left text-sm transition ${
                    !available
                      ? "border-gray-50 text-gray-300 cursor-not-allowed"
                      : effectiveLayer === key
                        ? "border-[#68bfae] bg-[#edf9f6] text-[#0f766e]"
                        : "border-[#dce4e1] text-[#5d6965] hover:border-[#b8c9c3]"
                  }`}
                >
                  <Eye size={14} />
                  {meta.label}
                  {!available && <span className="ml-auto text-[10px] text-gray-300">N/A</span>}
                </button>
              );
            })}
          </div>
        </div>

        {/* Downloads */}
        <div className="surface p-4">
          <h3 className="eyebrow mb-3">
            <Download size={13} className="mr-1.5 inline" /> Downloads
          </h3>
          <div className="space-y-1.5">
            {(Object.entries(LAYER_META) as [ViewerLayer, typeof LAYER_META[ViewerLayer]][]).map(([key, meta]) => (
              hasFile(meta.s3Suffix) && (
                <a
                  key={key}
                  href={getFileUrl(`missions/${missionId}/${meta.s3Suffix}`)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex min-h-10 items-center gap-2 rounded-xl border border-[#e1e8e5] px-3 py-2 text-xs text-[#5d6965] hover:border-[#83cfc1] hover:text-[#0f766e]"
                >
                  <Download size={12} /> {meta.label} (.tif)
                </a>
              )
            ))}
            {hasGaussianPly && gaussianPlyKey && (
              <a
                href={getFileUrl(gaussianPlyKey)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-h-10 items-center gap-2 rounded-xl border border-[#e1e8e5] px-3 py-2 text-xs text-[#5d6965] hover:border-[#83cfc1] hover:text-[#0f766e]"
              >
                <Box size={12} /> Gaussian Point Cloud (.ply)
              </a>
            )}
            {hasPointCloud && (
              <a
                href={getFileUrl(`missions/${missionId}/colmap/sparse/0/${hasFile("points3D.ply") ? "points3D.ply" : "points3D.bin"}`)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-h-10 items-center gap-2 rounded-xl border border-[#e1e8e5] px-3 py-2 text-xs text-[#5d6965] hover:border-[#83cfc1] hover:text-[#0f766e]"
              >
                <Box size={12} /> Sparse Point Cloud (COLMAP)
              </a>
            )}
            <a
              href={`${getApiBaseUrl()}/browse?prefix=${encodeURIComponent(`missions/${missionId}/tiles/`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex min-h-10 items-center gap-2 rounded-xl border border-[#e1e8e5] px-3 py-2 text-xs text-[#5d6965] hover:border-[#83cfc1] hover:text-[#0f766e]"
            >
              <Download size={12} /> Tiles folder
            </a>
          </div>
        </div>
      </div>

      {/* Main viewer */}
      <div className="min-h-[520px] flex-1 overflow-hidden rounded-[1.25rem] border border-[#263632] bg-[#16201d] shadow-[0_20px_50px_rgba(20,32,28,0.12)]">
        <ImageViewer
          key={imageUrl}
          src={imageUrl!}
          alt={LAYER_META[effectiveLayer].label}
          depthMode={effectiveLayer === "depth"}
        />
      </div>
    </div>
    </div>
  );
}
