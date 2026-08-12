{{/*
Environment shared by the standalone control worker and the legacy embedded
mode. Keep scheduling configuration out of the request-serving API whenever
dashboardApi.controlWorker.enabled is true.
*/}}
{{- define "drone-ai.controlEnv" -}}
- name: DRONEAI_STAGE_JOBS_ENABLED
  value: {{ .Values.stageJobs.enabled | quote }}
- name: DRONEAI_ARTIFACT_MANIFEST_V2_WRITE_ENABLED
  value: {{ .Values.stageJobs.artifactManifestV2WriteEnabled | quote }}
- name: DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED
  value: {{ .Values.stageJobs.artifactSelectiveRestoreEnabled | quote }}
- name: DRONEAI_DETECTION_FANOUT_ENABLED
  value: {{ .Values.stageJobs.detectionFanout.enabled | quote }}
- name: DRONEAI_DETECTION_TILES_PER_SHARD
  value: {{ .Values.stageJobs.detectionFanout.tilesPerShard | quote }}
- name: DRONEAI_DETECTION_SHARD_PARALLELISM
  value: {{ .Values.stageJobs.detectionFanout.parallelism | quote }}
- name: DRONEAI_DETECTION_MAXIMUM_TILES
  value: {{ .Values.stageJobs.detectionFanout.maximumTiles | quote }}
- name: DRONEAI_STAGE_SCHEDULER_POLL_SECONDS
  value: {{ .Values.stageJobs.scheduler.pollSeconds | quote }}
- name: DRONEAI_STAGE_GLOBAL_CONCURRENCY
  value: {{ .Values.stageJobs.scheduler.globalConcurrency | quote }}
- name: DRONEAI_STAGE_OWNER_CONCURRENCY
  value: {{ .Values.stageJobs.scheduler.perOwnerConcurrency | quote }}
- name: DRONEAI_STAGE_MISSION_CONCURRENCY
  value: {{ .Values.stageJobs.scheduler.perMissionConcurrency | quote }}
- name: DRONEAI_STAGE_RESOURCE_CONCURRENCY_JSON
  value: {{ .Values.stageJobs.scheduler.resourceConcurrency | toJson | quote }}
- name: DRONEAI_STAGE_EXECUTORS_JSON
  value: {{ .Values.stageJobs.executors | toJson | quote }}
- name: DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON
  value: {{ .Values.stageJobs.credentialSecrets | toJson | quote }}
- name: DRONEAI_STAGE_JOB_SERVICE_ACCOUNT
  value: {{ .Values.stageJobs.serviceAccountName | quote }}
- name: DRONEAI_STAGE_JOB_ACTIVE_DEADLINE_SECONDS
  value: {{ .Values.stageJobs.activeDeadlineSeconds | quote }}
- name: DRONEAI_STAGE_JOB_TTL_SECONDS_AFTER_FINISHED
  value: {{ .Values.stageJobs.ttlSecondsAfterFinished | quote }}
- name: DRONEAI_STAGE_JOB_RUNTIME_CLASS
  value: {{ .Values.gpu.runtimeClassName | quote }}
- name: DRONEAI_STAGE_WORK_DRIVES_JSON
  value: {{ .Values.colmapWorker.workVolume.drives | toJson | quote }}
- name: DRONEAI_STAGE_WORK_DRIVE_DEFAULT
  value: {{ .Values.colmapWorker.workVolume.default | quote }}
- name: DRONEAI_STAGE_WORK_EMPTY_DIR_SIZE_LIMIT
  value: {{ .Values.colmapWorker.workVolume.sizeLimit | quote }}
- name: DRONEAI_STAGE_HF_TOKEN_SECRET_NAME
  value: {{ .Values.hfToken.existingSecret | quote }}
- name: DRONEAI_STAGE_HF_TOKEN_SECRET_KEY
  value: {{ .Values.hfToken.secretKey | quote }}
- name: DRONEAI_STAGE_SAM3_MODEL_ID
  value: {{ .Values.iaWorker.sam3.repository | quote }}
- name: DRONEAI_STAGE_SAM3_MODEL_REVISION
  value: {{ .Values.iaWorker.sam3.revision | quote }}
- name: DRONEAI_STAGE_STORAGE_SECRET_NAME
  value: {{ include "drone-ai.storageSecretName" . | quote }}
- name: DRONEAI_STAGE_DATABASE_URL_SECRET_KEY
  value: {{ .Values.storage.databaseUrlSecretKey | quote }}
- name: DRONEAI_STAGE_S3_ACCESS_KEY_SECRET_KEY
  value: {{ .Values.storage.accessKeySecretKey | quote }}
- name: DRONEAI_STAGE_S3_SECRET_KEY_SECRET_KEY
  value: {{ .Values.storage.secretKeySecretKey | quote }}
{{- end }}
