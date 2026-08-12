"""Fail-closed deployment-mode policy shared by API and workers."""

from __future__ import annotations

import os


PROTECTED_ENVIRONMENTS = frozenset({"staging", "production"})


def deployment_environment() -> str:
    return os.getenv("DRONEAI_ENV", "development").strip().lower()


def is_protected_environment() -> bool:
    return deployment_environment() in PROTECTED_ENVIRONMENTS


def configured_stage_jobs_enabled() -> bool:
    raw = os.getenv("DRONEAI_STAGE_JOBS_ENABLED", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError("DRONEAI_STAGE_JOBS_ENABLED must be true or false")
    return raw == "true"


def bounded_stage_jobs_enabled() -> bool:
    enabled = configured_stage_jobs_enabled()
    if is_protected_environment() and not enabled:
        raise RuntimeError(
            "Staging and production require bounded stage Jobs; "
            "fused Kafka compute is development-only"
        )
    return enabled


def assert_fused_compute_allowed(service_name: str) -> None:
    if is_protected_environment():
        raise RuntimeError(
            f"{service_name} fused Kafka worker is development-only and cannot "
            f"run in {deployment_environment()}"
        )
