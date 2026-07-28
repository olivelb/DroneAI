"use client";

import React from "react";
import {
  Activity,
  Filter,
  Gauge,
  ShieldCheck,
  Sparkles,
  TimerReset,
  Zap,
} from "lucide-react";
import { ParamField } from "./ParamField";
import { useStore } from "../lib/store";
import type { ParameterMeta } from "../lib/types";

const PARAMETER_GROUPS = [
  {
    id: "output",
    label: "Orthomosaic output",
    description: "Ground sampling and final product resolution.",
    keys: ["ortho_mesh_resolution"],
  },
  {
    id: "training",
    label: "Training budget",
    description: "Core DroneGS image, capacity and appearance settings.",
    keys: [
      "gs_backend",
      "gs_production_profile",
      "gs_iterations",
      "gs_data_factor",
      "gs_max_width",
      "gs_tile_mode",
      "gs_cap_max",
      "gs_sh_degree",
      "gs_seed",
    ],
  },
  {
    id: "schedule",
    label: "Optimizer & schedule",
    description: "Advanced topology, rasterization and photometric schedule.",
    keys: [
      "gs_optimizer_profile",
      "gs_pruning_policy",
      "gs_raster_profile",
      "gs_sh_degree_interval",
      "gs_topology_cooldown",
      "gs_photometric_finish",
      "gs_photometric_mse_percent",
    ],
  },
  {
    id: "reliability",
    label: "Checkpoints & quality gates",
    description: "Recovery cadence, held-out split and production acceptance thresholds.",
    keys: [
      "gs_checkpoint_every",
      "gs_test_every",
      "gs_test_split",
      "gs_test_guard_percent",
      "gs_canary_min_psnr",
      "gs_canary_min_ssim",
    ],
  },
  {
    id: "filters",
    label: "Spatial cleanup",
    description: "Remove floaters, oversized splats, isolated components and statistical outliers.",
    keys: [
      "gs_filter_enabled",
      "gs_filter_max_scale",
      "gs_filter_dist",
      "gs_filter_opacity",
      "gs_filter_needle",
      "gs_filter_sor",
      "gs_filter_cc",
      "gs_filter_z_floater",
      "gs_filter_sor_sigma",
    ],
  },
] as const;

const DRONEGS_PRESETS = [
  {
    id: "preview",
    label: "Preview",
    description: "Fast proof of coverage before spending the full training budget.",
    icon: <Zap size={16} />,
    values: {
      gs_production_profile: "custom",
      gs_iterations: "5000",
      gs_data_factor: "8",
      gs_max_width: "1024",
      gs_tile_mode: "4",
      gs_cap_max: "1000000",
      gs_sh_degree: "1",
      gs_optimizer_profile: "reference-absolute",
      gs_pruning_policy: "spatial-bounds",
      gs_raster_profile: "bounded",
      gs_topology_cooldown: "1000",
      gs_photometric_finish: "1000",
      gs_photometric_mse_percent: "100",
      gs_checkpoint_every: "1000",
      gs_test_every: "8",
      gs_test_split: "modulo",
      gs_test_guard_percent: "0",
    },
  },
  {
    id: "balanced",
    label: "Balanced production",
    description: "Exact Albagnac dev.45 recipe: reference rates, spatial bounds and structural FastGS.",
    icon: <Gauge size={16} />,
    values: {
      gs_production_profile: "DRONEGS_PRODUCTION_PROFILE_V1",
      gs_iterations: "15000",
      gs_data_factor: "4",
      gs_max_width: "1600",
      gs_tile_mode: "4",
      gs_cap_max: "1500000",
      gs_sh_degree: "3",
      gs_optimizer_profile: "reference-absolute",
      gs_pruning_policy: "spatial-bounds",
      gs_raster_profile: "fastgs",
      gs_sh_degree_interval: "1000",
      gs_topology_cooldown: "1000",
      gs_photometric_finish: "1000",
      gs_photometric_mse_percent: "100",
      gs_checkpoint_every: "2000",
      gs_test_every: "8",
      gs_test_split: "modulo",
      gs_test_guard_percent: "0",
      gs_canary_min_psnr: "18.0",
      gs_canary_min_ssim: "0.35",
    },
  },
  {
    id: "detailed",
    label: "Detailed",
    description: "More training and image detail for a controlled high-quality run.",
    icon: <ShieldCheck size={16} />,
    values: {
      gs_production_profile: "custom",
      gs_iterations: "30000",
      gs_data_factor: "2",
      gs_max_width: "2400",
      gs_tile_mode: "4",
      gs_cap_max: "3000000",
      gs_sh_degree: "3",
      gs_checkpoint_every: "2000",
      gs_test_every: "8",
      gs_test_split: "spatial-block",
      gs_test_guard_percent: "25",
      gs_canary_min_psnr: "20.0",
      gs_canary_min_ssim: "0.45",
    },
  },
] as const;

