import type {
  DatasetItem,
  MissionCatalogResponse,
  MissionDetail,
  MissionSummary,
  ParameterConfigResponse,
  PodState,
  StatusPayload,
} from "./types";
import {
  anyOf,
  arrayOf,
  booleanValue,
  decoder,
  integerValue,
  nonEmptyString,
  nullable,
  nullish,
  numberValue,
  objectWith,
  oneOf,
  recordOf,
  recordValue,
  stringValue,
  type Validator,
} from "./contract-decoder";

const stringOrBoolean = anyOf(stringValue, booleanValue);
const qualityProfile = oneOf(
  "fast-v1",
  "normal-v1",
  "high-quality-v1",
  "normal-v2",
  "high-quality-v2",
  "normal-v3",
  "high-quality-v3",
);
const stageId = oneOf(
  "reconstruction",
  "gaussian_training",
  "gaussian_filtering",
  "rasterization",
  "detection",
);

const statusPayload: Validator = objectWith(
  { vol_id: nonEmptyString },
  {
    step: stringValue,
    progress: numberValue,
    status: oneOf("processing", "success", "error", "cancelled"),
    service: stringValue,
    log: stringValue,
    details: recordValue,
  },
);

const missionLog: Validator = objectWith(
  { message: stringValue },
  {
    service: nullish(stringValue),
    step: nullish(stringValue),
    status: nullish(stringValue),
    progress: nullish(numberValue),
    ts: numberValue,
    created_at: nullish(stringValue),
    details: nullish(recordValue),
  },
);

const missionDetailLog: Validator = objectWith({}, {
  service: nullish(stringValue),
  step: nullish(stringValue),
  status: nullish(stringValue),
  progress: nullish(numberValue),
  message: nullish(stringValue),
  created_at: nullish(stringValue),
  details: nullish(recordValue),
});

const stageRun: Validator = objectWith(
  {
    run_id: nonEmptyString,
    stage: stageId,
    attempt: integerValue,
    status: nonEmptyString,
    progress: numberValue,
  },
  {
    current_step: nullish(stringValue),
    executor: nullish(stringValue),
    resource_class: nullish(stringValue),
    job_name: nullish(stringValue),
    dispatch_attempts: integerValue,
    dispatch_error: nullish(stringValue),
    parameters: recordValue,
    upstream_artifact_ids: arrayOf(stringValue),
    provenance: recordValue,
    quality_metrics: recordValue,
    error_message: nullish(stringValue),
    heartbeat_at: nullish(stringValue),
    scheduled_at: nullish(stringValue),
    started_at: nullish(stringValue),
    completed_at: nullish(stringValue),
  },
);

const product: Validator = objectWith(
  { kind: nonEmptyString },
  {
    run_id: stringValue,
    artifact_id: stringValue,
    stage_run_id: stringValue,
    name: stringValue,
    status: stringValue,
    s3_key: nullish(stringValue),
    checksum_sha256: stringValue,
    size_bytes: nullish(numberValue),
    metadata: recordValue,
    parent_artifact_ids: arrayOf(stringValue),
  },
);

const missionSummary: Validator = objectWith(
  {
    vol_id: nonEmptyString,
    services: recordOf(statusPayload),
    logs: arrayOf(missionLog),
    updated_at: numberValue,
    overall_status: nonEmptyString,
  },
  {
    workspace_dir: stringValue,
    workspace_state: nullish(recordValue),
    colmap_resume: objectWith({
      available: booleanValue,
      state: nonEmptyString,
      reason: stringValue,
      downstream_processing: arrayOf(stringValue),
    }),
    status: stringValue,
    current_step: nullish(stringValue),
    progress: numberValue,
    quality_profile: nullish(qualityProfile),
    stage_runs: arrayOf(stageRun),
    parameters: recordValue,
    products: arrayOf(product),
    is_stale: booleanValue,
    last_event_age_seconds: nullish(numberValue),
  },
);

const catalogItem: Validator = objectWith(
  {
    vol_id: nonEmptyString,
    owner_subject: nonEmptyString,
    status: nonEmptyString,
    progress: numberValue,
    pipeline: oneOf("modern", "legacy"),
    attempt_count: integerValue,
    overall_status: nonEmptyString,
    is_stale: booleanValue,
  },
  {
    current_step: nullish(stringValue),
    quality_profile: nullish(qualityProfile),
    created_at: nullish(stringValue),
    updated_at: nullish(stringValue),
    last_event_age_seconds: nullish(numberValue),
  },
);

export const parseMissionSummaryResponse = decoder<{
  missions: MissionSummary[];
  active_vol_id: string | null;
}>(
  "mission summary",
  objectWith({
    missions: arrayOf(missionSummary),
    active_vol_id: nullable(stringValue),
  }),
);

