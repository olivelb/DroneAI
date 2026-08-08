"use client";

import React from "react";
import { Cpu, Gauge, HardDrive, ShieldCheck, Zap } from "lucide-react";
import { useMissionRuntime } from "../lib/mission-runtime";
import { useStore } from "../lib/store";
import AdvancedParameters from "./AdvancedParameters";
import { ParamField } from "./ParamField";
import { PresetButton } from "./PresetButton";
import StageHeader from "./StageHeader";

const RECONSTRUCTION_PARAMS = [
  "orthophoto_mode",
  "facade_selection_mode",
  "facade_max_abs_pitch_deg",
  "facade_min_pass_images",
  "facade_target_yaw_deg",
  "facade_yaw_tolerance_deg",
  "facade_scale_mode",
  "facade_meters_per_model_unit",
  "facade_texture_max_incidence_deg",
  "facade_depth_iqr_multiplier",
  "facade_seed_max_reprojection_error",
  "facade_seed_min_track_length",
  "facade_canary_min_psnr",
  "facade_canary_min_ssim",
  "feature_type",
  "feature_max_image_size",
  "feature_num_threads",
  "feature_max_num_features",
  "feature_max_num_matches",
  "sift_first_octave",
  "matcher_type",
  "guided_matching",
  "matching_strategy",
  "gps_pair_max_neighbors",
  "gps_pair_min_neighbors",
  "gps_pair_temporal_neighbors",
  "gps_pair_max_distance_m",
  "camera_model",
  "alignment_engine",
  "use_view_graph_calibrator",
  "imu_gravity_enabled",
  "global_mapper_max_tracks",
  "global_mapper_ba_iterations",
  "global_mapper_ceres_iterations",
  "global_mapper_skip_retriangulation",
  "global_mapper_random_seed",
  "global_mapper_ba_min_track_length",
  "global_mapper_tri_complete_max_reproj_error",
  "global_mapper_tri_merge_max_reproj_error",
  "global_mapper_tri_min_angle",
  "minimum_registration_ratio",
  "mapping_timeout_seconds",
  "projected_crs_mode",
  "projected_crs",
  "rtk_refinement_enabled",
  "rtk_refinement_timeout_seconds",
  "rtk_refinement_iterations",
  "rtk_refinement_loss_scale",
  "rtk_minimum_point_ratio",
  "rtk_maximum_reprojection_degradation_px",
  "rtk_maximum_track_length_loss_ratio",
  "rtk_maximum_focal_length_change_ratio",
  "gcp_adjustment_enabled",
  "gcp_horizontal_accuracy_m",
  "gcp_vertical_accuracy_m",
  "gcp_image_accuracy_px",
  "gcp_robust_loss_scale",
  "gcp_require_checkpoints",
  "gcp_min_checkpoint_count",
  "gcp_max_checkpoint_horizontal_rmse_m",
  "gcp_max_checkpoint_vertical_rmse_m",
  "gcp_max_checkpoint_normalized_error_sigma",
  "gcp_min_adjustment_baseline_m",
  "alignment_max_error",
  "mvs_max_image_size",
  "mvs_num_threads",
];

const RECONSTRUCTION_GROUPS = [
  "Product",
  "Facade",
  "Features",
  "Matching",
  "Mapping",
  "Georeferencing",
  "Undistortion",
];

const GROUP_DESCRIPTIONS: Record<string, string> = {
  Product: "Choose a georeferenced aerial map or an HD facade product in a local frame.",
  Facade: "Image-pass selection and metric scale for a facade; no EPSG or absolute RTK alignment is used.",
  Features: "Working resolution and feature density. Higher values improve fine detail but increase extraction and matching time.",
  Matching: "Controls which image pairs are compared. GPS pairs keep large aerial datasets bounded.",
  Mapping: "Global reconstruction, bundle adjustment, fallback behavior, quality gate, and time budget.",
  Georeferencing: "Projected metric CRS and robust tolerance used to align the reconstruction with GPS, RTK, or PPK camera positions.",
  Undistortion: "Maximum image size prepared for the dense and orthomosaic stages.",
};