const isTrue = (value: unknown) =>
  value === true || String(value).trim().toLowerCase() === "true";

const PRODUCTION_TRAINING_KEYS = new Set([
  "gs_iterations",
  "gs_data_factor",
  "gs_max_width",
  "gs_tile_mode",
  "gs_cap_max",
  "gs_sh_degree",
  "gs_seed",
  "gs_optimizer_profile",
  "gs_pruning_policy",
  "gs_raster_profile",
  "gs_sh_degree_interval",
  "gs_topology_cooldown",
  "gs_photometric_finish",
  "gs_photometric_mse_percent",
  "gs_checkpoint_every",
  "gs_test_every",
  "gs_test_split",
  "gs_test_guard_percent",
  "gs_canary_min_psnr",
  "gs_canary_min_ssim",
]);

export default function PhaseGaussian() {
  const {
    parameterSchema,
    parameterValues,
    updateParameter,
    setParameterValues,
    activeMission,
  } = useStore();

  const metadata = parameterSchema?.metadata ?? {};
  const updateDroneGSParameter = (key: string, value: string | boolean) => {
    if (
      key === "gs_production_profile" &&
      value === "DRONEGS_PRODUCTION_PROFILE_V1"
    ) {
      const production = DRONEGS_PRESETS.find(
        (preset) => preset.id === "balanced",
      );
      if (production) {
        setParameterValues((current) => ({
          ...current,
          ...production.values,
        }));
      }
      return;
    }
    updateParameter(key, value);
    if (PRODUCTION_TRAINING_KEYS.has(key)) {
      updateParameter("gs_production_profile", "custom");
    }
  };
  const colmapService = activeMission?.services?.COLMAP;
  const hasReconstruction =
    colmapService?.status === "success" ||
    (colmapService?.progress ?? 0) >= 70;
  const activeFilters = [
    "gs_filter_enabled",
    "gs_filter_sor",
    "gs_filter_cc",
    "gs_filter_z_floater",
  ].filter((key) => isTrue(parameterValues[key])).length;

  return (
    <div className="space-y-6">
      <section className="surface overflow-hidden">
        <div className="flex flex-col gap-5 p-5 sm:p-6 md:flex-row md:items-end md:justify-between">
          <div className="max-w-2xl">
            <div className="eyebrow">Stage 03 · Appearance</div>
            <div className="mt-2 flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#fff1cf] text-[#b66b05]">
                <Sparkles size={21} />
              </span>
              <div>
                <h2 className="text-2xl font-bold tracking-[-0.035em] text-[#17201e]">
                  DroneGS & orthomosaic
                </h2>
                <p className="mt-1 text-sm leading-6 text-[#6f7c78]">
                  Train a bounded Gaussian scene, enforce held-out quality gates
                  and render georeferenced raster products.
                </p>
              </div>
            </div>
          </div>
          <div
            className={`flex items-center gap-2 rounded-2xl border px-4 py-3 ${
              hasReconstruction
                ? "border-emerald-200 bg-emerald-50"
                : "border-amber-200 bg-amber-50"
            }`}
          >
            <Activity
              size={17}
              className={hasReconstruction ? "text-emerald-600" : "text-amber-600"}
            />
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wide text-[#7c8884]">
                Input readiness
              </div>
              <div className="text-sm font-semibold text-[#34413d]">
                {hasReconstruction ? "Sparse model ready" : "Waiting for reconstruction"}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="surface p-5 sm:p-6">
        <div className="mb-4">
          <div className="eyebrow">DroneGS profiles</div>
          <h3 className="mt-1 text-lg font-bold text-[#26332f]">
            Start from an operational budget
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-[#77847f]">
            Profiles update the core budget without hiding any parameter. You can
            refine every value below before launching the complete mission.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {DRONEGS_PRESETS.map((preset) => {
            const selected = Object.entries(preset.values).every(
              ([key, value]) => String(parameterValues[key]) === String(value),
            );
            return (
              <button
                key={preset.id}
                type="button"
                onClick={() =>
                  setParameterValues((previous) => ({
                    ...previous,
                    ...preset.values,
                  }))
                }
                className={`min-h-[130px] rounded-2xl border p-4 text-left transition ${
                  selected
                    ? "border-[#e2b557] bg-[#fff8e7] shadow-[0_8px_24px_rgba(180,116,12,0.08)]"
                    : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#c8b986]"
                }`}
              >
                <span
                  className={`flex h-9 w-9 items-center justify-center rounded-xl ${
                    selected
                      ? "bg-[#b66b05] text-white"
                      : "bg-white text-[#7a7568]"
                  }`}
                >
                  {preset.icon}
                </span>
                <span className="mt-3 block text-sm font-bold text-[#2b3834]">
                  {preset.label}
                </span>
                <span className="mt-1 block text-xs leading-5 text-[#75827e]">
                  {preset.description}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {([
          ["Iterations", String(parameterValues.gs_iterations ?? "—")],
          ["Training width", `${String(parameterValues.gs_max_width ?? "—")} px`],
          ["Gaussian cap", Number(parameterValues.gs_cap_max || 0).toLocaleString()],
          ["Cleanup modules", `${activeFilters} active`],
        ] satisfies Array<[string, string]>).map(([label, value]) => (
          <div key={label} className="surface-soft px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wide text-[#8a9692]">
              {label}
            </div>
            <div className="mt-1 text-base font-bold text-[#2a3733]">{value}</div>
          </div>
        ))}
      </section>

      {colmapService?.step === "GAUSS" && (
        <section className="rounded-[1.25rem] border border-[#bee2da] bg-[#edf9f6] p-5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold text-[#31504a]">
              DroneGS training in progress
            </span>
            <span className="font-bold text-[#0f766e]">
              {colmapService.progress ?? 0}%
            </span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/80">
            <div
              className="h-full rounded-full bg-[#0f766e] transition-all duration-500"
              style={{ width: `${colmapService.progress ?? 0}%` }}
            />
          </div>
        </section>
      )}

      {PARAMETER_GROUPS.map((group, index) => {
        const Icon =
          group.id === "filters"
            ? Filter
            : group.id === "reliability"
              ? TimerReset
              : group.id === "schedule"
                ? Activity
                : Sparkles;
        const availableKeys = group.keys.filter((key) => metadata[key]);
        if (availableKeys.length === 0) return null;
        return (
          <details
            key={group.id}
            className="surface"
            open={index < 2 ? true : undefined}
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-5 sm:px-6">
              <span className="flex min-w-0 items-start gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#fff4d9] text-[#a76509]">
                  <Icon size={16} />
                </span>
                <span>
                  <span className="block text-base font-bold text-[#2d3a36]">
                    {group.label}
                  </span>
                  <span className="mt-1 block text-xs leading-5 text-[#77847f]">
                    {group.description}
                  </span>
                </span>
              </span>
              <span className="shrink-0 rounded-full bg-[#edf3f1] px-2.5 py-1 text-[10px] font-bold text-[#5d6b66]">
                {availableKeys.length} controls
              </span>
            </summary>
            <div className="parameter-grid border-t border-[#e5ebe8] px-5 py-5 sm:px-6">
              {availableKeys.map((key) => (
                <ParamField
                  key={key}
                  paramKey={key}
                  meta={metadata[key] as ParameterMeta}
                  value={parameterValues[key] ?? ""}
                  onChange={updateDroneGSParameter}
                />
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}