export const parseMissionCatalog = decoder<MissionCatalogResponse>(
  "mission catalog",
  objectWith({
    items: arrayOf(catalogItem),
    total: integerValue,
    limit: integerValue,
    offset: integerValue,
  }),
);

export const parseMissionDetail = decoder<MissionDetail>(
  "mission detail",
  (value, path) => {
    catalogItem(value, path);
    objectWith({
      parameters: recordValue,
      attempts: arrayOf(anyOf(numberValue, recordValue)),
      phases: recordOf(statusPayload),
      heartbeat: objectWith({
        updated_at: nullish(stringValue),
        age_seconds: nullish(numberValue),
        delayed: booleanValue,
      }),
      logs: arrayOf(missionDetailLog),
      products: arrayOf(product),
    }, {
      stage_runs: arrayOf(stageRun),
    })(value, path);
  },
);

export const parsePods = decoder<{
  pods: PodState[];
  error: string | null;
}>(
  "pod status",
  objectWith({
    pods: arrayOf(objectWith({
      name: nonEmptyString,
      phase: nonEmptyString,
      ready: nullable(stringValue),
      restarts: nullable(integerValue),
    }, {
      reason: nullish(stringValue),
      last_terminated_reason: nullish(stringValue),
      last_terminated_exit_code: nullish(integerValue),
      oom_killed: booleanValue,
      memory_limit: nullish(stringValue),
      memory_request: nullish(stringValue),
    })),
    error: nullable(stringValue),
  }),
);

const processConfig = objectWith({
  id: oneOf("map", "facade"),
  label: nonEmptyString,
  description: stringValue,
  stages: arrayOf(oneOf("COLMAP", "TILER", "IA")),
  parameters: recordOf(stringOrBoolean),
}, { profile_id: stringValue });

const qualityProfileConfig = objectWith({
  id: qualityProfile,
  version: integerValue,
  name: nonEmptyString,
  description: stringValue,
  parameters: recordOf(stringOrBoolean),
});

export const parseParameterConfig = decoder<ParameterConfigResponse>(
  "mission parameter catalog",
  objectWith({
    pipelines: objectWith({
      modern: recordOf(stringOrBoolean),
      legacy: recordOf(stringOrBoolean),
    }),
    processes: arrayOf(processConfig),
    metadata: recordOf(objectWith({
      label: nonEmptyString,
      type: oneOf("select", "int", "float", "bool", "text"),
      group: nonEmptyString,
    }, {
      description: stringValue,
      min: numberValue,
      max: numberValue,
      step: numberValue,
      options: arrayOf(anyOf(stringValue, objectWith({
        value: stringValue,
        label: stringValue,
      }))),
    })),
    quality_profiles: arrayOf(qualityProfileConfig),
    quality_profile_default: qualityProfile,
    yolo_models: arrayOf(objectWith({
      id: oneOf(
        "yolo26l", "yolo26m", "yolo26s", "yolo26n",
        "yolo11l", "yolo11m", "yolo11s", "yolo11n",
      ),
      label: nonEmptyString,
      available: booleanValue,
      artifact: nonEmptyString,
      repository: nonEmptyString,
      revision: nonEmptyString,
      artifact_sha256: nonEmptyString,
      classes: arrayOf(stringValue),
      selectable_classes: arrayOf(stringValue),
    })),
    sam3: objectWith({
      model_id: nonEmptyString,
      model_revision: nonEmptyString,
      processor_target_size: integerValue,
      maximum_source_tile_size: integerValue,
      inference_batch_size: integerValue,
      minimum_vram_gib: numberValue,
    }),
    stage_dag: objectWith({
      version: integerValue,
      stages: arrayOf(objectWith({
        id: stageId,
        dependencies: arrayOf(stageId),
      })),
    }),
  }, {
    work_drives: arrayOf(objectWith({
      name: nonEmptyString,
      label: nonEmptyString,
      mount: nonEmptyString,
    })),
    work_drive_default: stringValue,
  }),
);

export const parseDatasetItems = decoder<DatasetItem[]>(
  "dataset browser",
  arrayOf(objectWith({
    name: nonEmptyString,
    path: nonEmptyString,
    is_dir: booleanValue,
    image_count: integerValue,
  })),
);

export const parseStatusPayload = decoder<StatusPayload>(
  "status event",
  statusPayload,
);

export const parseCommandResponse = decoder<{
  status: string;
  message?: string;
  vol_id?: string;
}>(
  "command",
  objectWith({ status: nonEmptyString }, {
    message: stringValue,
    vol_id: stringValue,
  }),
);
