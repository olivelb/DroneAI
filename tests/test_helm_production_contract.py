from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "drone-ai"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_production_overlay_requires_immutable_application_images() -> None:
    values = _read(CHART / "values-production.example.yaml")
    defaults = _read(CHART / "values.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")
    ci = _read(ROOT / ".github" / "workflows" / "ci.yml")

    assert "requireImmutableImages: false" in defaults
    assert "requireImmutableImages: true" in values
    assert values.count('tag: "REPLACE_GIT_SHA"') == 2
    assert 'regexMatch "^[0-9a-f]{7,40}$" $tag' in helpers
    assert 'regexMatch "@sha256:[0-9a-f]{64}$" .image' in helpers
    assert "Mutable application image tag found in the production render" in ci
    assert ci.count("--set-string") >= 8
    assert "${GITHUB_SHA}" in ci


def test_browser_upload_cors_exposes_multipart_etag() -> None:
    defaults = _read(CHART / "values.yaml")
    minio = _read(CHART / "templates" / "minio.yaml")
    compose = _read(ROOT / "compose.test.yaml")
    external_script = _read(ROOT / "scripts" / "deploy" / "configure-s3-upload-cors.sh")

    assert "browserUploadCors:" in defaults
    assert "MINIO_API_CORS_ALLOW_ORIGIN" in minio
    assert 'join "," .Values.minio.browserUploadCors.allowedOrigins' in minio
    assert "mc cors set" not in minio
    assert "MINIO_API_CORS_ALLOW_ORIGIN" in compose
    assert "http://localhost:3000,http://127.0.0.1:3000" in compose
    assert "http://localhost:30000,http://127.0.0.1:30000" in compose
    assert "DRONEAI_MINIO_CORS_ALLOW_ORIGIN" in compose
    assert "mc cors set" not in compose
    assert "PUT" in external_script
    assert "ETag" in external_script
    assert "AllowedOrigin" in external_script


def test_production_api_scale_out_uses_shared_runtime_contracts() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")

    assert "replicaCount: 1" in defaults
    assert "rateLimitBackend: auto" in defaults
    assert "replicaCount: 2" in production
    assert "replicas: {{ .Values.dashboardApi.replicaCount }}" in deployment
    assert "type: RollingUpdate" in deployment
    assert "DRONEAI_TILE_RATE_LIMIT_BACKEND" in deployment
    assert "fieldPath: metadata.name" in deployment


def test_protected_overlays_enable_organization_quota_and_retention_controls() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    preproduction = _read(CHART / "values-ovh-preprod.example.yaml")
    api = _read(CHART / "templates" / "dashboard-api.yaml")
    control = _read(CHART / "templates" / "_control-env.tpl")

    assert "organizationRequestQuotasEnabled: false" in defaults
    for values in (production, preproduction):
        assert "organizationRequestQuotasEnabled: true" in values
    assert "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED" in api
    assert "dashboardApi.saas.organizationRequestQuotasEnabled must be true" in api
    assert "DRONEAI_RETENTION_CLEANUP_SECONDS" in control
    assert "DRONEAI_RETENTION_FAILURE_RETRY_SECONDS" in control


def test_protected_overlays_expose_operational_metrics_and_alert_thresholds() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    preproduction = _read(CHART / "values-ovh-preprod.example.yaml")
    api = _read(CHART / "templates" / "dashboard-api.yaml")
    control_worker = _read(CHART / "templates" / "dashboard-control-worker.yaml")
    alerts = _read(CHART / "templates" / "operational-alerts.yaml")

    assert "metricsEnabled: false" in defaults
    for values in (production, preproduction):
        assert "metricsEnabled: true" in values
    assert "metricsEnabled must be true in staging and production" in api
    for workload in (api, control_worker):
        assert "prometheus.io/scrape" in workload
        assert "DRONEAI_METRICS_ENABLED" in workload
        assert "DRONEAI_METRICS_PORT" in workload
    assert "kind: PrometheusRule" in alerts
    assert "droneai_outbox_oldest_unpublished_age_seconds" in alerts
    assert "droneai_stage_oldest_queued_age_seconds" in alerts
    assert "droneai_s3_operation_failures_total" in alerts


