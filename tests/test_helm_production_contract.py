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
    assert 'trustedCidrs: "REPLACE_TRUSTED_PROXY_CIDRS"' in values
    assert 'image: "drone-dashboard-api@sha256:REPLACE_OCI_DIGEST"' in values
    assert 'image: "drone-dashboard-frontend@sha256:REPLACE_OCI_DIGEST"' in values
    assert 'regexMatch "^[0-9a-f]{7,40}$" $tag' in helpers
    assert 'regexMatch "@sha256:[0-9a-f]{64}$" .image' in helpers
    assert "Mutable application image tag found in the production render" in ci
    assert ci.count("--set-string") >= 8
    assert '--set-string kafka.broker="kafka.ci.internal:9093"' in ci
    assert "${CI_OCI_DIGEST}" in ci


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
    assert "alerts:\n    enabled: true" in production
    assert "metricsEnabled must be true in staging and production" in api
    assert "observability.alerts.enabled must be true in production" in api
    assert "kafka.enabled must be false in production" in api
    assert "kafka.broker must identify an explicit external service" in api
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
    assert "DRONEAI_CONTROL_HEALTH_MAX_AGE_SECONDS" in worker
    assert "app4-dashboard.api.control_worker_health" in worker
    assert "livenessProbe:" in worker
    assert "readinessProbe:" in worker
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


def test_helm_requires_digests_even_if_protected_image_guard_is_disabled() -> None:
    import shutil
    import subprocess
    import pytest
    import yaml

    if not shutil.which("helm"):
        pytest.skip("Helm render is also mandatory in the dedicated CI job")
    digest = "sha256:" + "a" * 64
    for overlay in ("values-production.example.yaml", "values-ovh-preprod.example.yaml"):
        args = ["helm", "template", "test", str(CHART), "-f", str(CHART / overlay),
                "--set", "global.requireImmutableImages=false",
                "--set-string", "dashboardApi.proxy.trustedCidrs=10.0.0.0/8",
                "--set-string", f"stageJobs.sam3.artifactSha256={'a' * 64}"]
        if overlay == "values-production.example.yaml":
            args += ["--set-string", "networkPolicy.externalHttpsCidrs[0]=198.51.100.0/24"]
        if overlay == "values-production.example.yaml":
            args += ["--set-string", "kafka.broker=kafka.test:9093"]
        for stage in ("reconstruction", "gaussian_training", "gaussian_filtering",
                      "rasterization", "detection", "gaussian_viewer"):
            args += ["--set-string", f"stageJobs.executors.{stage}.image=registry.test/worker@{digest}"]
        for tag in ("latest", "a" * 7, "a" * 40):
            result = subprocess.run([*args,
                "--set-string", "dashboardApi.image=api",
                "--set-string", f"dashboardApi.tag={tag}",
                "--set-string", f"dashboardFrontend.image=frontend@{digest}"],
                capture_output=True, text=True)
            assert result.returncode != 0
            assert "must use an OCI digest in staging and production" in result.stderr
        result = subprocess.run([*args,
            "--set-string", f"dashboardApi.image=api@{digest}",
            "--set-string", f"dashboardFrontend.image=frontend@{digest}"],
            check=True, capture_output=True, text=True)
        images = []
        for doc in yaml.safe_load_all(result.stdout):
            if doc and doc["kind"] in ("Deployment", "Job"):
                containers = doc["spec"]["template"]["spec"].get("containers", [])
                images.extend(c["image"] for c in containers if c["image"].endswith(digest))
        assert len(images) >= 4


