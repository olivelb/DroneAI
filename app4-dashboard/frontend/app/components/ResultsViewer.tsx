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

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setLoaded(false);
    setError(false);
    setBaseScale(1);
  }, [src]);

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

  const handleMouseDown = (e: React.MouseEvent) => {
    setDragging(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragging) return;
    setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
  };

  const handleMouseUp = () => setDragging(false);

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
      className="relative h-full w-full cursor-grab overflow-hidden bg-gray-900 active:cursor-grabbing"
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
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
        <button onClick={() => setZoom((z) => Math.min(20, z * 1.5))} className="hover:text-blue-300">+</button>
        <span className="mx-1 text-gray-400">|</span>
        <button onClick={() => setZoom((z) => Math.max(0.1, z / 1.5))} className="hover:text-blue-300">−</button>
        <span className="mx-1 text-gray-400">|</span>
        <button onClick={fitToView} className="hover:text-blue-300">Fit</button>
        <span className="mx-1 text-gray-400">|</span>
        <button onClick={() => { setZoom(1 / baseScale); setPan({ x: 0, y: 0 }); }} className="hover:text-blue-300">1:1</button>
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

  // Auto-select the most recent mission when the list arrives and nothing is picked yet
  useEffect(() => {
    if (!selectedVol && !activeMission && sortedMissions.length > 0) {
      setSelectedVol(sortedMissions[0].vol_id);
    }
  }, [selectedVol, activeMission, sortedMissions]);

  const mission = selectedVol ? missions[selectedVol] : activeMission;
  const missionId = mission?.vol_id ?? sortedMissions[0]?.vol_id ?? null;

  // Fetch available files for the selected mission (top-level + key subdirs)
  const refreshFiles = useCallback(async (volId: string) => {
    try {
      const prefix = `missions/${volId}/`;
      const [top, sparse, gaussian] = await Promise.all([
        fetchBrowse(prefix).catch(() => []),
        fetchBrowse(`${prefix}sparse/0/`).catch(() => []),
        fetchBrowse(`${prefix}gaussian/`).catch(() => []),
      ]);
      const all = [...(top as Record<string, string>[]), ...(sparse as Record<string, string>[]), ...(gaussian as Record<string, string>[])];
      const paths = all.map((d) => d.path ?? d.name ?? "");
      setAvailableFiles(paths);
    } catch { setAvailableFiles([]); }
  }, []);

  useEffect(() => {
    if (missionId) refreshFiles(missionId);
  }, [missionId, refreshFiles]);

  const hasFile = useCallback(
    (suffix: string) => availableFiles.some((f) => f.endsWith(suffix)),
    [availableFiles],
  );

  // Use the preview endpoint (TIF→PNG) instead of the raw file
  const imageUrl = useMemo(() => {
    if (!missionId) return null;
    const suffix = LAYER_META[activeLayer].s3Suffix;
    const cmap = activeLayer === "depth" ? "depth" : "";
    return getPreviewUrl(`missions/${missionId}/${suffix}`, 4096, cmap);
  }, [missionId, activeLayer]);

  // Auto-switch to an available layer
  useEffect(() => {
    if (!missionId) return;
    const current = LAYER_META[activeLayer].s3Suffix;
    if (!hasFile(current)) {
      const fallback = (Object.entries(LAYER_META) as [ViewerLayer, { s3Suffix: string }][])
        .find(([, meta]) => hasFile(meta.s3Suffix));
      if (fallback) setActiveLayer(fallback[0]);
    }
  }, [availableFiles, activeLayer, missionId, hasFile]);

  // Detect point cloud files
  const hasPointCloud = hasFile("points3D.bin") || hasFile("points3D.ply");
  const hasGaussianPly = availableFiles.some((f) => f.includes("gaussian/") && f.endsWith(".ply"));
  const gaussianPlyKey = availableFiles.find((f) => f.includes("gaussian/") && f.endsWith("final.ply"))
    ?? availableFiles.find((f) => f.includes("gaussian/") && f.endsWith(".ply"));

  if (!missionId || sortedMissions.length === 0) {
    return (
      <div className="flex h-[calc(100vh-200px)] items-center justify-center text-gray-400">
        <div className="text-center">
          <MapIcon size={36} className="mx-auto mb-2 text-gray-300" />
          <p className="text-sm">No missions found</p>
          <p className="mt-1 text-xs text-gray-300">Run a pipeline first to see results here</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-200px)] gap-6">
      {/* Sidebar */}
      <div className="w-72 shrink-0 space-y-4 overflow-y-auto">
        {/* Mission selector */}
        <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-400">Mission</h3>
          <select
            value={selectedVol ?? missionId}
            onChange={(e) => setSelectedVol(e.target.value)}
            className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-mono text-gray-700 outline-none"
          >
            {sortedMissions.map((m) => (
              <option key={m.vol_id} value={m.vol_id}>{m.vol_id}</option>
            ))}
          </select>
        </div>

        {/* Layer selector */}
        <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-400">
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
                      : activeLayer === key
                        ? "border-blue-400 bg-blue-50 text-blue-700"
                        : "border-gray-100 text-gray-600 hover:border-gray-200"
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
        <div className="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-400">
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
                  className="flex items-center gap-2 rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-600 hover:border-blue-200 hover:text-blue-600"
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
                className="flex items-center gap-2 rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-600 hover:border-blue-200 hover:text-blue-600"
              >
                <Box size={12} /> Gaussian Point Cloud (.ply)
              </a>
            )}
            {hasPointCloud && (
              <a
                href={getFileUrl(`missions/${missionId}/${hasFile("points3D.ply") ? "sparse/0/points3D.ply" : "sparse/0/points3D.bin"}`)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-600 hover:border-blue-200 hover:text-blue-600"
              >
                <Box size={12} /> Sparse Point Cloud (COLMAP)
              </a>
            )}
            <a
              href={`${getApiBaseUrl()}/browse?prefix=${encodeURIComponent(`missions/${missionId}/tiles/`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 rounded-xl border border-gray-100 px-3 py-2 text-xs text-gray-600 hover:border-blue-200 hover:text-blue-600"
            >
              <Download size={12} /> Tiles folder
            </a>
          </div>
        </div>
      </div>

      {/* Main viewer */}
      <div className="flex-1 rounded-2xl border border-gray-100 bg-gray-900 shadow-sm overflow-hidden">
        <ImageViewer
          src={imageUrl!}
          alt={LAYER_META[activeLayer].label}
          depthMode={activeLayer === "depth"}
        />
      </div>
    </div>
  );
}
