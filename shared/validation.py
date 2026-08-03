import json
import os
import re
from pathlib import Path
from typing import Any

from shared.pipeline_params import PARAM_OVERRIDE_KEYS, PARAMETER_METADATA
from shared.facade_selection import parse_excluded_basename_ranges
from shared.projected_crs import normalize_epsg

SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
CLASS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
DATASET_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BOOLEAN_STRINGS = {"0", "1", "false", "true", "no", "yes", "off", "on"}


def validate_mission_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not MISSION_ID_RE.fullmatch(normalized):
        raise ValueError(
            "vol_id must contain 3-64 letters, digits, underscores, or hyphens and must start with a letter or digit"
        )
    return normalized


def validate_safe_segment(value: str, *, field_name: str = "path segment") -> str:
    normalized = str(value or "").strip()
    if not SAFE_SEGMENT_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} contains unsupported path characters")
    return normalized


def validate_dataset_prefix(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not normalized.startswith("datasets/"):
        raise ValueError("input_dataset must start with 'datasets/'")
    if normalized.startswith("/") or "//" in normalized:
        raise ValueError("input_dataset must be a normalized S3 prefix")

    parts = normalized.split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("input_dataset contains an unsafe path segment")
    if any(not DATASET_SEGMENT_RE.fullmatch(part) for part in parts[1:]):
        raise ValueError("input_dataset contains unsupported characters")
    if len(normalized) > 512:
        raise ValueError("input_dataset is too long")
    return normalized


def configured_work_drives(raw_value: str | None = None) -> list[dict[str, str]]:
    raw_value = os.getenv("WORK_DRIVES", "") if raw_value is None else raw_value
    if not raw_value:
        return []
    try:
        entries = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []

    drives = []
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not SAFE_SEGMENT_RE.fullmatch(name) or name in names:
            continue
        mount = str(entry.get("mount") or f"/work/{name}").strip()
        if mount != f"/work/{name}":
            continue
        label = " ".join(str(entry.get("label") or name).split())
        if not label or len(label) > 160:
            continue
        names.add(name)
        drives.append({"name": name, "label": label, "mount": mount})
    return drives


def configured_work_drive_names(raw_value: str | None = None) -> set[str]:
    return {entry["name"] for entry in configured_work_drives(raw_value)}


def validate_work_drive(value: str, *, configured_names: set[str] | None = None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    validate_safe_segment(normalized, field_name="work_drive")
    configured_names = configured_work_drive_names() if configured_names is None else configured_names
    if configured_names and normalized not in configured_names:
        raise ValueError(f"work_drive must be one of: {', '.join(sorted(configured_names))}")
    return normalized


def safe_child_path(base: str | Path, child: str, *, field_name: str = "path segment") -> Path:
    safe_child = validate_safe_segment(child, field_name=field_name)
    resolved_base = Path(base).resolve(strict=False)
    candidate = (resolved_base / safe_child).resolve(strict=False)
    if resolved_base not in candidate.parents:
        raise ValueError(f"{field_name} escapes its allowed base directory")
    return candidate


def validate_class_names(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        item = str(value or "").strip()
        if not CLASS_NAME_RE.fullmatch(item):
            raise ValueError("classes contain an unsupported or empty value")
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        raise ValueError("at least one class is required")
    if len(normalized) > 20:
        raise ValueError("at most 20 classes are allowed")
    return normalized


def _validate_numeric_parameter(key: str, value: Any, metadata: dict[str, Any]) -> None:
    parameter_type = metadata.get("type")
    try:
        numeric_value = int(value) if parameter_type == "int" else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a valid {parameter_type}") from exc

    if key == "feature_num_threads" and numeric_value == -1:
        return
    if "min" in metadata and numeric_value < metadata["min"]:
        raise ValueError(f"{key} must be >= {metadata['min']}")
    if "max" in metadata and numeric_value > metadata["max"]:
        raise ValueError(f"{key} must be <= {metadata['max']}")


def _validate_pipeline_parameter(
    key: str,
    value: Any,
    metadata: dict[str, Any],
) -> None:
    parameter_type = metadata.get("type")
    if parameter_type in {"int", "float"}:
        _validate_numeric_parameter(key, value, metadata)
    elif parameter_type == "select":
        options = {str(option) for option in metadata.get("options", [])}
        if str(value) not in options:
            raise ValueError(f"{key} must be one of: {', '.join(map(str, metadata['options']))}")
    elif parameter_type == "bool":
        normalized = str(value).strip().lower()
        if not isinstance(value, bool) and normalized not in BOOLEAN_STRINGS:
            raise ValueError(f"{key} must be a boolean")
    elif key == "projected_crs" and str(value).strip():
        normalize_epsg(str(value))


def validate_pipeline_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    unknown_keys = sorted(set(overrides) - set(PARAM_OVERRIDE_KEYS))
    if unknown_keys:
        raise ValueError(f"unknown COLMAP parameters: {', '.join(unknown_keys)}")

    for key, value in overrides.items():
        _validate_pipeline_parameter(key, value, PARAMETER_METADATA[key])

    target_yaw = str(overrides.get("facade_target_yaw_deg", "")).strip()
    if target_yaw:
        try:
            numeric_yaw = float(target_yaw)
        except ValueError as exc:
            raise ValueError("facade_target_yaw_deg must be blank or numeric") from exc
        if not -360.0 <= numeric_yaw <= 360.0:
            raise ValueError("facade_target_yaw_deg must be between -360 and 360")

    if "facade_excluded_image_ranges" in overrides:
        parse_excluded_basename_ranges(
            str(overrides["facade_excluded_image_ranges"])
        )

    if {
        "gps_pair_min_neighbors",
        "gps_pair_max_neighbors",
    }.issubset(overrides):
        minimum_neighbors = int(overrides["gps_pair_min_neighbors"])
        maximum_neighbors = int(overrides["gps_pair_max_neighbors"])
        if minimum_neighbors > maximum_neighbors:
            raise ValueError("gps_pair_min_neighbors must be <= gps_pair_max_neighbors")

    if (
        str(overrides.get("alignment_engine", "")).lower() == "caspar"
        and str(overrides.get("camera_model", "")).upper() == "OPENCV"
    ):
        raise ValueError("alignment_engine=caspar requires camera_model PINHOLE or SIMPLE_RADIAL")
    if (
        str(overrides.get("orthophoto_mode", "map")).lower() != "facade"
        and str(overrides.get("projected_crs_mode", "")).lower() == "custom"
    ):
        normalize_epsg(str(overrides.get("projected_crs", "")))
    return overrides
