export type ServiceName = "COLMAP" | "TILER" | "IA";
export type PipelineStatus = "processing" | "success" | "error" | "cancelled";
export type PipelineName = "modern" | "legacy";
export type AIBackend = "yolo" | "sam3";
export type YOLOModelVariant =
  | "yolo26l" | "yolo26m" | "yolo26s" | "yolo26n"
  | "yolo11l" | "yolo11m" | "yolo11s" | "yolo11n";
export type ParamValue = string | boolean;
export type ParameterOption = string | {
  value: string;
  label: string;
};
export type PhaseId = "setup" | "reconstruction" | "gaussian" | "detection" | "results";

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

export const AVAILABLE_CLASSES = [
  "bicycle", "car", "motorcycle", "airplane",
  "bus", "truck", "boat",
];

export const AVAILABLE_AI_BACKENDS: Array<{ value: AIBackend; label: string; description: string }> = [
  { value: "yolo", label: "YOLO OBB", description: "Fast oriented-box vehicle detector" },
  { value: "sam3", label: "SAM 3", description: "Prompted mask segmentation from Meta" },
];

export const AVAILABLE_YOLO_MODELS: Array<{ value: YOLOModelVariant; label: string; description: string }> = [
  { value: "yolo26l", label: "YOLO26-L", description: "Largest YOLO26 OBB model" },
  { value: "yolo26m", label: "YOLO26-M", description: "Balanced YOLO26 OBB model" },
  { value: "yolo26s", label: "YOLO26-S", description: "Smaller YOLO26 OBB model" },
  { value: "yolo26n", label: "YOLO26-N", description: "Lightest YOLO26 OBB model" },
  { value: "yolo11l", label: "YOLO11-L", description: "Largest YOLO11 OBB model" },
  { value: "yolo11m", label: "YOLO11-M", description: "Balanced YOLO11 OBB model" },
  { value: "yolo11s", label: "YOLO11-S", description: "Smaller YOLO11 OBB model" },
  { value: "yolo11n", label: "YOLO11-N", description: "Lightest YOLO11 OBB model" },
];

// Phase → COLMAP step mapping (for progress tracking)
export const PHASE_STEPS: Record<string, string[]> = {
  reconstruction: [
    "PREPARING", "DOWNLOADING_IMAGES", "COPYING_IMAGES", "GPS_EXTRACTION",
    "FEATURES", "MATCHING", "CALIBRATING", "MAPPING", "UNDISTORT", "ALIGNING",
  ],
  gaussian: ["GAUSS", "UPLOADING"],
  detection: ["TILING_START", "TILING_IN_PROGRESS", "TILING_DONE", "DETECTING", "AGGREGATING_DETECTIONS"],
};
