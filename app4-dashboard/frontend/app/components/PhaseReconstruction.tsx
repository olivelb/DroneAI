"use client";

import React from "react";
import { Play, RotateCcw, Square, HardDrive } from "lucide-react";
import { useStore } from "../lib/store";
import { postMission, postCancel, postPhaseRerun } from "../lib/api";
import { ParamField } from "./ParamField";
import type { ParameterMeta, ParamValue } from "../lib/types";

const RECONSTRUCTION_PARAMS = [
  "feature_type", "feature_max_image_size", "feature_num_threads", "feature_max_num_features",
  "matcher_type", "mapper_cmd", "use_view_graph_calibrator", "read_orientation", "mvs_max_image_size",
];

const RECONSTRUCTION_GROUPS = ["Features", "Matching", "Mapping", "Undistortion"];

export default function PhaseReconstruction() {
  const {
    volId, selectedPath, pipeline, setPipeline, parameterSchema,
    parameterValues, updateParameter, setLogs, activeMission,
    setActiveMissionId, aiConfidence, aiBackend, aiModelVariant,
    samPrompt, selectedClasses, refreshSummary, workDrive, setWorkDrive,
  } = useStore();

  const metadata = parameterSchema?.metadata ?? {};
  const workDrives = parameterSchema?.work_drives ?? [];
  const colmapSvc = activeMission?.services?.["COLMAP"];
  const isRunning = activeMission?.overall_status === "processing";

  const canRerun = activeMission && (activeMission.overall_status === "success" || activeMission.overall_status === "error");

  const handleRun = async () => {
    setLogs(["[SYSTEM] Starting full pipeline…"]);
    setActiveMissionId(volId);
    const params = {
      vol_id: volId,
      input_dataset: selectedPath,
      epsg: "EPSG:4326",
      camera_model: "PINHOLE",
      pipeline,
      tile_size: 1024,
      ai_confidence: aiConfidence,
      ai_backend: aiBackend,
      ai_model_variant: aiModelVariant,
      sam_prompt: samPrompt.trim() || "car",
      classes: aiBackend === "sam3" ? [samPrompt.trim() || "car"] : selectedClasses,
      colmap_params: parameterValues,
      work_drive: workDrive,
    };
    try {
      await postMission(params);
      setLogs((p) => [...p, `[SYSTEM] Mission ${volId} started.`]);
    } catch (e) {
      setLogs((p) => [...p, `[SYSTEM] Error: ${e}`]);
    }
  };

  const handleRerun = async () => {
    setLogs((p) => [...p, "[SYSTEM] Rerunning reconstruction phase…"]);
    try {
      await postPhaseRerun(volId, "reconstruction", { colmap_params: parameterValues, pipeline });
      setLogs((p) => [...p, "[SYSTEM] Reconstruction rerun requested."]);
      void refreshSummary();
    } catch (e) {
      setLogs((p) => [...p, `[SYSTEM] Rerun error: ${e}`]);
    }
  };

  const handleCancel = async () => {
    try {
      await postCancel(activeMission?.vol_id ?? volId);
      setLogs((p) => [...p, "[SYSTEM] Cancel sent."]);
    } catch (e) {
      setLogs((p) => [...p, `[SYSTEM] Cancel error: ${e}`]);
    }
  };

  const groupedParams = RECONSTRUCTION_GROUPS.map((group) => ({
    group,
    keys: RECONSTRUCTION_PARAMS.filter((k) => metadata[k]?.group === group),
  })).filter((g) => g.keys.length > 0);

  return (
    <div className="space-y-6">
      {/* Actions bar */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h2 className="mr-auto text-lg font-bold text-gray-800">
          Phase 1 — Reconstruction
        </h2>
        {!isRunning && (
          <button
            onClick={handleRun}
            disabled={!selectedPath}
            className="flex items-center gap-2 rounded-xl bg-blue-500 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-600 disabled:bg-gray-200 disabled:text-gray-400"
          >
            <Play size={15} /> Run Full Pipeline
          </button>
        )}
        {canRerun && (
          <button
            onClick={handleRerun}
            className="flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-5 py-2.5 text-sm font-semibold text-blue-600 hover:bg-blue-100"
          >
            <RotateCcw size={15} /> Rerun Reconstruction
          </button>
        )}
        {isRunning && (
          <button
            onClick={handleCancel}
            className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-5 py-2.5 text-sm font-semibold text-red-600 hover:bg-red-100"
          >
            <Square size={15} /> Cancel
          </button>
        )}
      </div>

      {/* Work Drive selector */}
      {workDrives.length > 0 && (
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-bold text-gray-700">
            <HardDrive size={15} /> Work Drive
          </h3>
          <p className="mb-3 text-xs text-gray-500">
            Choose where COLMAP temporary files are stored during processing.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {workDrives.map((d) => (
              <button
                key={d.name}
                onClick={() => setWorkDrive(d.name)}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  workDrive === d.name ? "border-blue-400 bg-blue-50" : "border-gray-100 bg-gray-50 hover:border-gray-200"
                }`}
              >
                <div className="text-sm font-semibold text-gray-800">{d.label}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Pipeline mode */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">Pipeline Preset</h3>
        <div className="grid grid-cols-2 gap-3">
          {(["modern", "legacy"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPipeline(p)}
              className={`rounded-xl border px-4 py-3 text-left transition ${
                pipeline === p ? "border-blue-400 bg-blue-50" : "border-gray-100 bg-gray-50 hover:border-gray-200"
              }`}
            >
              <div className="text-sm font-semibold capitalize text-gray-800">{p}</div>
              <div className="mt-1 text-[11px] text-gray-500">
                {p === "modern" ? "ALIKED + LightGlue + Global Mapper" : "SIFT + Standard + Mapper"}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Status indicator */}
      {colmapSvc && (
        <div className={`rounded-2xl border p-4 ${
          colmapSvc.status === "success" ? "border-emerald-200 bg-emerald-50"
          : colmapSvc.status === "error" ? "border-red-200 bg-red-50"
          : "border-blue-200 bg-blue-50"
        }`}>
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-700">COLMAP: {colmapSvc.step ?? "—"}</span>
            <span className="text-sm font-bold">{colmapSvc.progress ?? 0}%</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/50">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                colmapSvc.status === "success" ? "bg-emerald-500" : colmapSvc.status === "error" ? "bg-red-500" : "bg-blue-500"
              }`}
              style={{ width: `${colmapSvc.progress ?? 0}%` }}
            />
          </div>
        </div>
      )}

      {/* COLMAP Parameters */}
      {groupedParams.map(({ group, keys }) => (
        <div key={group} className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h3 className="mb-4 text-sm font-bold text-gray-700">{group}</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {keys.map((k) => metadata[k] && (
              <ParamField
                key={k}
                paramKey={k}
                meta={metadata[k] as ParameterMeta}
                value={parameterValues[k] ?? ""}
                onChange={updateParameter}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
