export type ServiceName = "COLMAP" | "TILER" | "IA";
export type PipelineStatus = "processing" | "success" | "error" | "cancelled";
export type PipelineName = "modern" | "legacy";
export type AIBackend = "yolo" | "sam3";
export type YOLOModelVariant =
  | "yolo26l" | "yolo26m" | "yolo26s" | "yolo26n"
  | "yolo11l" | "yolo11m" | "yolo11s" | "yolo11n";
export type QualityProfileId = "fast-v1" | "normal-v1" | "high-quality-v1";
export type ParamValue = string | boolean;
export type ParameterOption = string | {
  value: string;
  label: string;
};
export type PhaseId = "setup" | "reconstruction" | "gaussian" | "detection" | "results";
export type MissionStageId =
  | "reconstruction"
  | "gaussian_training"
  | "gaussian_filtering"
  | "rasterization"
  | "detection";

export type StatusPayload = {
  vol_id: string;
  step?: string;
  progress?: number;
  status?: PipelineStatus;
  service?: string;
  log?: string;
  details?: {
    event?: string;
    process?: "map" | "facade";
    terminal?: boolean;
    [key: string]: unknown;
  };
};

export type MissionLog = {
  service?: string;
  step?: string;
  status?: string;
  message: string;
  ts?: number;
};

export type WorkspaceCommandState = {
  step?: string;
  command?: string[];
  started_at?: string;
  finished_at?: string;
  event?: string;
  return_code?: number;
  resume_note?: string;
};

export type WorkspaceCopyProgress = {
  processed?: number;
  total?: number;
  copied?: number;
  skipped?: number;
  updated_at?: string;
  resume_note?: string;
};

export type WorkspaceResumeInfo = {
  mode?: string;
  note?: string;
  resumed_from?: {
    status?: string;
    step?: string;
    progress?: number;
    updated_at?: string;
    last_log?: string | null;
  };
};

export type WorkspaceMissionState = {
  version?: number;
  vol_id?: string;
  workspace_dir?: string;
  mission?: Record<string, unknown>;
  started_at?: string;
  updated_at?: string;
  status?: string;
  step?: string;
  progress?: number;
  last_log?: string | null;
  current_command?: WorkspaceCommandState | null;
  last_command?: WorkspaceCommandState | null;
  copy_progress?: WorkspaceCopyProgress | null;
  resume_info?: WorkspaceResumeInfo | null;
};

export type ColmapResumeState = {
  available: boolean;
  state: "running" | "completed" | "resumable" | "checkpointed" | "cancelled" | "unavailable";
  reason: string;
  downstream_processing: string[];
};

export type MissionSummary = {
  vol_id: string;
  workspace_dir?: string;
  workspace_state?: WorkspaceMissionState | null;
  colmap_resume?: ColmapResumeState;
  services: Record<string, StatusPayload>;
  logs: MissionLog[];
  updated_at: number;
  overall_status: string;
  status?: string;
  current_step?: string | null;
  progress?: number;
  quality_profile?: QualityProfileId | null;
  stage_runs?: MissionStageRun[];
  parameters?: Record<string, unknown>;
  products?: MissionProduct[];
  is_stale?: boolean;
  last_event_age_seconds?: number | null;
};