const ESSENTIAL_KEYS = new Set([
  "orthophoto_mode",
  "facade_selection_mode",
  "facade_excluded_image_ranges",
  "facade_scale_mode",
  "facade_meters_per_model_unit",
  "feature_max_image_size",
  "global_mapper_ba_iterations",
  "global_mapper_skip_retriangulation",
  "mvs_max_image_size",
  "projected_crs_mode",
  "projected_crs",
  "gcp_adjustment_enabled",
]);

const HIDDEN_IN_FACADE = new Set([
  "projected_crs_mode", "projected_crs", "rtk_refinement_enabled",
  "rtk_refinement_timeout_seconds", "rtk_refinement_iterations",
  "rtk_refinement_loss_scale", "rtk_minimum_point_ratio",
  "rtk_maximum_reprojection_degradation_px", "rtk_maximum_track_length_loss_ratio",
  "rtk_maximum_focal_length_change_ratio", "gcp_adjustment_enabled",
  "gcp_horizontal_accuracy_m", "gcp_vertical_accuracy_m", "gcp_image_accuracy_px",
  "gcp_robust_loss_scale", "gcp_require_checkpoints", "gcp_min_checkpoint_count",
  "gcp_max_checkpoint_horizontal_rmse_m", "gcp_max_checkpoint_vertical_rmse_m",
  "gcp_max_checkpoint_normalized_error_sigma", "gcp_min_adjustment_baseline_m",
  "alignment_max_error", "imu_gravity_enabled",
]);

const isTrue = (value: unknown) =>
  value === true || String(value).trim().toLowerCase() === "true";

const ALIGNMENT_PRESETS = [
  {
    id: "fast",
    label: "Rapide · grande mission",
    description: "Profil ALBAGNAC sous une heure, avec une passe BA sans retriangulation finale.",
    icon: <Zap size={16} />,
    values: {
      feature_type: "SIFT",
      feature_max_image_size: "1600",
      feature_max_num_features: "2048",
      sift_first_octave: "-1",
      matcher_type: "STANDARD",
      guided_matching: false,
      matching_strategy: "gps_pairs",
      camera_model: "SIMPLE_RADIAL",
      alignment_engine: "auto",
      use_view_graph_calibrator: true,
      global_mapper_ba_iterations: "1",
      global_mapper_ceres_iterations: "50",
      global_mapper_skip_retriangulation: true,
      global_mapper_random_seed: "42",
      global_mapper_ba_min_track_length: "3",
      global_mapper_tri_complete_max_reproj_error: "15.0",
      global_mapper_tri_merge_max_reproj_error: "15.0",
      global_mapper_tri_min_angle: "1.0",
      mapping_timeout_seconds: "1200",
      mvs_max_image_size: "1600",
      rtk_refinement_enabled: true,
      rtk_refinement_timeout_seconds: "900",
      rtk_refinement_iterations: "25",
      rtk_refinement_loss_scale: "7.82",
    },
  },
  {
    id: "survey",
    label: "Planimétrie · Helenenschacht",
    description: "Profil 2400 px validé sur un Autel et 5 checkpoints ; il priorise le XY, avec une verticale moins précise.",
    icon: <ShieldCheck size={16} />,
    values: {
      feature_type: "SIFT",
      feature_max_image_size: "2400",
      feature_max_num_features: "4096",
      sift_first_octave: "-1",
      matcher_type: "STANDARD",
      guided_matching: false,
      matching_strategy: "gps_pairs",
      camera_model: "SIMPLE_RADIAL",
      alignment_engine: "auto",
      use_view_graph_calibrator: true,
      global_mapper_ba_iterations: "2",
      global_mapper_ceres_iterations: "50",
      global_mapper_skip_retriangulation: false,
      global_mapper_random_seed: "42",
      global_mapper_ba_min_track_length: "3",
      global_mapper_tri_complete_max_reproj_error: "15.0",
      global_mapper_tri_merge_max_reproj_error: "15.0",
      global_mapper_tri_min_angle: "1.0",
      mapping_timeout_seconds: "2400",
      mvs_max_image_size: "2400",
      rtk_refinement_enabled: true,
      rtk_refinement_timeout_seconds: "900",
      rtk_refinement_iterations: "25",
      rtk_refinement_loss_scale: "7.82",
    },
  },
  {
    id: "precision-rtk",
    label: "Précision 3D · RTK",
    description: "Profil 3200 px/8192 SIFT validé sur Helenenschacht : meilleur compromis 3D avec RTK, mais moins précis en planimétrie que le profil 2400 px.",
    icon: <ShieldCheck size={16} />,
    values: {
      feature_type: "SIFT",
      feature_max_image_size: "3200",
      feature_max_num_features: "8192",
      sift_first_octave: "0",
      matcher_type: "STANDARD",
      guided_matching: true,
      matching_strategy: "gps_pairs",
      camera_model: "SIMPLE_RADIAL",
      alignment_engine: "auto",
      use_view_graph_calibrator: true,
      global_mapper_ba_iterations: "2",
      global_mapper_ceres_iterations: "50",
      global_mapper_skip_retriangulation: false,
      global_mapper_random_seed: "42",
      global_mapper_ba_min_track_length: "3",
      global_mapper_tri_complete_max_reproj_error: "15.0",
      global_mapper_tri_merge_max_reproj_error: "15.0",
      global_mapper_tri_min_angle: "1.0",
      mapping_timeout_seconds: "3600",
      mvs_max_image_size: "3200",
      rtk_refinement_enabled: true,
      rtk_refinement_timeout_seconds: "900",
      rtk_refinement_iterations: "25",
      rtk_refinement_loss_scale: "62.56",
    },
  },
] as const;

