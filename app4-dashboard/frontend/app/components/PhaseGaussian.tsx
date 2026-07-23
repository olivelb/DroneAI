"use client";

import React from "react";
import { useStore } from "../lib/store";
import { ParamField } from "./ParamField";
import type { ParameterMeta } from "../lib/types";

const GAUSSIAN_PARAMS = [
  "ortho_mesh_resolution", "gs_iterations", "gs_data_factor", "gs_cap_max", "gs_sh_degree",
  "gs_filter_enabled", "gs_filter_max_scale", "gs_filter_dist", "gs_filter_opacity",
  "gs_filter_needle", "gs_filter_sor", "gs_filter_cc", "gs_filter_z_floater", "gs_filter_sor_sigma",
];

export default function PhaseGaussian() {
  const {
    parameterSchema, parameterValues, updateParameter, activeMission,
  } = useStore();

  const metadata = parameterSchema?.metadata ?? {};
  const colmapSvc = activeMission?.services?.["COLMAP"];
  const hasReconData = colmapSvc && (colmapSvc.progress ?? 0) >= 70;

  const trainingParams = GAUSSIAN_PARAMS.filter((k) => !k.startsWith("gs_filter_"));
  const filterParams = GAUSSIAN_PARAMS.filter((k) => k.startsWith("gs_filter_"));

  return (
    <div className="space-y-6">
      {/* Actions */}
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-gray-100 bg-white p-4 shadow-sm">
        <h2 className="mr-auto text-lg font-bold text-gray-800">
          Phase 2 — Gaussian Training & Ortho
        </h2>
      </div>

      {/* Data availability */}
      <div className={`rounded-2xl border p-4 ${hasReconData ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className={`inline-block h-2 w-2 rounded-full ${hasReconData ? "bg-emerald-500" : "bg-amber-400"}`} />
          <span className={hasReconData ? "text-emerald-700" : "text-amber-700"}>
            {hasReconData
              ? "Undistorted images available — ready for Gaussian training"
              : "Run Phase 1 (Reconstruction) first to produce undistorted images"}
          </span>
        </div>
        {colmapSvc?.step === "GAUSS" && (
          <div className="mt-2">
            <div className="flex items-center justify-between text-xs text-gray-600">
              <span>Gaussian Splatting: {colmapSvc.step}</span>
              <span className="font-bold">{colmapSvc.progress ?? 0}%</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/50">
              <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${colmapSvc.progress ?? 0}%` }} />
            </div>
          </div>
        )}
      </div>

      {/* Training */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">Training Parameters</h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {trainingParams.map((k) => metadata[k] && (
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

      {/* Filters */}
      <div className="rounded-2xl border border-gray-100 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-bold text-gray-700">Gaussian Filters</h3>
        <p className="mb-3 text-xs text-gray-400">Post-training filters for removing floaters, outliers, and noise</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filterParams.map((k) => metadata[k] && (
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
    </div>
  );
}
