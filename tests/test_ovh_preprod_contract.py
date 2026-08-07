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


def test_preprod_overlay_requires_immutable_images_and_external_secrets() -> None:
    values = _read(CHART / "values-ovh-preprod.example.yaml")

    assert ":latest" not in values
    assert values.count('tag: "REPLACE_GIT_SHA"') == 5
    assert "existingSecret: drone-ai-storage-preprod" in values
    assert "environment: staging" in values
    assert "minio:\n  enabled: false" in values
    assert "postgres:\n  enabled: false" in values
    assert "memory: 16Gi" in values
    assert "memory: 32Gi" in values
    assert "droneai-preprod.olembo.fr" in values
    assert "api-droneai-preprod.olembo.fr" in values


def test_gpu_workers_are_opt_in_and_external_kafka_is_supported() -> None:
    values = _read(CHART / "values-ovh-preprod.example.yaml")
    helpers = _read(CHART / "templates" / "_helpers.tpl")
    colmap = _read(CHART / "templates" / "colmap-worker.yaml")
    ia = _read(CHART / "templates" / "ia-worker.yaml")

    assert values.count("enabled: false") >= 4
    assert "if .Values.colmapWorker.enabled" in colmap
    assert "if .Values.iaWorker.enabled" in ia
    assert ".Values.kafka.broker" in helpers