def test_production_identity_uses_database_credentials_and_rotatable_secrets() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    preproduction = _read(CHART / "values-ovh-preprod.example.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")

    assert "databaseAuthEnabled: false" in defaults
    assert "databaseAuthEnabled: true" in production
    assert "allowStaticBootstrap: true" in defaults
    assert "allowStaticBootstrap: false" in production
    assert "allowStaticBootstrap: true" in preproduction
    assert "credentialPepperSecretKey: credential-pepper" in production
    assert "DRONEAI_DATABASE_AUTH_ENABLED" in deployment
    assert "DRONEAI_ALLOW_STATIC_BOOTSTRAP" in deployment
    assert "rateLimitBackend: auto" in defaults
    assert "peerRateLimitPerMinute:" in defaults
    assert "credentialRateLimitPerMinute:" in defaults
    assert "DRONEAI_IDENTITY_RATE_LIMIT_BACKEND" in deployment
    assert "DRONEAI_IDENTITY_PEER_RATE_LIMIT_PER_MINUTE" in deployment
    assert "DRONEAI_IDENTITY_CREDENTIAL_RATE_LIMIT_PER_MINUTE" in deployment
    assert "DRONEAI_CREDENTIAL_PEPPER" in deployment
    assert "optional: true" in deployment


def test_production_api_uses_a_distinct_rls_database_role() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    preproduction = _read(CHART / "values-ovh-preprod.example.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")

    assert "databaseUrlSecretKey: database-url" in defaults
    assert "rowLevelSecurityRequired: false" in defaults
    for values in (production, preproduction):
        assert "databaseUrlSecretKey: api-database-url" in values
        assert "rowLevelSecurityRequired: true" in values
    assert "DRONEAI_RLS_REQUIRED" in deployment
    assert "storage.existingSecret is required" in deployment
    assert "distinct non-owner PostgreSQL role" in deployment
    assert "controlWorker.enabled must remain true" in deployment
    assert ".databaseUrlSecretKey" in helpers
    assert "key: {{ .Values.dashboardApi.databaseUrlSecretKey }}" in deployment



def test_bounded_stage_jobs_are_opt_in_and_have_least_privilege_rbac() -> None:
    defaults = _read(CHART / "values.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")
    control_worker = _read(CHART / "templates" / "dashboard-control-worker.yaml")
    control_env = _read(CHART / "templates" / "_control-env.tpl")

    assert "stageJobs:" in defaults
    assert "enabled: false" in defaults
    assert "databaseUrlSecretKey: stage-database-url" in defaults
    assert "globalConcurrency: 2" in defaults
    assert "perOwnerConcurrency: 1" in defaults
    assert 'resources: ["jobs"]' in control_worker
    assert 'verbs: ["create", "get", "list", "watch", "delete"]' in control_worker
    assert "DRONEAI_STAGE_JOBS_ENABLED" in control_env
    assert "DRONEAI_STAGE_JOBS_ENABLED" in deployment
    assert "credentialSecrets: {}" in defaults
    assert "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON" in control_env
    assert ".Values.stageJobs.databaseUrlSecretKey" in control_env
    assert "stageJobs.databaseUrlSecretKey must identify a non-owner" in deployment
    assert "one distinct Secret per stage" in deployment
    assert "DRONEAI_STAGE_JOB_RUNTIME_CLASS" in control_env
    assert ".Values.gpu.runtimeClassName" in control_env
    assert "DRONEAI_STAGE_WORK_DRIVES_JSON" in control_env
    assert ".Values.colmapWorker.workVolume.drives" in control_env
    assert "DRONEAI_STAGE_WORK_DRIVE_DEFAULT" in control_env
    assert "DRONEAI_STAGE_WORK_EMPTY_DIR_SIZE_LIMIT" in control_env
    assert "DRONEAI_STAGE_HF_TOKEN_SECRET_NAME" in control_env
    assert "DRONEAI_STAGE_HF_TOKEN_SECRET_KEY" in control_env
    assert "DRONEAI_STAGE_SAM3_MODEL_REVISION" in control_env
    assert "artifactSelectiveRestoreEnabled: false" in defaults
    assert "detectionFanout:" in defaults
    assert "DRONEAI_DETECTION_FANOUT_ENABLED" in control_env
    assert "DRONEAI_DETECTION_TILES_PER_SHARD" in control_env
    assert "DRONEAI_DETECTION_SHARD_PARALLELISM" in control_env
    assert "DRONEAI_DETECTION_MAXIMUM_TILES" in control_env
    assert "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED" in control_env
    assert (
        "detectionFanout.enabled requires stageJobs.artifactSelectiveRestoreEnabled=true"
    ) in deployment
    assert "automountServiceAccountToken: false" in deployment