def test_protected_api_requires_narrow_trusted_proxy_cidrs() -> None:
    import shutil
    import subprocess
    import pytest
    import yaml

    if not shutil.which("helm"):
        pytest.skip("Helm render is mandatory in the dedicated CI job")
    digest = "sha256:" + "b" * 64
    base = [
        "helm", "template", "test", str(CHART),
        "-f", str(CHART / "values-production.example.yaml"),
        "--set-string", f"dashboardApi.image=api@{digest}",
        "--set-string", f"dashboardFrontend.image=frontend@{digest}",
        "--set-string", "kafka.broker=kafka.test:9093",
        "--set-string", f"stageJobs.sam3.artifactSha256={'b' * 64}",
        "--set-string", "networkPolicy.externalHttpsCidrs[0]=198.51.100.0/24",
    ]
    for stage in ("reconstruction", "gaussian_training", "gaussian_filtering",
                  "rasterization", "detection", "gaussian_viewer"):
        base += ["--set-string", f"stageJobs.executors.{stage}.image=worker@{digest}"]
    for invalid in ("", "*", "0.0.0.0/0", "::/0", "REPLACE_TRUSTED_PROXY_CIDRS",
                    r"10.0.0.0/8\, 10.1.0.0/16"):
        result = subprocess.run(
            [*base, "--set-string", f"dashboardApi.proxy.trustedCidrs={invalid}"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "must contain explicit comma-separated" in result.stderr

    rendered = subprocess.run(
        [*base, "--set-string",
         r"dashboardApi.proxy.trustedCidrs=10.0.0.0/16\,10.1.2.3"],
        check=True, capture_output=True, text=True,
    )
    documents = [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]
    api = next(doc for doc in documents if doc["kind"] == "Deployment"
               and doc["metadata"]["name"] == "dashboard-api")
    container = next(item for item in api["spec"]["template"]["spec"]["containers"]
                     if item["name"] == "api")
    assert container["command"] == ["uvicorn"]
    assert container["args"][-3:] == [
        "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/16,10.1.2.3",
    ]


def test_protected_network_policies_default_deny_and_allow_only_required_ports() -> None:
    import shutil
    import subprocess
    import pytest
    import yaml

    if not shutil.which("helm"):
        pytest.skip("Helm render is mandatory in the dedicated CI job")
    digest = "sha256:" + "c" * 64
    args = [
        "helm", "template", "test", str(CHART),
        "-f", str(CHART / "values-production.example.yaml"),
        "--set-string", f"dashboardApi.image=api@{digest}",
        "--set-string", f"dashboardFrontend.image=frontend@{digest}",
        "--set-string", "dashboardApi.proxy.trustedCidrs=10.0.0.0/8",
        "--set-string", "kafka.broker=kafka.test:9093",
        "--set-string", f"stageJobs.sam3.artifactSha256={'c' * 64}",
        "--set-string", "networkPolicy.externalHttpsCidrs[0]=198.51.100.0/24",
    ]
    for stage in ("reconstruction", "gaussian_training", "gaussian_filtering",
                  "rasterization", "detection", "gaussian_viewer"):
        args += ["--set-string", f"stageJobs.executors.{stage}.image=worker@{digest}"]
    rendered = subprocess.run(args, check=True, capture_output=True, text=True)
    policies = {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(rendered.stdout)
        if doc and doc["kind"] == "NetworkPolicy"
    }
    assert set(policies) == {
        "drone-ai-app-default-deny", "drone-ai-stage-default-deny",
        "dashboard-frontend-allow", "dashboard-api-allow",
        "dashboard-control-worker-allow", "drone-ai-stage-egress",
    }
    assert policies["drone-ai-app-default-deny"]["spec"]["policyTypes"] == [
        "Ingress", "Egress",
    ]
    assert "ingress" not in policies["drone-ai-stage-default-deny"]["spec"]
    stage_ports = {
        port["port"]
        for rule in policies["drone-ai-stage-egress"]["spec"]["egress"]
        for port in rule["ports"]
    }
    assert stage_ports == {53, 443, 5432}
    assert 9092 not in stage_ports
    api_ingress = policies["dashboard-api-allow"]["spec"]["ingress"]
    assert all(
        rule.get("to", [{}])[0].get("ipBlock", {}).get("cidr") != "0.0.0.0/0"
        for policy in policies.values()
        for rule in policy["spec"].get("egress", [])
    )
    api_ports = {
        port["port"]
        for rule in policies["dashboard-api-allow"]["spec"]["egress"]
        for port in rule["ports"]
    }
    assert 9093 in api_ports
    assert api_ingress[0]["from"][0]["namespaceSelector"]["matchLabels"][
        "kubernetes.io/metadata.name"
    ] == "traefik"

    disabled = subprocess.run(
        [*args, "--set", "networkPolicy.enabled=false"],
        capture_output=True, text=True,
    )
    assert disabled.returncode != 0
    assert "networkPolicy.enabled must be true" in disabled.stderr


def test_production_render_rejects_internal_or_implicit_kafka() -> None:
    import shutil
    import subprocess
    import pytest

    if not shutil.which("helm"):
        pytest.skip("Helm render is mandatory in the dedicated CI job")
    digest = "sha256:" + "d" * 64
    base = [
        "helm", "template", "test", str(CHART),
        "-f", str(CHART / "values-production.example.yaml"),
        "--set-string", f"dashboardApi.image=api@{digest}",
        "--set-string", f"dashboardFrontend.image=frontend@{digest}",
        "--set-string", "dashboardApi.proxy.trustedCidrs=10.0.0.0/8",
    ]
    for stage in ("reconstruction", "gaussian_training", "gaussian_filtering",
                  "rasterization", "detection", "gaussian_viewer"):
        base += ["--set-string", f"stageJobs.executors.{stage}.image=worker@{digest}"]

    internal = subprocess.run(
        [*base, "--set", "kafka.enabled=true", "--set-string", "kafka.broker=kafka.test:9093"],
        capture_output=True, text=True,
    )
    assert internal.returncode != 0
    assert "kafka.enabled must be false in production" in internal.stderr

    implicit = subprocess.run(
        [*base, "--set-string", "kafka.broker=REPLACE_EXTERNAL_KAFKA_BROKER"],
        capture_output=True, text=True,
    )
    assert implicit.returncode != 0
    assert "kafka.broker must identify an explicit external service" in implicit.stderr
