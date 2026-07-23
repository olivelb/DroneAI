"use client";

import React from "react";
import { Search } from "lucide-react";
import { useStore } from "../lib/store";
import {
  AVAILABLE_AI_BACKENDS, AVAILABLE_YOLO_MODELS, AVAILABLE_CLASSES,
} from "../lib/types";
import type { AIBackend, YOLOModelVariant } from "../lib/types";

export default function PhaseDetection() {
  const {
    aiConfidence, setAiConfidence, aiBackend, setAiBackend,
    aiModelVariant, setAiModelVariant, samPrompt, setSamPrompt,
    selectedClasses, setSelectedClasses, activeMission,
  } = useStore();

  const tilerSvc = activeMission?.services?.["TILER"];
  const iaSvc = activeMission?.services?.["IA"];
  const hasOrtho = tilerSvc?.status === "success" || (tilerSvc?.progress ?? 0) >= 100;

  const toggleClass = (cls: string) => {
    setSelectedClasses(
      selectedClasses.includes(cls)
        ? selectedClasses.filter((c) => c !== cls)
        : [...selectedClasses, cls],
    );
  };

  return (
    <div className="space-y-6">
      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h2 className="mr-auto text-lg font-bold text-gray-800">
          Phase 3 — Tiling & Detection
        </h2>
      </div>

      {/* Availability */}
      <div className={`rounded-2xl border p-4 ${hasOrtho ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className={`inline-block h-2 w-2 rounded-full ${hasOrtho ? "bg-emerald-500" : "bg-amber-400"}`} />
          <span className={hasOrtho ? "text-emerald-700" : "text-amber-700"}>
            {hasOrtho
              ? "Orthomosaic available — ready for tiling & detection"
              : "Run Phase 2 (Gaussian & Ortho) first to produce the orthomosaic"}
          </span>
        </div>
        {iaSvc && (
          <div className="mt-2">
            <div className="flex items-center justify-between text-xs text-gray-600">
              <span>Detection: {iaSvc.step ?? "—"}</span>
              <span className="font-bold">{iaSvc.progress ?? 0}%</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/50">
              <div className={`h-full rounded-full transition-all duration-500 ${
                iaSvc.status === "success" ? "bg-emerald-500" : iaSvc.status === "error" ? "bg-red-500" : "bg-blue-500"
              }`} style={{ width: `${iaSvc.progress ?? 0}%` }} />
            </div>
          </div>
        )}
      </div>

      {/* AI Backend */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">AI Backend</h3>
        <div className="grid grid-cols-2 gap-3">
          {AVAILABLE_AI_BACKENDS.map((b) => (
            <button
              key={b.value}
              onClick={() => setAiBackend(b.value as AIBackend)}
              className={`rounded-xl border px-4 py-3 text-left transition ${
                aiBackend === b.value ? "border-blue-400 bg-blue-50" : "border-gray-100 bg-gray-50 hover:border-gray-200"
              }`}
            >
              <div className="text-sm font-semibold text-gray-800">{b.label}</div>
              <div className="mt-1 text-[11px] text-gray-500">{b.description}</div>
            </button>
          ))}
        </div>
      </div>

      {/* YOLO Model if yolo backend */}
      {aiBackend === "yolo" && (
        <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
          <h3 className="mb-4 text-sm font-bold text-gray-700">YOLO Model</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {AVAILABLE_YOLO_MODELS.map((m) => (
              <button
                key={m.value}
                onClick={() => setAiModelVariant(m.value as YOLOModelVariant)}
                className={`rounded-xl border px-3 py-2 text-left transition ${
                  aiModelVariant === m.value ? "border-blue-400 bg-blue-50" : "border-gray-100 bg-gray-50 hover:border-gray-200"
                }`}
              >
                <div className="text-xs font-semibold text-gray-800">{m.label}</div>
                <div className="mt-0.5 text-[10px] text-gray-500">{m.description}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Confidence */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">Confidence Threshold</h3>
        <div className="flex items-center gap-4">
          <input
            type="range" min="0.1" max="0.9" step="0.05"
            value={aiConfidence}
            onChange={(e) => setAiConfidence(parseFloat(e.target.value))}
            className="flex-1 accent-blue-500"
          />
          <span className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm font-mono font-bold text-gray-700">
            {aiConfidence.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Classes / Prompt */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">
          {aiBackend === "sam3" ? "SAM Prompt" : "Object Classes"}
        </h3>
        {aiBackend === "sam3" ? (
          <div className="flex items-center gap-2">
            <Search size={16} className="text-gray-400" />
            <input
              value={samPrompt}
              onChange={(e) => setSamPrompt(e.target.value)}
              placeholder="e.g. car, vehicle, building"
              className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-blue-400"
            />
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {AVAILABLE_CLASSES.map((cls) => (
              <button
                key={cls}
                onClick={() => toggleClass(cls)}
                className={`rounded-full border px-4 py-1.5 text-xs font-medium transition ${
                  selectedClasses.includes(cls)
                    ? "border-blue-400 bg-blue-50 text-blue-700"
                    : "border-gray-200 bg-white text-gray-600 hover:border-gray-300"
                }`}
              >
                {cls}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