export default function PhaseReconstruction() {
  const {
    pipeline, setPipeline, parameterSchema,
    parameterValues, updateParameter, setParameterValues,
    workDrive, setWorkDrive,
  } = useStore();
  const { activeMission } = useMissionRuntime();

  const metadata = parameterSchema?.metadata ?? {};
  const processes = parameterSchema?.processes ?? [];
  const workDrives = parameterSchema?.work_drives ?? [];
  const colmapSvc = activeMission?.services?.["COLMAP"];
  const retriangulationEnabled = !isTrue(
    parameterValues.global_mapper_skip_retriangulation,
  );
  const facadeMode = parameterValues.orthophoto_mode === "facade";

  const advancedGroups = RECONSTRUCTION_GROUPS.map((group) => ({
    id: group.toLocaleLowerCase(),
    label: group,
    description: GROUP_DESCRIPTIONS[group],
    keys: RECONSTRUCTION_PARAMS.filter(
      (key) => metadata[key]?.group === group && !ESSENTIAL_KEYS.has(key)
        && !(facadeMode && HIDDEN_IN_FACADE.has(key))
        && (facadeMode || metadata[key]?.group !== "Facade"),
    ),
  }));
  const essentialKeys = [
    "feature_max_image_size",
    "global_mapper_ba_iterations",
    "global_mapper_skip_retriangulation",
    "mvs_max_image_size",
    ...(facadeMode
      ? [
          "facade_selection_mode",
          "facade_excluded_image_ranges",
          "facade_scale_mode",
          ...(parameterValues.facade_scale_mode === "manual"
            ? ["facade_meters_per_model_unit"]
            : []),
        ]
      : ["projected_crs_mode"]),
    ...(!facadeMode && parameterValues.projected_crs_mode === "custom"
      ? ["projected_crs"]
      : []),
  ].filter((key) => metadata[key]);

  const applyProcess = (processId: "map" | "facade") => {
    const process = processes.find((candidate) => candidate.id === processId);
    const pipelineDefaults = parameterSchema?.pipelines[pipeline] ?? {};
    setParameterValues({
      ...pipelineDefaults,
      ...(process?.parameters ?? { orthophoto_mode: processId }),
    });
  };

  return (
    <div className="space-y-5">
      <StageHeader
        eyebrow="Étape 02 · Géométrie"
        title="Reconstruction et alignement"
        description="Choisissez un objectif de production, puis ajustez uniquement les contrôles qui ont un impact direct sur la durée ou la précision."
        icon={<Cpu size={21} />}
        iconClassName="bg-[#e1f3ef] text-[#0f766e]"
        status={
          <div className="flex items-center gap-2 rounded-2xl border border-[#dce5e1] bg-white px-4 py-3">
            <Gauge size={17} className="text-[#0f766e]" />
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wide text-[#8a9692]">
                Moteur actif
              </div>
              <div className="text-sm font-semibold text-[#34413d]">
                {pipeline === "modern" ? "Alignement global rapide" : "Référence legacy"}
              </div>
            </div>
          </div>
        }
      />

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

      <section className="surface p-5 sm:p-6">
        <div className="eyebrow">Processus de production</div>
        <h3 className="mt-1 text-lg font-bold text-[#26332f]">
          Choisir le produit attendu
        </h3>
        <p className="mt-1 text-xs leading-5 text-[#77847f]">
          Le choix configure toute la chaîne et ses étapes terminales. Une
          façade reste dans un repère métrique local et ne lance pas la
          détection cartographique.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {processes.map((process) => {
            const selected = parameterValues.orthophoto_mode === process.id;
            return (
              <button
                key={process.id}
                type="button"
                onClick={() => applyProcess(process.id)}
                aria-pressed={selected}
                className={`rounded-2xl border p-4 text-left transition ${
                  selected
                    ? "border-[#54ad9d] bg-[#edf9f6] shadow-[0_8px_24px_rgba(15,118,110,0.08)]"
                    : "border-[#dce5e1] bg-[#fafcfb] hover:border-[#aac3bb]"
                }`}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="text-sm font-bold text-[#26332f]">
                    {process.label}
                  </span>
                  <span className="rounded-full bg-white px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-[#0f766e]">
                    {process.stages.join(" → ")}
                  </span>
                </span>
                <span className="mt-2 block text-xs leading-5 text-[#6f7d78]">
                  {process.description}
                </span>
                {process.profile_id && (
                  <span className="mt-2 block font-mono text-[9px] text-[#82908b]">
                    Profil validé : {process.profile_id}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </section>

      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="surface p-5 sm:p-6">
          <div className="eyebrow">Profil de production</div>
          <h3 className="mt-1 text-lg font-bold text-[#26332f]">
            {facadeMode ? "Façade HD · couverture qualifiée" : "Relevé précis ou traitement rapide"}
          </h3>
          <p className="mt-1 text-xs leading-5 text-[#77847f]">
            {facadeMode
              ? "Le profil Façade HD privilégie une distribution homogène des points ; les séquences de détail peuvent être exclues avant la densification DroneGS."
              : "Le profil initial cible la planimétrie ; il ne constitue pas une certification universelle. Pour un autre capteur ou un besoin altimétrique, partez du profil rapide et validez sur des checkpoints."}
          </p>
          {facadeMode ? (
            <div className="mt-4 rounded-2xl border border-[#bee2da] bg-white p-4 text-xs leading-5 text-[#60716b]">
              SIFT 4200 px · Caspar · voisinage 48/16 + 6 temporelles ·
              DroneGS 30 000 itérations en 4K · texture ≤ 45°.
            </div>
          ) : (
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
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
          )}
        </div>

        <div className="rounded-[1.25rem] border border-[#bee2da] bg-[#edf9f6] p-5">
          <div className="eyebrow">Résumé effectif</div>
          <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-5">
            {[
              ["Résolution SIFT", `${parameterValues.feature_max_image_size ?? "—"} px`],
              ["Passes BA", String(parameterValues.global_mapper_ba_iterations ?? "—")],
              ["Retriangulation", retriangulationEnabled ? "Activée" : "Ignorée"],
              [
                "CRS",
                facadeMode
                  ? "Repère local (sans CRS)"
                  : parameterValues.projected_crs_mode === "custom"
                  ? String(parameterValues.projected_crs || "EPSG requis")
                  : String(parameterValues.projected_crs_mode ?? "auto-local"),
              ],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-[10px] font-bold uppercase tracking-wide text-[#78908a]">
                  {label}
                </div>
                <div className="mt-1 truncate text-sm font-bold text-[#25332f]" title={value}>
                  {value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="surface p-5 sm:p-6">
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <div className="eyebrow">Contrôles essentiels</div>
            <h3 className="mt-1 text-lg font-bold text-[#293632]">
              Les réglages qui changent vraiment le résultat
            </h3>
          </div>
          <span className="hidden rounded-full bg-[#edf3f1] px-2.5 py-1 text-[10px] font-bold text-[#65736e] sm:inline">
            {essentialKeys.length} réglages
          </span>
        </div>
        <div className="parameter-grid">
          {essentialKeys.map((key) => (
            <ParamField
              key={key}
              paramKey={key}
              meta={metadata[key]}
              value={parameterValues[key] ?? ""}
              onChange={updateParameter}
            />
          ))}
        </div>
      </section>

      <details className="surface">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 sm:px-6">
          <span className="flex items-center gap-3">
            <HardDrive size={16} className="text-[#0f766e]" />
            <span>
              <span className="block text-sm font-bold text-[#2d3a36]">
                Environnement d’exécution
              </span>
              <span className="mt-0.5 block text-xs text-[#77847f]">
                {workDrives.find((drive) => drive.name === workDrive)?.label ??
                  "Stockage automatique"}{" "}
                · {pipeline === "modern" ? "pipeline moderne" : "pipeline legacy"}
              </span>
            </span>
          </span>
          <span className="text-xs font-semibold text-[#0f766e]">Modifier</span>
        </summary>
        <div className="grid gap-5 border-t border-[#e5ebe8] p-5 sm:p-6 lg:grid-cols-2">
          <div>
            <h4 className="text-sm font-bold text-[#34413d]">Disque de travail</h4>
            <div className="mt-3 grid gap-2">
              {workDrives.map((drive) => (
                <button
                  key={drive.name}
                  type="button"
                  onClick={() => setWorkDrive(drive.name)}
                  className={`rounded-xl border px-4 py-3 text-left text-sm font-semibold transition ${
                    workDrive === drive.name
                      ? "border-[#68bfae] bg-[#edf9f6]"
                      : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
                  }`}
                >
                  {drive.label}
                </button>
              ))}
              {workDrives.length === 0 && (
                <p className="text-xs text-[#77847f]">
                  Le meilleur volume disponible sera sélectionné automatiquement.
                </p>
              )}
            </div>
          </div>
          <div>
            <h4 className="text-sm font-bold text-[#34413d]">Famille de moteur</h4>
            <div className="mt-3 grid gap-2">
              {(["modern", "legacy"] as const).map((engine) => (
                <button
                  key={engine}
                  type="button"
                  onClick={() => setPipeline(engine)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    pipeline === engine
                      ? "border-[#68bfae] bg-[#edf9f6]"
                      : "border-[#dce4e1] bg-[#fafcfb] hover:border-[#b8c9c3]"
                  }`}
                >
                  <span className="block text-sm font-bold capitalize text-[#2f3d38]">
                    {engine}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-[#77847f]">
                    {engine === "modern"
                      ? "SIFT CUDA, paires GPS bornées et GLOMAP GPU"
                      : "SIFT haute résolution, paires spatiales et mapper Ceres"}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </details>

      <AdvancedParameters
        groups={advancedGroups}
        metadata={metadata}
        values={parameterValues}
        onChange={updateParameter}
        description="Matching, solveur, RTK, garde-fous et budgets de temps restent disponibles sans encombrer la configuration courante."
      />
    </div>
  );
}