export type MissionStageRun = {
  run_id: string;
  stage: MissionStageId;
  attempt: number;
  status: string;
  progress: number;
  current_step?: string | null;
  executor?: string | null;
  resource_class?: string | null;
  job_name?: string | null;
  dispatch_attempts?: number;
  dispatch_error?: string | null;
  parameters?: Record<string, unknown>;
  upstream_artifact_ids?: string[];
  provenance?: Record<string, unknown>;
  quality_metrics?: Record<string, unknown>;
  error_message?: string | null;
  heartbeat_at?: string | null;
  scheduled_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type MissionProduct = {
  kind: string;
  run_id?: string;
  artifact_id?: string;
  stage_run_id?: string;
  name?: string;
  status?: string;
  s3_key?: string | null;
  checksum_sha256?: string;
  size_bytes?: number | null;
  metadata?: Record<string, unknown>;
  parent_artifact_ids?: string[];
};

export type MissionCatalogItem = {
  vol_id: string;
  owner_subject: string;
  status: string;
  current_step?: string | null;
  progress: number;
  pipeline: PipelineName;
  quality_profile?: QualityProfileId | null;
  attempt_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  overall_status: string;
  is_stale: boolean;
  last_event_age_seconds?: number | null;
};

export type MissionCatalogResponse = {
  items: MissionCatalogItem[];
  total: number;
  limit: number;
  offset: number;
};

export type MissionDetail = MissionCatalogItem & {
  parameters: Record<string, unknown>;
  attempts: Array<number | Record<string, unknown>>;
  stage_runs?: MissionStageRun[];
  phases: Record<string, StatusPayload>;
  heartbeat: {
    updated_at?: string | null;
    age_seconds?: number | null;
    delayed: boolean;
  };
  logs: Array<{
    service?: string | null;
    step?: string | null;
    status?: string | null;
    progress?: number | null;
    message?: string | null;
    created_at?: string | null;
  }>;
  products: MissionProduct[];
};

export type DatasetItem = {
  name: string;
  path: string;
  is_dir: boolean;
  image_count: number;
};

export type PodState = {
  name: string;
  phase: string;
  ready: string | null;
  restarts: number | null;
  reason?: string | null;
  last_terminated_reason?: string | null;
  last_terminated_exit_code?: number | null;
  oom_killed?: boolean;
  memory_limit?: string | null;
  memory_request?: string | null;
};

export type AnalysisRun = {
  run_id: string;
  vol_id: string;
  name: string;
  description: string;
  color: string;
  tags: string[];
  backend: AIBackend;
  model_variant?: string;
  prompt?: string;
  classes: string[];
  confidence: number;
  tile_size: number;
  persist_results: boolean;
  status: string;
  phase: string;
  progress: number;
  total_tiles: number;
  tiles_completed: number;
  detection_count: number;
  retry_count: number;
  error_message?: string | null;
  result_s3_key?: string | null;
  model_manifest?: {
    schema: string;
    backend: AIBackend;
    identity: {
      repository: string;
      revision: string;
      artifact: string;
      artifact_sha256: string;
    };
    libraries: Record<string, string>;
    runtime: Record<string, unknown>;
    inference: Record<string, unknown>;
  } | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type AnalysisCreate = {
  name: string;
  description: string;
  color: string;
  tags: string[];
  backend: AIBackend;
  model_variant: string;
  prompt: string;
  classes: string[];
  confidence: number;
  tile_size: number;
  persist_results: boolean;
};

export type RasterPalette = "none" | "gray" | "depth" | "terrain" | "viridis";

export type RasterStyleRecipe = {
  bands: number[];
  display_ranges: Array<[number, number] | null>;
  palette: RasterPalette;
  opacity: number;
  stretch: "global-percentile" | "fixed";
};

export type RasterLayerStyle = {
  style_id: string;
  layer: "ortho" | "depth";
  name: string;
  artifact_id?: string | null;
  style: RasterStyleRecipe;
  is_default: boolean;
  version: number;
  created_by: string;
  updated_by: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type RasterMetadata = {
  bounds: { wgs84: [number, number, number, number] };
  bands: number;
  min_zoom: number;
  max_zoom: number;
  display_ranges?: Array<[number, number] | null>;
};

export type FeatureBulkAction = "review" | "unreview" | "delete" | "restore";

export type ParameterMeta = {
  label: string;
  description?: string;
  type: "select" | "int" | "float" | "bool" | "text";
  group: string;
  min?: number;
  max?: number;
  step?: number;
  options?: ParameterOption[];
};

export type WorkDrive = {
  name: string;
  label: string;
  mount: string;
};

export type ParameterConfigResponse = {
  pipelines: Record<PipelineName, Record<string, ParamValue>>;
  processes: ProductProcess[];
  metadata: Record<string, ParameterMeta>;
  work_drives?: WorkDrive[];
  work_drive_default?: string;
  quality_profiles: QualityProfile[];
  quality_profile_default: QualityProfileId;
  yolo_models: YoloModelCapability[];
  stage_dag: {
    version: number;
    stages: Array<{
      id: MissionStageId;
      dependencies: MissionStageId[];
    }>;
  };
};

export type QualityProfile = {
  id: QualityProfileId;
  version: number;
  name: string;
  description: string;
  parameters: Record<string, ParamValue>;
};

export type YoloModelCapability = {
  id: YOLOModelVariant;
  label: string;
  available: boolean;
  artifact: string;
  repository: string;
  revision: string;
  artifact_sha256: string;
  classes: string[];
  selectable_classes: string[];
};

export type ProductProcess = {
  id: "map" | "facade";
  label: string;
  description: string;
  stages: ServiceName[];
  profile_id?: string;
  parameters: Record<string, ParamValue>;
};

export const SERVICE_ORDER: ServiceName[] = ["COLMAP", "TILER", "IA"];

export const serviceOrderFor = (
  services: Record<string, StatusPayload>,
): ServiceName[] =>
  services.COLMAP?.details?.process === "facade"
    && services.COLMAP.details.terminal === true
    ? ["COLMAP"]
    : SERVICE_ORDER;

export const overallStatusFor = (
  services: Record<string, StatusPayload>,
): PipelineStatus => {
  const statuses = Object.values(services).map(
    (service) => service.status ?? "processing",
  );
  if (statuses.includes("error")) return "error";
  if (statuses.includes("cancelled")) return "cancelled";
  const requiredServices = serviceOrderFor(services);
  return requiredServices.every(
    (service) => services[service]?.status === "success",
  )
    ? "success"
    : "processing";
};

export const AVAILABLE_AI_BACKENDS: Array<{ value: AIBackend; label: string; description: string }> = [
  { value: "yolo", label: "YOLO OBB", description: "Fast oriented-box vehicle detector" },
  { value: "sam3", label: "SAM 3", description: "Prompted mask segmentation from Meta" },
];

// Phase → COLMAP step mapping (for progress tracking)
export const PHASE_STEPS: Record<string, string[]> = {
  reconstruction: [
    "PREPARING", "DOWNLOADING_IMAGES", "COPYING_IMAGES", "GPS_EXTRACTION",
    "FEATURES", "MATCHING", "CALIBRATING", "MAPPING", "UNDISTORT", "ALIGNING",
  ],
  gaussian: ["GAUSS", "ORTHO", "UPLOADING", "DONE"],
  detection: ["TILING_START", "TILING_IN_PROGRESS", "TILING_DONE", "DETECTING", "AGGREGATING_DETECTIONS"],
};

export const missionPhaseStatus = (
  mission: MissionSummary | null,
  phase: "reconstruction" | "gaussian" | "detection",
): string => {
  if (!mission) return "waiting";
  const latestStage = (stage: MissionStageId) =>
    [...(mission.stage_runs ?? [])]
      .filter((run) => run.stage === stage)
      .sort((left, right) => right.attempt - left.attempt)[0];
  const projectedStatus = (run?: MissionStageRun) => {
    if (!run) return "waiting";
    if (run.status === "succeeded") return "success";
    if (run.status === "running") return "processing";
    if (run.status === "failed") return "error";
    return run.status;
  };
  if (mission.stage_runs?.length) {
    if (phase === "reconstruction") {
      return projectedStatus(latestStage("reconstruction"));
    }
    if (phase === "gaussian") {
      const gaussianStages: MissionStageId[] = [
        "gaussian_training",
        "gaussian_filtering",
        "rasterization",
      ];
      const runs = gaussianStages.map(latestStage).filter(Boolean) as MissionStageRun[];
      if (runs.some((run) => run.status === "failed")) return "error";
      if (runs.some((run) => run.status === "running")) return "processing";
      if (runs.every((run) => run.status === "succeeded")) return "success";
      return "waiting";
    }
    return projectedStatus(latestStage("detection"));
  }
  const colmap = mission.services.COLMAP;
  const colmapStep = colmap?.step ?? "";
  if (phase === "reconstruction") {
    if (
      PHASE_STEPS.gaussian.includes(colmapStep) ||
      colmap?.status === "success"
    ) {
      return "success";
    }
    return colmap?.status ?? "waiting";
  }
  if (phase === "gaussian") {
    if (PHASE_STEPS.gaussian.includes(colmapStep)) {
      return colmap?.status ?? "processing";
    }
    return colmap?.status === "success" ? "success" : "waiting";
  }
  return (
    mission.services.IA?.status ??
    mission.services.TILER?.status ??
    "waiting"
  );
};
