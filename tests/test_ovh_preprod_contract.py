from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = ROOT / "infra" / "ovh" / "preprod"
CHART = ROOT / "charts" / "drone-ai"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_terraform_defaults_are_cost_bounded_and_non_destructive() -> None:
    main = _read(TERRAFORM / "main.tf")
    variables = _read(TERRAFORM / "variables.tf")
    versions = _read(TERRAFORM / "versions.tf")

    assert 'version = "~> 2.18.0"' in versions
    assert 'plan         = "free"' in main
    assert "count = var.enable_gpu_pool ? 1 : 0" in main
    assert re.search(r"desired_nodes\s*=\s*0", main)
    assert 'default     = false' in variables
    assert "prevent_destroy = true" in main
    assert 'sse_algorithm = "AES256"' in main
    assert 'status = "enabled"' in main


def test_terraform_remote_state_bootstrap_is_encrypted_locked_and_least_privilege() -> None:
    state = _read(TERRAFORM / "state-bootstrap.tf")
    outputs = _read(TERRAFORM / "outputs.tf")
    backend = _read(TERRAFORM / "backend-preprod.s3.tfbackend.example")
    lock_test = _read(ROOT / "scripts" / "deploy" / "test-terraform-backend-lock.sh")

    assert 'resource "ovh_cloud_project_storage" "terraform_state"' in state
    assert 'sse_algorithm = "AES256"' in state
    assert 'status = "enabled"' in state
    assert "prevent_destroy = true" in state
    assert 'resource "ovh_cloud_project_user_s3_policy" "terraform_state"' in state
    assert 'Sid      = "ReadWriteStateWithoutDelete"' in state
    assert 'Sid      = "ManageStateLock"' in state
    assert state.count('"s3:DeleteObject"') == 1
    assert 'key          = "preprod/terraform.tfstate"' in backend
    assert 'region       = "gra"' in backend
    assert "use_lockfile = true" in backend
    assert "encrypt      = true" in backend
    assert outputs.count("sensitive   = true") >= 3
    assert "-lock-timeout=0s" in lock_test
    assert "Error acquiring the state lock" in lock_test


def test_managed_service_bootstrap_accounts_are_scoped_and_sensitive() -> None:
    services = _read(TERRAFORM / "managed-services.tf")
    outputs = _read(TERRAFORM / "outputs.tf")

    assert 'resource "ovh_cloud_project_containerregistry_user" "bootstrap"' in services
    assert 'login        = "droneai-bootstrap"' in services
    assert 'email        = "admin@olembo.fr"' in services
    assert 'resource "ovh_cloud_project_user_s3_policy" "assets"' in services
    assert 'arn:aws:s3:::${ovh_cloud_project_storage.assets.name}/*' in services
    assert "ovh_cloud_project_storage.terraform_state" not in services
    assert 'output "registry_bootstrap_password"' in outputs
    assert 'output "object_storage_access_key_id"' in outputs
    assert 'output "object_storage_secret_access_key"' in outputs
    assert outputs.count("sensitive   = true") >= 6
    assert 'output "object_storage_endpoint"' in outputs
    assert 'https://s3.${lower(var.object_storage_region)}.io.cloud.ovh.net' in outputs
    assert 'output "object_storage_virtual_host"' in outputs


def test_preprod_overlay_requires_immutable_images_and_external_secrets() -> None:
    values = _read(CHART / "values-ovh-preprod.example.yaml")
    issuer = _read(ROOT / "infra" / "kubernetes" / "ovh-preprod" / "cluster-issuer.yaml.example")
    traefik = _read(ROOT / "infra" / "kubernetes" / "ovh-preprod" / "traefik-values.yaml")
    cert_manager = _read(ROOT / "infra" / "kubernetes" / "ovh-preprod" / "cert-manager-values.yaml")

    assert ":latest" not in values
    assert values.count('tag: "REPLACE_GIT_SHA"') == 5
    assert "existingSecret: drone-ai-storage-preprod" in values
    assert "createNamespace: false" in values
    assert "environment: staging" in values
    assert "minio:\n  enabled: false" in values
    assert "postgres:\n  enabled: true" in values
    assert "existingSecret: drone-ai-postgres" in values
    assert "passwordSecretKey: password" in values
    assert "password: \"\"" in values
    assert values.count("@sha256:") >= 2
    assert "processingWorker:\n  tag: \"REPLACE_GIT_SHA\"\n  resources:" in values
    assert "limits:\n      memory: 4Gi\n    requests:\n      memory: 2Gi" in values
    assert "memory: 16Gi" in values
    assert "memory: 32Gi" in values
    assert "droneai-preprod.olembo.fr" in values
    assert "api-droneai-preprod.olembo.fr" in values
    assert "className: traefik" in values
    assert "nginx.ingress.kubernetes.io" not in values
    assert "email: admin@olembo.fr" in issuer
    assert "ingressClassName: traefik" in issuer
    assert "loadbalancer.ovhcloud.com/class: octavia" in traefik
    assert "loadbalancer.ovhcloud.com/flavor: small" in traefik
    assert "dashboard: false" in traefik
    assert "crds:\n  enabled: true\n  keep: true" in cert_manager

    postgres = _read(CHART / "templates" / "postgres.yaml")
    assert "if .Values.postgres.existingSecret" in postgres
    assert "secretKeyRef:" in postgres
    assert ".Values.postgres.passwordSecretKey" in postgres


