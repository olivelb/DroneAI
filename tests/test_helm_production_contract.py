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
    assert values.count('tag: "REPLACE_GIT_SHA"') == 5
    assert 'regexMatch "^[0-9a-f]{7,40}$" $tag' in helpers
    assert 'regexMatch "@sha256:[0-9a-f]{64}$" .image' in helpers
    assert "Mutable application image tag found in the production render" in ci
    assert ci.count("--set-string") >= 8
    assert "${GITHUB_SHA}" in ci


def test_browser_upload_cors_exposes_multipart_etag() -> None:
    defaults = _read(CHART / "values.yaml")
    minio = _read(CHART / "templates" / "minio.yaml")
    compose = _read(ROOT / "compose.local.yaml")
    external_script = _read(
        ROOT / "scripts" / "deploy" / "configure-s3-upload-cors.sh"
    )

    assert "browserUploadCors:" in defaults
    assert "MINIO_API_CORS_ALLOW_ORIGIN" in minio
    assert 'join \",\" .Values.minio.browserUploadCors.allowedOrigins' in minio
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


def test_production_identity_uses_database_credentials_and_rotatable_secrets() -> None:
    defaults = _read(CHART / "values.yaml")
    production = _read(CHART / "values-production.example.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")

    assert "databaseAuthEnabled: false" in defaults
    assert "databaseAuthEnabled: true" in production
    assert "credentialPepperSecretKey: credential-pepper" in production
    assert "DRONEAI_DATABASE_AUTH_ENABLED" in deployment
    assert "DRONEAI_CREDENTIAL_PEPPER" in deployment
    assert "optional: true" in deployment


def test_tile_result_size_limit_is_shared_by_producer_and_consumer() -> None:
    defaults = _read(CHART / "values.yaml")
    ia_deployment = _read(CHART / "templates" / "ia-worker.yaml")
    processing_deployment = _read(CHART / "templates" / "processing-worker.yaml")
    compose = _read(ROOT / "compose.local.yaml")

    assert 'maximumBytes: "10485760"' in defaults
    assert ".Values.tileResults.maximumBytes" in ia_deployment
    assert ".Values.tileResults.maximumBytes" in processing_deployment
    assert compose.count('ANALYSIS_MAX_TILE_RESULT_BYTES: "10485760"') == 2


def test_bounded_stage_jobs_are_opt_in_and_have_least_privilege_rbac() -> None:
    defaults = _read(CHART / "values.yaml")
    deployment = _read(CHART / "templates" / "dashboard-api.yaml")
    control_worker = _read(CHART / "templates" / "dashboard-control-worker.yaml")
    control_env = _read(CHART / "templates" / "_control-env.tpl")

    assert "stageJobs:" in defaults
    assert "enabled: false" in defaults
    assert "globalConcurrency: 2" in defaults
    assert "perOwnerConcurrency: 1" in defaults
    assert 'resources: ["jobs"]' in control_worker
    assert 'verbs: ["create", "get", "list", "watch", "delete"]' in control_worker
    assert "DRONEAI_STAGE_JOBS_ENABLED" in control_env
    assert "credentialSecrets: {}" in defaults
    assert "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON" in control_env
    assert "one distinct Secret per stage" in deployment
    assert "DRONEAI_STAGE_JOB_RUNTIME_CLASS" in control_env
    assert ".Values.gpu.runtimeClassName" in control_env
    assert "DRONEAI_STAGE_HF_TOKEN_SECRET_NAME" in control_env
    assert "DRONEAI_STAGE_HF_TOKEN_SECRET_KEY" in control_env
    assert "DRONEAI_STAGE_SAM3_MODEL_REVISION" in control_env
    assert "artifactManifestV2WriteEnabled: false" in defaults
    assert "artifactSelectiveRestoreEnabled: false" in defaults
    assert "detectionFanout:" in defaults
    assert "DRONEAI_DETECTION_FANOUT_ENABLED" in control_env
    assert "DRONEAI_DETECTION_TILES_PER_SHARD" in control_env
    assert "DRONEAI_DETECTION_SHARD_PARALLELISM" in control_env
    assert "DRONEAI_DETECTION_MAXIMUM_TILES" in control_env
    assert "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED" in control_env
    assert (
        "artifactSelectiveRestoreEnabled requires "
        "stageJobs.artifactManifestV2WriteEnabled=true"
    ) in deployment
    assert (
        "detectionFanout.enabled requires "
        "stageJobs.artifactSelectiveRestoreEnabled=true"
    ) in deployment
    assert "automountServiceAccountToken: false" in deployment


def test_control_worker_is_separate_from_http_api_and_probes_are_meaningful() -> None:
    defaults = _read(CHART / "values.yaml")
    api = _read(CHART / "templates" / "dashboard-api.yaml")
    worker = _read(CHART / "templates" / "dashboard-control-worker.yaml")
    compose = _read(ROOT / "compose.local.yaml")

    assert "controlWorker:" in defaults
    assert "DRONEAI_EMBED_CONTROL_LOOPS" in api
    assert "path: /ready" in api
    assert "path: /live" in api
    assert "dashboard-control-worker-sa" in worker
    assert 'command: ["python", "-m", "app4-dashboard.api.control_worker"]' in worker
    assert "type: Recreate" in worker
    assert "dashboard-control-worker:" in compose
    assert 'DRONEAI_EMBED_CONTROL_LOOPS: "false"' in compose
    assert "http://localhost:8000/ready" in compose
