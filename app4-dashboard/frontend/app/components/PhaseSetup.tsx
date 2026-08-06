"use client";

import React, { useState } from "react";
import {
  ChevronRight,
  File,
  Folder,
  Home,
  Trash2,
  Upload,
} from "lucide-react";
import { useStore } from "../lib/store";
import { uploadDataset as uploadDatasetApi, deleteDataset as deleteDatasetApi } from "../lib/api";
import StageHeader from "./StageHeader";

export default function PhaseSetup() {
  const {
    currentPath, items, selectedPath, browse, setSelectedPath,
    volId, setVolId, missions, activeMissionId, setActiveMissionId,
    uploadDatasetName, setUploadDatasetName, uploadFiles, setUploadFiles,
    uploadProgress, setUploadProgress, isUploading, setIsUploading,
  } = useStore();

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const goUp = () => {
    const parts = currentPath.split("/").filter(Boolean);
    parts.pop();
    void browse("/" + parts.join("/"));
  };

  const handleUpload = async () => {
    if (!uploadFiles || uploadFiles.length === 0 || !uploadDatasetName.trim()) return;
    setIsUploading(true);
    setUploadProgress({ total: uploadFiles.length, completed: 0, failed: 0, status: "uploading" });
    try {
      const result = await uploadDatasetApi(uploadDatasetName.trim(), uploadFiles, (p) => {
        setUploadProgress({ ...p, status: "uploading" });
      });
      setUploadProgress({
        total: result.total ?? uploadFiles.length,
        completed: result.completed ?? 0,
        failed: result.failed ?? 0,
        status: result.status ?? "done",
      });
      // Always refresh the dataset listing after upload
      await browse("datasets/");
      // Auto-select the newly created dataset
      setSelectedPath(`datasets/${uploadDatasetName.trim().replace(/[^a-zA-Z0-9_\-]/g, "_")}`);
    } catch {
      setUploadProgress((prev) => prev ? { ...prev, status: "error" } : null);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="space-y-5">
      <StageHeader
        eyebrow="Étape 01 · Préparation"
        title="Préparer la mission aérienne"
        description="Sélectionnez la collection d’images et donnez un identifiant durable à cette exécution. Les imports et anciennes missions restent accessibles sans encombrer le parcours principal."
        icon={<Folder size={21} />}
        iconClassName="bg-[#e1f3ef] text-[#0f766e]"
        status={
          <div className={`rounded-2xl border px-4 py-3 ${
            selectedPath
              ? "border-emerald-200 bg-emerald-50"
              : "border-amber-200 bg-amber-50"
          }`}>
            <div className="text-[10px] font-bold uppercase tracking-wide text-[#7c8884]">
              Entrée
            </div>
            <div className="mt-0.5 max-w-56 truncate text-sm font-semibold text-[#34413d]">
              {selectedPath || "Dataset à sélectionner"}
            </div>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Left: Dataset browser */}
      <div className="surface overflow-hidden">
        <div className="border-b border-[#e7ecea] p-5">
          <div className="eyebrow">Source imagery</div>
          <h2 className="mt-1 text-lg font-bold text-[#293632]">Dataset browser</h2>
          <p className="mt-1 text-xs text-[#7a8783]">Navigate object storage and select one image collection.</p>
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-[#dce4e1] bg-[#f7faf9] px-3 py-2 font-mono text-xs text-[#64716d]">
            <button type="button" aria-label="Dataset root" onClick={() => void browse("/")} className="text-[#0f766e]"><Home size={14} /></button>
            <ChevronRight size={12} className="text-gray-300" />
            <span className="truncate">{currentPath || "/"}</span>
            <button onClick={goUp} className="ml-auto rounded-lg bg-gray-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:bg-gray-200">
              Up
            </button>
          </div>
        </div>

        <div className="max-h-[430px] overflow-y-auto p-3">
          <div className="space-y-1">
            {items.map((item) => {
              const isSelected = item.is_dir
                ? selectedPath === item.path || selectedPath === item.path + "/"
                : selectedPath === item.path;
              return (
              <div
                key={item.path}
                className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-gray-50 ${
                  isSelected ? "bg-[#edf9f6] ring-1 ring-[#83cfc1]" : ""
                }`}
              >
                {item.is_dir ? (
                  <button
                    onClick={() => void browse(item.path + "/")}
                    className="flex min-h-10 items-center gap-2 truncate text-[#46534f] hover:text-[#0f766e]"
                    title="Open folder"
                  >
                    <Folder size={16} className="shrink-0 text-[#0f766e]" />
                    <span className="truncate font-medium">{item.name}</span>
                  </button>
                ) : (
                  <button
                    onClick={() => setSelectedPath(item.path)}
                    className="flex min-h-10 items-center gap-2 truncate text-[#46534f] hover:text-[#0f766e]"
                  >
                    <File size={16} className="text-gray-400 shrink-0" />
                    <span className="truncate font-medium">{item.name}</span>
                  </button>
                )}
                <span className="ml-auto flex items-center gap-1">
                  {item.is_dir && item.image_count > 0 && (
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-500">
                      {item.image_count} imgs
                    </span>
                  )}
                  {item.is_dir && (
                    <button
                      onClick={() => { setSelectedPath(item.path); }}
                      title="Select as input dataset"
                      className={`rounded-lg px-2 py-1 text-[10px] font-bold uppercase tracking-wider transition ${
                        isSelected
                          ? "bg-[#0f766e] text-white"
                          : "bg-[#edf3f1] text-[#5d6a66] hover:bg-[#dff5f0] hover:text-[#0f766e]"
                      }`}
                    >
                      {isSelected ? "Selected" : "Select"}
                    </button>
                  )}
                  {item.is_dir && currentPath.startsWith("datasets") && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDelete(item.name); }}
                      title={`Delete ${item.name}`}
                      className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-500"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </span>
              </div>
              );
            })}
            {items.length === 0 && <p className="p-4 text-center text-xs text-gray-400">Empty folder</p>}
          </div>
          {confirmDelete && (
            <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3">
              <p className="text-xs text-red-700">Delete dataset <strong>{confirmDelete}</strong> and all its images?</p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={async () => {
                    setDeleting(true);
                    try {
                      await deleteDatasetApi(confirmDelete);
                      void browse(currentPath);
                    } catch (e) { console.error("Delete failed:", e); }
                    finally { setDeleting(false); setConfirmDelete(null); }
                  }}
                  disabled={deleting}
                  className="rounded-md bg-red-500 px-3 py-1 text-xs font-semibold text-white hover:bg-red-600 disabled:opacity-50"
                >
                  {deleting ? "Deleting…" : "Confirm"}
                </button>
                <button
                  onClick={() => setConfirmDelete(null)}
                  className="rounded-md border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:bg-gray-100"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        <details className="border-t border-[#e7ecea]">
          <summary className="flex min-h-14 cursor-pointer list-none items-center gap-2 px-4 text-xs font-bold text-[#52615c] hover:bg-[#f7faf9]">
            <Upload size={14} className="text-[#0f766e]" />
            Importer un nouveau dataset
          </summary>
          <div className="space-y-2 border-t border-[#edf1ef] p-4">
            <input
              type="text"
              placeholder="Nom du dataset"
              value={uploadDatasetName}
              onChange={(e) => setUploadDatasetName(e.target.value)}
              className="input-control min-h-11"
            />
            <label className="flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border border-dashed border-[#c8d3cf] bg-[#f7faf9] px-3 py-3 text-xs text-[#687571] hover:border-[#68bfae] hover:text-[#0f766e]">
              <Upload size={14} />
              {uploadFiles ? `${uploadFiles.length} fichier(s) sélectionné(s)` : "Sélectionner les images…"}
              <input type="file" multiple className="hidden" onChange={(e) => setUploadFiles(e.target.files)} />
            </label>
            <button
              onClick={handleUpload}
              disabled={isUploading || !uploadFiles || uploadFiles.length === 0 || !uploadDatasetName.trim()}
              className="min-h-11 w-full rounded-xl bg-[#0f766e] px-3 py-2 text-sm font-semibold text-white hover:bg-[#115e59] disabled:cursor-not-allowed disabled:bg-[#d4ddda] disabled:text-white"
            >
              {isUploading ? "Import en cours…" : "Importer"}
            </button>
            {uploadProgress && (
              <div className="space-y-1">
                <div className="rounded-lg bg-gray-50 p-2 text-xs text-gray-500">
                  {uploadProgress.completed}/{uploadProgress.total} files — {uploadProgress.status}
                  {uploadProgress.failed > 0 && <span className="text-red-500"> ({uploadProgress.failed} failed)</span>}
                </div>
                {uploadProgress.status === "uploading" && uploadProgress.total > 0 && (
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200">
                    <div
                      className="h-full rounded-full bg-blue-500 transition-all duration-300"
                      style={{ width: `${Math.round((uploadProgress.completed / uploadProgress.total) * 100)}%` }}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </details>
      </div>

      {/* Right: Mission config */}
      <div className="space-y-6">
        <div className="surface p-5 sm:p-6">
          <div className="eyebrow">Mission identity</div>
          <h2 className="mt-1 text-lg font-bold text-[#293632]">Name this run</h2>
          <p className="mt-1 text-xs text-[#7a8783]">Use a durable ID for tracking, files and resume checkpoints.</p>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-gray-600">Mission ID</span>
              <input
                value={volId}
                onChange={(e) => setVolId(e.target.value)}
                className="input-control min-h-11 font-mono"
              />
            </label>
          </div>
        </div>

        {/* Selection summary */}
        <div className="surface p-5 sm:p-6">
          <h3 className="text-sm font-bold text-[#34413d]">Selected dataset</h3>
          <div className={`mt-3 rounded-xl border px-4 py-3 font-mono text-sm ${
            selectedPath ? "border-[#83cfc1] bg-[#edf9f6] text-[#0f766e]" : "border-[#e1e8e5] bg-[#f7faf9] text-[#8a9692]"
          }`}>
            {selectedPath || "No dataset selected — click \"Select\" on a folder"}
          </div>
          {selectedPath && (
            <button
              onClick={() => setSelectedPath("")}
              className="mt-2 text-xs text-gray-400 hover:text-red-500"
            >
              Clear selection
            </button>
          )}
        </div>

        <details className="surface">
          <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-3 px-5">
            <span>
              <span className="block text-sm font-bold text-[#34413d]">Missions précédentes</span>
              <span className="mt-0.5 block text-xs text-[#7a8783]">Reprendre ou inspecter une production.</span>
            </span>
            <span className="rounded-full bg-[#edf3f1] px-2.5 py-1 text-[10px] font-bold text-[#65736e]">
              {Object.keys(missions).length}
            </span>
          </summary>
          <div className="max-h-[300px] space-y-1.5 overflow-y-auto border-t border-[#e5ebe8] p-4">
            {Object.values(missions).sort((a, b) => b.updated_at - a.updated_at).map((m) => (
              <button
                key={m.vol_id}
                onClick={() => { setActiveMissionId(m.vol_id); setVolId(m.vol_id); }}
                className={`flex w-full items-center justify-between rounded-xl border px-4 py-2.5 text-left text-sm transition ${
                  activeMissionId === m.vol_id ? "border-[#83cfc1] bg-[#edf9f6]" : "border-[#e1e8e5] hover:border-[#b8c9c3]"
                }`}
              >
                <span className="font-mono font-medium text-gray-700">{m.vol_id}</span>
                <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
                  m.overall_status === "success" ? "bg-emerald-50 text-emerald-600"
                  : m.overall_status === "error" ? "bg-red-50 text-red-600"
                  : m.overall_status === "cancelled" ? "bg-slate-100 text-slate-600"
                  : "bg-blue-50 text-blue-600"
                }`}>{m.overall_status}</span>
              </button>
            ))}
            {Object.keys(missions).length === 0 && <p className="text-xs text-gray-400">Aucune mission</p>}
          </div>
        </details>
      </div>
    </div>
    </div>
  );
}
