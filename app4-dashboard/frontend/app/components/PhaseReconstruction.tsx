"use client";

import React from "react";
import { Cpu, Gauge, HardDrive, ShieldCheck, Zap } from "lucide-react";
import { useStore } from "../lib/store";
import { ParamField } from "./ParamField";
import { PresetButton } from "./PresetButton";
import type { ParameterMeta } from "../lib/types";

const RECONSTRUCTION_PARAMS = [
  "feature_type",
  "feature_max_image_size",
  "feature_num_threads",
  "feature_max_num_features",
  "matcher_type",
  "matching_strategy",
  "gps_pair_max_neighbors",
  "gps_pair_min_neighbors",
  "gps_pair_temporal_neighbors",
  "gps_pair_max_distance_m",
  "camera_model",
  "alignment_engine",
  "use_view_graph_calibrator",
  "read_orientation",
  "global_mapper_max_tracks",
  "global_mapper_ba_iterations",
  "global_mapper_ceres_iterations",
  "global_mapper_skip_retriangulation",
  "minimum_registration_ratio",
  "mapping_timeout_seconds",
  "projected_crs_mode",
  "projected_crs",
  "rtk_refinement_enabled",
  "rtk_refinement_timeout_seconds",
  "rtk_refinement_iterations",
  "alignment_max_error",
  "mvs_max_image_size",
];

const RECONSTRUCTION_GROUPS = [
  "Features",
  "Matching",
  "Mapping",
  "Georeferencing",
  "Undistortion",
];

const GROUP_DESCRIPTIONS: Record<string, string> = {
  Features: "Working resolution and feature density. Higher values improve fine detail but increase extraction and matching time.",
  Matching: "Controls which image pairs are compared. GPS pairs keep large aerial datasets bounded.",
  Mapping: "Global reconstruction, bundle adjustment, fallback behavior, quality gate, and time budget.",
  Georeferencing: "Projected metric CRS and robust tolerance used to align the reconstruction with GPS, RTK, or PPK camera positions.",
  Undistortion: "Maximum image size prepared for the dense and orthomosaic stages.",
};

const isTrue = (value: unknown) =>
  value === true || String(value).trim().toLowerCase() === "true";

const ALIGNMENT_PRESETS = [
  {
    id: "fast",
    label: "Operational fast",
    description: "Validated ALBAGNAC profile, designed to stay below one hour.",
    icon: <Zap size={16} />,
    values: {
      feature_max_image_size: "1600",
      feature_max_num_features: "2048",
      global_mapper_ba_iterations: "1",
      global_mapper_ceres_iterations: "50",
      global_mapper_skip_retriangulation: true,
      mapping_timeout_seconds: "1200",
      mvs_max_image_size: "1600",
      rtk_refinement_enabled: true,
      rtk_refinement_timeout_seconds: "900",
      rtk_refinement_iterations: "25",
    },
  },
  {
    id: "quality",
    label: "Quality comparison",
    description: "More image detail, two BA passes and final retriangulation.",
    icon: <ShieldCheck size={16} />,
    values: {
      feature_max_image_size: "2400",
      feature_max_num_features: "4096",
      global_mapper_ba_iterations: "2",
      global_mapper_ceres_iterations: "80",
      global_mapper_skip_retriangulation: false,
      mapping_timeout_seconds: "2400",
      mvs_max_image_size: "2400",
      rtk_refinement_enabled: true,
      rtk_refinement_timeout_seconds: "1200",
      rtk_refinement_iterations: "50",
    },
  },
] as const;

