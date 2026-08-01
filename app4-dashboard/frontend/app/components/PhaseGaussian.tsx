"use client";

import React from "react";
import {
  Activity,
  Gauge,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import AdvancedParameters from "./AdvancedParameters";
import { ParamField } from "./ParamField";
import { PresetButton } from "./PresetButton";
import StageHeader from "./StageHeader";
import { useStore } from "../lib/store";

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
      "gs_ortho_mip_filter_variance",
      "gs_ortho_mip_filter_compensation",
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
      "gs_qualification_policy",
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
      gs_qualification_policy: "custom",
      gs_iterations: "5000",
      gs_data_factor: "8",
      gs_max_width: "1024",
      gs_ortho_mip_filter_variance: "0.03",
      gs_ortho_mip_filter_compensation: true,
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
      gs_qualification_policy: "DRONEGS_QUALIFICATION_POLICY_V1",
      gs_iterations: "15000",
      gs_data_factor: "4",
      gs_max_width: "1600",
      gs_ortho_mip_filter_variance: "0.03",
      gs_ortho_mip_filter_compensation: true,
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
      gs_canary_min_ssim: "0.25",
    },
  },
  {
    id: "detailed",
    label: "Detailed",
    description: "3,200 px, 3 M Gaussians and compensated ortho filtering for controlled high-detail work.",
    icon: <ShieldCheck size={16} />,
    values: {
      gs_production_profile: "custom",
      gs_qualification_policy: "custom",
      gs_iterations: "30000",
      gs_data_factor: "auto",
      gs_max_width: "3200",
      gs_ortho_mip_filter_variance: "0.03",
      gs_ortho_mip_filter_compensation: true,
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
]);

const QUALIFICATION_KEYS = new Set([
  "gs_canary_min_psnr",
  "gs_canary_min_ssim",
]);

const ESSENTIAL_KEYS = new Set([
  "ortho_mesh_resolution",
  "gs_iterations",
  "gs_max_width",
  "gs_cap_max",
  "gs_checkpoint_every",
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
        const trainingValues = Object.fromEntries(
          Object.entries(production.values).filter(
            ([name]) =>
              name !== "gs_qualification_policy" &&
              !QUALIFICATION_KEYS.has(name),
          ),
        );
        setParameterValues((current) => ({
          ...current,
          ...trainingValues,
        }));
      }
      return;
    }
    if (
      key === "gs_qualification_policy" &&
      value === "DRONEGS_QUALIFICATION_POLICY_V1"
    ) {
      setParameterValues((current) => ({
        ...current,
        gs_qualification_policy: value,
        gs_canary_min_psnr: "18.0",
        gs_canary_min_ssim: "0.25",
      }));
      return;
    }
    updateParameter(key, value);
    if (PRODUCTION_TRAINING_KEYS.has(key)) {
      updateParameter("gs_production_profile", "custom");
    }
    if (QUALIFICATION_KEYS.has(key)) {
      updateParameter("gs_qualification_policy", "custom");
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
  const essentialKeys = [...ESSENTIAL_KEYS].filter((key) => metadata[key]);
  const advancedGroups = PARAMETER_GROUPS.map((group) => ({
    ...group,
    keys: group.keys.filter((key) => !ESSENTIAL_KEYS.has(key)),
  }));

  return (
    <div className="space-y-5">
      <StageHeader
        eyebrow="Étape 03 · Apparence"
        title="DroneGS et orthomosaïque"
        description="Sélectionnez un budget de production, contrôlez la résolution finale et laissez les réglages de recherche accessibles à la demande."
        icon={<Sparkles size={21} />}
        iconClassName="bg-[#fff1cf] text-[#b66b05]"
        status={
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
                {hasReconstruction ? "Modèle sparse prêt" : "Reconstruction requise"}
              </div>
            </div>
          </div>
        }
      />

      {colmapService?.step === "GAUSS" && (
        <section className="rounded-[1.25rem] border border-[#bee2da] bg-[#edf9f6] p-5">
          <div className="flex items-center justify-between text-sm">
            <span className="font-semibold text-[#31504a]">
              Entraînement DroneGS en cours
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

      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div className="surface p-5 sm:p-6">
        <div className="mb-4">
          <div className="eyebrow">DroneGS profiles</div>
          <h3 className="mt-1 text-lg font-bold text-[#26332f]">
            Partir d’un budget opérationnel
          </h3>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-[#77847f]">
            Le profil équilibré reprend la recette de production validée. Les
            profils aperçu et détaillé bornent clairement le coût GPU.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {DRONEGS_PRESETS.map((preset) => (
            <PresetButton
              key={preset.id}
              preset={preset}
              parameterValues={parameterValues}
              layout="stacked"
              tone="amber"
              onApply={(values) =>
                setParameterValues((previous) => ({ ...previous, ...values }))
              }
            />
          ))}
        </div>
        </div>
        <div className="rounded-[1.25rem] border border-[#eadcb9] bg-[#fff9e9] p-5">
          <div className="eyebrow text-[#a76509]">Budget effectif</div>
          <div className="mt-4 space-y-4">
            {([
              ["Itérations", String(parameterValues.gs_iterations ?? "—")],
              ["Largeur d’entraînement", `${String(parameterValues.gs_max_width ?? "—")} px`],
              ["Plafond de Gaussiennes", Number(parameterValues.gs_cap_max || 0).toLocaleString()],
              ["Nettoyages actifs", String(activeFilters)],
            ] satisfies Array<[string, string]>).map(([label, value]) => (
              <div key={label} className="flex items-end justify-between gap-3 border-b border-[#eadfca] pb-3 last:border-0 last:pb-0">
                <span className="text-xs text-[#766f62]">{label}</span>
                <span className="text-sm font-bold text-[#3a352c]">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="surface p-5 sm:p-6">
        <div className="eyebrow">Contrôles essentiels</div>
        <h3 className="mt-1 text-lg font-bold text-[#293632]">
          Sortie, budget et seuils qualité
        </h3>
        <div className="parameter-grid mt-5">
          {essentialKeys.map((key) => (
            <ParamField
              key={key}
              paramKey={key}
              meta={metadata[key]}
              value={parameterValues[key] ?? ""}
              onChange={updateDroneGSParameter}
            />
          ))}
        </div>
      </section>

      <AdvancedParameters
        groups={advancedGroups}
        metadata={metadata}
        values={parameterValues}
        onChange={updateDroneGSParameter}
        description="Capacité, optimiseur, checkpoints, stratégie de test et filtres spatiaux sont regroupés et recherchables."
      />
    </div>
  );
}
