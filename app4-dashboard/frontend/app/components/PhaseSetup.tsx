"use client";

import React, { useState } from "react";
import { ChevronRight, File, Folder, Home, Trash2, Upload } from "lucide-react";
import { useStore } from "../lib/store";
import { uploadDataset as uploadDatasetApi, deleteDataset as deleteDatasetApi, getApiBaseUrl } from "../lib/api";

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
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Left: Dataset browser */}
      <div className="rounded-2xl border border-gray-100 bg-white shadow-sm">
        <div className="border-b border-gray-50 p-5">
          <h2 className="text-lg font-bold text-gray-800">Dataset Browser</h2>
          <p className="mt-1 text-xs text-gray-400">Navigate S3 datasets and select input images</p>
          <div className="mt-3 flex items-center gap-2 rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-500">
            <button onClick={() => void browse("/")} className="hover:text-blue-500"><Home size={14} /></button>
            <ChevronRight size={12} className="text-gray-300" />
            <span className="truncate">{currentPath || "/"}</span>
            <button onClick={goUp} className="ml-auto rounded-lg bg-gray-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:bg-gray-200">
              Up
            </button>
          </div>
        </div>

        <div className="max-h-[400px] overflow-y-auto p-3">
          <div className="space-y-1">
            {items.map((item) => {
              const isSelected = item.is_dir
                ? selectedPath === item.path || selectedPath === item.path + "/"
                : selectedPath === item.path;
              return (
              <div
                key={item.path}
                className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition hover:bg-gray-50 ${
                  isSelected ? "bg-blue-50 ring-1 ring-blue-300" : ""
                }`}
              >
                {item.is_dir ? (
                  <button
                    onClick={() => void browse(item.path + "/")}
                    className="flex items-center gap-2 truncate text-gray-700 hover:text-blue-600"
                    title="Open folder"
                  >
                    <Folder size={16} className="text-blue-400 shrink-0" />
                    <span className="truncate font-medium">{item.name}</span>
                  </button>
                ) : (
                  <button
                    onClick={() => setSelectedPath(item.path)}
                    className="flex items-center gap-2 truncate text-gray-700 hover:text-blue-600"
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
                          ? "bg-blue-500 text-white"
                          : "bg-gray-100 text-gray-500 hover:bg-blue-100 hover:text-blue-600"
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

        {/* Upload */}
        <div className="border-t border-gray-50 p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-gray-400">Upload Dataset</h3>
          <div className="space-y-2">
            <input
              type="text"
              placeholder="Dataset name"
              value={uploadDatasetName}
              onChange={(e) => setUploadDatasetName(e.target.value)}
              className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400"
            />
            <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-gray-300 bg-gray-50 px-3 py-3 text-xs text-gray-500 hover:border-blue-400 hover:text-blue-500">
              <Upload size={14} />
              {uploadFiles ? `${uploadFiles.length} file(s) selected` : "Select images…"}
              <input type="file" multiple className="hidden" onChange={(e) => setUploadFiles(e.target.files)} />
            </label>
            <button
              onClick={handleUpload}
              disabled={isUploading || !uploadFiles || uploadFiles.length === 0 || !uploadDatasetName.trim()}
              className="w-full rounded-xl bg-blue-500 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400"
            >
              {isUploading ? "Uploading…" : "Upload"}
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
        </div>
      </div>

      {/* Right: Mission config */}
      <div className="space-y-6">
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-bold text-gray-800">Mission Configuration</h2>
          <p className="mt-1 text-xs text-gray-400">Set mission ID and review your selection before proceeding</p>

          <div className="mt-5 space-y-4">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-gray-600">Mission ID</span>
              <input
                value={volId}
                onChange={(e) => setVolId(e.target.value)}
                className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 font-mono text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400/30"
              />
            </label>
          </div>
        </div>

        {/* Selection summary */}
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-bold text-gray-700">Selected Dataset</h3>
          <div className={`mt-3 rounded-xl border px-4 py-3 font-mono text-sm ${
            selectedPath ? "border-blue-200 bg-blue-50 text-blue-700" : "border-gray-100 bg-gray-50 text-gray-400"
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

        {/* Existing missions */}
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h3 className="text-sm font-bold text-gray-700">Previous Missions</h3>
          <p className="mt-1 text-xs text-gray-400">Select an existing mission to view or rerun phases</p>
          <div className="mt-3 max-h-[250px] space-y-1.5 overflow-y-auto">
            {Object.values(missions).sort((a, b) => b.updated_at - a.updated_at).map((m) => (
              <button
                key={m.vol_id}
                onClick={() => { setActiveMissionId(m.vol_id); setVolId(m.vol_id); }}
                className={`flex w-full items-center justify-between rounded-xl border px-4 py-2.5 text-left text-sm transition ${
                  activeMissionId === m.vol_id ? "border-blue-300 bg-blue-50" : "border-gray-100 hover:border-gray-200"
                }`}
              >
                <span className="font-mono font-medium text-gray-700">{m.vol_id}</span>
                <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
                  m.overall_status === "success" ? "bg-emerald-50 text-emerald-600"
                  : m.overall_status === "error" ? "bg-red-50 text-red-600"
                  : "bg-blue-50 text-blue-600"
                }`}>{m.overall_status}</span>
              </button>
            ))}
            {Object.keys(missions).length === 0 && <p className="text-xs text-gray-400">No missions yet</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
