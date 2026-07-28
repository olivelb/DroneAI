"use client";

import React from "react";
import { CircleStop, Play, Radio, Route } from "lucide-react";
import { postCancel, postMission } from "../lib/api";
import { useStore } from "../lib/store";

export default function MissionLaunchBar() {
  const {
    volId,
    selectedPath,
    pipeline,
    parameterValues,
    setLogs,
    activeMission,
    setActiveMissionId,
    aiConfidence,
    aiBackend,
    aiModelVariant,
    samPrompt,
    selectedClasses,
    tileSize,
    workDrive,
  } = useStore();

  const isRunning = activeMission?.overall_status === "processing";
  const canLaunch = Boolean(selectedPath && volId.trim()) && !isRunning;

  const launch = async () => {
    setLogs(["[SYSTEM] Preparing the complete DroneAI pipeline…"]);
    setActiveMissionId(volId);
    try {
      await postMission({
        vol_id: volId,
        input_dataset: selectedPath,
        pipeline,
        tile_size: tileSize,
        ai_confidence: aiConfidence,
        ai_backend: aiBackend,
        ai_model_variant: aiModelVariant,
        sam_prompt: samPrompt.trim() || "car",
        classes:
          aiBackend === "sam3"
            ? [samPrompt.trim() || "car"]
            : selectedClasses,
        colmap_params: parameterValues,
        work_drive: workDrive,
      });
      setLogs((previous) => [
        ...previous,
        `[SYSTEM] Mission ${volId} queued with the current end-to-end profile.`,
      ]);
    } catch (error) {
      setLogs((previous) => [
        ...previous,
        `[SYSTEM] Launch failed: ${error}`,
      ]);
    }
  };

  const cancel = async () => {
    try {
      await postCancel(activeMission?.vol_id ?? volId);
      setLogs((previous) => [...previous, "[SYSTEM] Cancellation requested."]);
    } catch (error) {
      setLogs((previous) => [
        ...previous,
        `[SYSTEM] Cancellation failed: ${error}`,
      ]);
    }
  };

  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="hidden min-w-0 items-center gap-2 rounded-xl border border-[#dce5e1] bg-white/80 px-3 py-2 md:flex">
        {isRunning ? (
          <Radio size={15} className="shrink-0 animate-pulse text-emerald-600" />
        ) : (
          <Route size={15} className="shrink-0 text-[#0f766e]" />
        )}
        <div className="min-w-0">
          <div className="truncate font-mono text-xs font-semibold text-[#26332f]">
            {activeMission?.vol_id ?? volId}
          </div>
          <div className="truncate text-[10px] text-[#7a8783]">
            {isRunning
              ? activeMission?.services?.COLMAP?.step ?? "Pipeline running"
              : selectedPath || "Select a dataset to launch"}
          </div>
        </div>
      </div>

      {isRunning ? (
        <button
          type="button"
          onClick={cancel}
          className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 text-sm font-semibold text-red-700 transition hover:bg-red-100"
        >
          <CircleStop size={16} />
          <span className="hidden sm:inline">Stop mission</span>
          <span className="sm:hidden">Stop</span>
        </button>
      ) : (
        <button
          type="button"
          onClick={launch}
          disabled={!canLaunch}
          className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-[#0f766e] px-4 text-sm font-semibold text-white shadow-[0_8px_20px_rgba(15,118,110,0.2)] transition hover:bg-[#115e59] disabled:cursor-not-allowed disabled:bg-[#cfd8d5] disabled:shadow-none"
        >
          <Play size={16} fill="currentColor" />
          <span className="hidden sm:inline">Launch pipeline</span>
          <span className="sm:hidden">Launch</span>
        </button>
      )}
    </div>
  );
}
