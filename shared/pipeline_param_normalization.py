"""Normalization and merge rules for the pipeline parameter contract."""

from typing import Any

from shared.pipeline_param_catalog import (
    FEATURE_TYPES,
    MATCHER_TYPES,
    PARAM_OVERRIDE_KEYS,
    PIPELINE_DEFAULTS,
    SAM3_BACKEND_ALIASES,
)


def normalize_ai_backend(value: str | None) -> str:
    normalized = str(value or "yolo").strip().lower().replace("_", "-").replace(" ", "-")
    return "sam3" if normalized in SAM3_BACKEND_ALIASES else "yolo"


def normalize_feature_type(value: Any) -> str:
    normalized = str(value or "SIFT").strip().upper().replace("-", "_")
    if normalized in FEATURE_TYPES:
        return normalized
    if normalized in {"ALIKE", "ALIKED", "ALIKED_N16", "ALIKED_N16_ROT"}:
        return "ALIKED_N16ROT"
    return "SIFT"


def normalize_matcher_type(value: Any) -> str:
    normalized = str(value or "STANDARD").strip().upper().replace("-", "_")
    if normalized in MATCHER_TYPES:
        return normalized
    if normalized in {
        "SIFT",
        "SIFT_BRUTEFORCE",
        "ALIKED_BRUTEFORCE",
        "BRUTEFORCE",
        "BRUTE_FORCE",
    }:
        return "STANDARD"
    if normalized in {"SIFT_LIGHTGLUE", "ALIKED_LIGHTGLUE"}:
        return "LIGHTGLUE"
    return "STANDARD"


def coerce_param_value(template_value: Any, value: Any) -> Any:
    if isinstance(template_value, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if value is None:
        return template_value
    return str(value)


def merge_pipeline_params(
    pipeline_mode: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if pipeline_mode != "modern":
        raise ValueError(f"Unsupported pipeline {pipeline_mode!r}; only modern is supported")
    params = dict(PIPELINE_DEFAULTS["modern"])
    for key, value in (overrides or {}).items():
        if key in params and value is not None:
            params[key] = coerce_param_value(params[key], value)
    params["feature_type"] = normalize_feature_type(params.get("feature_type"))
    params["matcher_type"] = normalize_matcher_type(params.get("matcher_type"))
    return params


def merge_mission_pipeline_params(
    pipeline_mode: str,
    mission_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mission_params = mission_params or {}
    merged = merge_pipeline_params(pipeline_mode, mission_params.get("colmap_params"))
    top_level_overrides = {
        key: mission_params[key]
        for key in PARAM_OVERRIDE_KEYS
        if key in mission_params and mission_params[key] is not None
    }
    return merge_pipeline_params(pipeline_mode, {**merged, **top_level_overrides})