export default function PhaseReconstruction() {
  const {
    pipeline, setPipeline, parameterSchema,
    parameterValues, updateParameter, setParameterValues, activeMission,
    workDrive, setWorkDrive,
  } = useStore();

  const metadata = parameterSchema?.metadata ?? {};
  const workDrives = parameterSchema?.work_drives ?? [];
  const colmapSvc = activeMission?.services?.["COLMAP"];
  const retriangulationEnabled = !isTrue(
    parameterValues.global_mapper_skip_retriangulation,
  );

  const groupedParams = RECONSTRUCTION_GROUPS.map((group) => ({
    group,
    keys: RECONSTRUCTION_PARAMS.filter((k) => metadata[k]?.group === group),
  })).filter((g) => g.keys.length > 0);

  return (
    <div className="space-y-6">
      <section className="surface overflow-hidden">
        <div className="flex flex-col gap-5 p-5 sm:p-6 md:flex-row md:items-end md:justify-between">
          <div className="max-w-2xl">
            <div className="eyebrow">Stage 02 · Geometry</div>
            <div className="mt-2 flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#e1f3ef] text-[#0f766e]">
                <Cpu size={21} />
              </span>
              <div>
                <h2 className="text-2xl font-bold tracking-[-0.035em] text-[#17201e]">
                  Reconstruction & alignment
                </h2>
                <p className="mt-1 text-sm leading-6 text-[#6f7c78]">
                  Build a connected camera graph, solve global geometry and
                  align it to DJI GNSS, RTK or PPK positions.
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-2xl border border-[#dce5e1] bg-[#f7faf9] px-4 py-3">
            <Gauge size={17} className="text-[#0f766e]" />
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wide text-[#8a9692]">
                Current objective
              </div>
              <div className="text-sm font-semibold text-[#34413d]">
                {pipeline === "modern" ? "Fast global alignment" : "Legacy reference"}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="surface p-5 sm:p-6">
        <div className="mb-4">
          <div className="eyebrow">Recommended profiles</div>
          <h3 className="mt-1 text-lg font-bold text-[#26332f]">
            Choose the speed/quality tradeoff
          </h3>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {ALIGNMENT_PRESETS.map((preset) => (
            <PresetButton
              key={preset.id}
              preset={preset}
              parameterValues={parameterValues}
              layout="row"
              tone="teal"
              onApply={(values) =>
                setParameterValues((previous) => ({ ...previous, ...values }))
              }
            />
          ))}
        </div>
      </section>

      {/* Work Drive selector */}
      {workDrives.length > 0 && (
        <section className="surface p-5 sm:p-6">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-bold text-[#34413d]">
            <HardDrive size={15} /> Work Drive
          </h3>
          <p className="mb-3 text-xs text-[#788580]">
            Choose where COLMAP temporary files are stored during processing.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {workDrives.map((d) => (
              <button
                key={d.name}
                onClick={() => setWorkDrive(d.name)}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  workDrive === d.name
                    ? "border-[#68bfae] bg-[#edf9f6]"
                    : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
                }`}
              >
                <div className="text-sm font-semibold text-[#34413d]">{d.label}</div>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Pipeline mode */}
      <section className="surface p-5 sm:p-6">
        <div className="eyebrow">Geometry engine family</div>
        <h3 className="mb-4 mt-1 text-lg font-bold text-[#293632]">
          Pipeline foundation
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {(["modern", "legacy"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setPipeline(p)}
              className={`rounded-2xl border px-4 py-4 text-left transition ${
                pipeline === p
                  ? "border-[#68bfae] bg-[#edf9f6]"
                  : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
              }`}
            >
              <div className="text-sm font-bold capitalize text-[#2f3d38]">{p}</div>
              <div className="mt-1 text-[11px] leading-5 text-[#77847f]">
                {p === "modern"
                  ? "SIFT CUDA + bounded GPS pairs + GLOMAP GPU"
                  : "High-resolution SIFT + spatial pairs + Ceres mapper"}
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* Effective fast-alignment summary */}
      <section className="rounded-[1.25rem] border border-[#bee2da] bg-[#edf9f6] p-5">
        <div className="eyebrow">
          Effective alignment profile
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
          <div>
            <div className="text-xs text-[#6f7c78]">Feature resolution</div>
            <div className="font-bold text-[#25332f]">
              {parameterValues.feature_max_image_size ?? "—"} px
            </div>
          </div>
          <div>
            <div className="text-xs text-[#6f7c78]">Global BA passes</div>
            <div className="font-bold text-[#25332f]">
              {parameterValues.global_mapper_ba_iterations ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-xs text-[#6f7c78]">Retriangulation</div>
            <div className="font-bold text-[#25332f]">
              {retriangulationEnabled ? "Enabled" : "Skipped"}
            </div>
          </div>
          <div>
            <div className="text-xs text-[#6f7c78]">Mapping budget</div>
            <div className="font-bold text-[#25332f]">
              {parameterValues.mapping_timeout_seconds ?? "—"} s
            </div>
          </div>
          <div>
            <div className="text-xs text-[#6f7c78]">Projected CRS</div>
            <div className="font-bold text-[#25332f]">
              {parameterValues.projected_crs_mode === "custom"
                ? parameterValues.projected_crs || "Missing EPSG"
                : parameterValues.projected_crs_mode ?? "auto-local"}
            </div>
          </div>
        </div>
      </section>

      {/* Status indicator */}
      {colmapSvc && (
        <div className={`rounded-2xl border p-4 ${
          colmapSvc.status === "success" ? "border-emerald-200 bg-emerald-50"
          : colmapSvc.status === "error" ? "border-red-200 bg-red-50"
          : "border-[#bee2da] bg-[#edf9f6]"
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
        <details
          key={group}
          className="surface group"
          open={group === "Features" || group === "Mapping" ? true : undefined}
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-5 sm:px-6">
            <span>
              <span className="block text-base font-bold text-[#2d3a36]">{group}</span>
              <span className="mt-1 block text-xs leading-5 text-[#77847f]">
                {GROUP_DESCRIPTIONS[group]}
              </span>
            </span>
            <span className="rounded-full bg-[#edf3f1] px-2.5 py-1 text-[10px] font-bold text-[#5d6b66]">
              {keys.length} controls
            </span>
          </summary>
          <div className="parameter-grid border-t border-[#e5ebe8] px-5 py-5 sm:px-6">
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
        </details>
      ))}
    </div>
  );
}