def test_protected_overlays_exclusively_use_complete_bounded_compute() -> None:
    production = _read(CHART / "values-production.example.yaml")
    preproduction = _read(CHART / "values-ovh-preprod.example.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")

    for values in (production, preproduction):
        stage_values = values.split("\nstageJobs:\n", 1)[1]
        assert stage_values.startswith("  enabled: true\n")
        for stage in (
            "reconstruction",
            "gaussian_training",
            "gaussian_filtering",
            "rasterization",
            "detection",
            "gaussian_viewer",
        ):
            assert f"    {stage}:\n" in stage_values
        assert stage_values.count("gpu_architecture: REPLACE_GPU_ARCHITECTURE") == 5
        assert stage_values.count("@sha256:REPLACE_OCI_DIGEST") == 6
        assert "colmapWorker:\n  enabled: false" in values

    assert "stageJobs.enabled must be true in staging and production" in deployment
    assert "colmapWorker.enabled must be false" in deployment
    assert "iaWorker has been retired" in deployment
    assert "processingWorker has been retired" in deployment
    assert "stageJobs.executors.%s.image is required" in deployment
    assert "stageJobs.executors.%s.image must use an OCI digest" in deployment
    assert "Git-SHA tag" not in deployment
    assert "stageJobs.executors.%s.command is required" in deployment
    assert "name: DRONEAI_ENV" in helpers
    assert ".Values.dashboardApi.environment" in helpers


def test_control_worker_is_separate_from_http_api_and_probes_are_meaningful() -> None:
    defaults = _read(CHART / "values.yaml")
    api = _read(CHART / "templates" / "dashboard-api.yaml")
    worker = _read(CHART / "templates" / "dashboard-control-worker.yaml")
    compose = _read(ROOT / "compose.test.yaml")

    assert "controlWorker:" in defaults
    assert "DRONEAI_EMBED_CONTROL_LOOPS" in api
    assert "path: /ready" in api
    assert "path: /live" in api
    assert "dashboard-control-worker-sa" in worker
    assert 'command: ["python", "-m", "app4-dashboard.api.control_worker"]' in worker
    assert "type: RollingUpdate" in worker
    assert "maxUnavailable: 0" in worker
    assert "kind: PodDisruptionBudget" in worker
    assert "DRONEAI_CONTROL_LEADER_ELECTION" in worker
    assert "DRONEAI_CONTROL_LEADER_POLL_SECONDS" in worker
    assert "controlWorker.replicaCount must be at least 2" in api
    assert "controlWorker.leaderElection.enabled must be true" in api
    for protected_values in (
        _read(CHART / "values-production.example.yaml"),
        _read(CHART / "values-ovh-preprod.example.yaml"),
    ):
        assert "controlWorker:\n    replicaCount: 2" in protected_values
        assert "leaderElection:\n      enabled: true" in protected_values
    assert "dashboard-control-worker:" in compose
    assert 'DRONEAI_EMBED_CONTROL_LOOPS: "false"' in compose
    assert "http://localhost:8000/ready" in compose
