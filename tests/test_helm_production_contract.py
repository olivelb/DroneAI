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

    assert "stageJobs:" in defaults
    assert "enabled: false" in defaults
    assert "globalConcurrency: 2" in defaults
    assert "perOwnerConcurrency: 1" in defaults
    assert 'resources: ["jobs"]' in deployment
    assert 'verbs: ["create", "get", "list", "watch", "delete"]' in deployment
    assert "DRONEAI_STAGE_JOBS_ENABLED" in deployment
    assert "automountServiceAccountToken: false" in deployment