def test_gpu_workers_are_opt_in_and_external_kafka_is_supported() -> None:
    values = _read(CHART / "values-ovh-preprod.example.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")
    colmap = _read(CHART / "templates" / "colmap-worker.yaml")
    ia = _read(CHART / "templates" / "ia-worker.yaml")
    kafka = _read(CHART / "templates" / "kafka.yaml")
    kafka_topics = _read(CHART / "templates" / "kafka-topics.yaml")

    assert "minio:\n  enabled: false" in values
    assert "colmapWorker:\n  enabled: false" in values
    assert "iaWorker:\n  enabled: false" in values
    assert "if .Values.colmapWorker.enabled" in colmap
    assert "if .Values.iaWorker.enabled" in ia
    assert ".Values.kafka.broker" in helpers
    assert "fsGroup: 1000" in kafka
    assert "fsGroupChangePolicy: OnRootMismatch" in kafka
    assert "subPath: kafka" in kafka
    assert '"helm.sh/hook": post-install,post-upgrade' in kafka_topics
    assert "--create --if-not-exists" in kafka_topics
    assert "range .Values.kafka.topics" in kafka_topics
    for topic in (
        "vols-bruts",
        "images-ortho",
        "pipeline-status",
        "pipeline-control",
        "image-tiles",
        "tile-detections",
        "pipeline-dead-letter",
    ):
        assert f"- {topic}" in _read(CHART / "values.yaml")


def test_ovh_dns_upsert_is_bounded_to_explicit_a_records() -> None:
    script = _read(ROOT / "scripts" / "deploy" / "upsert-ovh-dns-a.sh")

    assert 'fieldType=A&subDomain=${subdomain}' in script
    assert '{fieldType:"A",subDomain:$subdomain,target:$target,ttl:300}' in script
    assert "record_count} -gt 1" in script
    assert '/domain/zone/${zone}/refresh' in script


def test_s3_smoke_test_does_not_persist_credentials() -> None:
    script = _read(ROOT / "scripts" / "deploy" / "test-ovh-s3-assets.sh")

    assert "object_storage_access_key_id" in script
    assert "object_storage_secret_access_key" in script
    assert "docker run --rm -i" in script
    assert "trap cleanup EXIT" in script
    assert "s3 rm" in script
    assert "set -x" not in script


def test_harbor_bootstrap_is_private_pull_only_and_secret_safe() -> None:
    script = _read(ROOT / "scripts" / "deploy" / "bootstrap-harbor-preprod.sh")

    assert '{project_name:$project,metadata:{public:"false"}}' in script
    assert 'resource:"repository",action:"pull"' in script
    assert 'resource:"repository",action:"push"' not in script
    assert "duration:-1" in script
    assert "create secret docker-registry drone-ai-registry" in script
    assert 'unset robot_secret robot_created' in script
    assert "set -x" not in script


def test_preprod_publication_never_implicitly_builds_gpu_images() -> None:
    script = _read(ROOT / "scripts" / "deploy" / "publish-preprod-images.sh")

    assert 'readonly INCLUDE_GPU_IMAGES="${INCLUDE_GPU_IMAGES:-0}"' in script
    assert 'REBUILD_COLMAP_BASE=1 requires INCLUDE_GPU_IMAGES=1' in script
    assert 'GPU publication refused: drone-colmap-base:latest is missing.' in script
    assert 'images=(\n    drone-processing' in script
    assert 'images=(drone-colmap drone-ia "${images[@]}")' in script

    wrapper = _read(ROOT / "scripts" / "deploy" / "publish-ovh-preprod-cpu.sh")
    assert "INCLUDE_GPU_IMAGES=0" in wrapper
    assert "trap cleanup EXIT" in wrapper
    assert "docker logout" in wrapper
    assert "registry_bootstrap_password" in wrapper


def test_cpu_deployment_bootstraps_external_secrets_and_is_atomic() -> None:
    secrets = _read(ROOT / "scripts" / "deploy" / "bootstrap-ovh-preprod-secrets.sh")
    deploy = _read(ROOT / "scripts" / "deploy" / "deploy-ovh-preprod-cpu.sh")

    assert "openssl rand -hex 32" in secrets
    assert "drone-ai-postgres" in secrets
    assert "drone-ai-storage-preprod" in secrets
    assert "drone-ai-api-auth" in secrets
    assert "unset api_key" in secrets
    assert "set -x" not in secrets
    assert "bootstrap-ovh-preprod-secrets.sh" in deploy
    assert "--atomic --wait --wait-for-jobs --timeout 15m" in deploy
    assert "--dry-run=server --hide-secret" in deploy
    assert "processingWorker.tag=${git_sha}" in deploy
    assert "colmapWorker.tag" not in deploy
    assert "iaWorker.tag" not in deploy
