"""Durable, lightweight recovery records for resident Gaussian cells."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from gaussian_training.manifest_contract import (
    load_run_manifest,
    validate_run_manifest,
)
from shared.checksums import sha256_file
from shared.json_io import atomic_write_json

from .camera_footprint import NativeImageCrop
from .partition import CellBounds, cell_bounds_from_dict


CELL_RECOVERY_FILENAME = "cell_recovery.json"
CELL_RECOVERY_SCHEMA_VERSION = 1
CELL_SUBSET_CONTRACT = "supported-colmap-observations-v2"
CELL_RECIPE_PREFIX = "droneai-gaussian-cell-recipe-v1"


@dataclass(frozen=True)
class CellRecoveryRecord:
    cell_label: str
    recipe_sha256: str
    dataset_fingerprint: str
    trainer_binary_sha256: str
    point_cloud_sha256: str
    point_cloud_bytes: int
    buffer_bytes: int
    gaussian_count: int
    core_gaussian_count: int
    bounds: CellBounds
    subset_report: dict[str, object]


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def cell_recipe_sha256(
    *,
    source_dataset_fingerprint: str,
    cell_label: str,
    bounds: CellBounds,
    camera_names: list[str],
    image_crops: Mapping[str, NativeImageCrop],
    subset_parameters: Mapping[str, object],
    training_parameters: Mapping[str, object],
) -> str:
    """Bind a cell output to immutable source, selection, and trainer inputs."""

    if not source_dataset_fingerprint or not cell_label:
        raise ValueError("cell recipe requires source identity and label")
    payload: dict[str, object] = {
        "contract": CELL_SUBSET_CONTRACT,
        "source_dataset_fingerprint": source_dataset_fingerprint,
        "cell_label": cell_label,
        "bounds": bounds.as_dict(),
        "camera_names": sorted(set(camera_names)),
        "image_crops": {
            name: asdict(crop)
            for name, crop in sorted(image_crops.items())
        },
        "subset_parameters": dict(subset_parameters),
        "training_parameters": dict(training_parameters),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return f"{CELL_RECIPE_PREFIX}:sha256:{digest}"


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"cell recovery record has invalid {key}")
    return value


def _required_integer(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"cell recovery record has invalid {key}")
    return value


def write_cell_recovery_record(
    output_dir: str | Path,
    *,
    cell_label: str,
    recipe_sha256: str,
    dataset_fingerprint: str,
    trainer_binary_sha256: str,
    buffer_path: str | Path,
    gaussian_count: int,
    core_gaussian_count: int,
    bounds: CellBounds,
    subset_report: Mapping[str, object],
) -> Path:
    """Publish the completion marker only after every qualified file exists."""

    output = Path(output_dir)
    manifest_path = output / "trainer_run.json"
    canary_path = output / "canary_result.json"
    point_cloud_path = output / "point_cloud.ply"
    buffer = Path(buffer_path)
    manifest = load_run_manifest(manifest_path)
    validate_run_manifest(manifest)
    canary = json.loads(canary_path.read_text(encoding="utf-8"))
    if (
        not isinstance(canary, dict)
        or canary.get("status") != "passed"
        or canary.get("failed_metrics")
    ):
        raise ValueError("cell recovery requires a passed quality canary")
    if manifest.get("trainer_binary_sha256") != trainer_binary_sha256:
        raise ValueError("cell recovery trainer identity mismatch")
    if manifest.get("dataset", {}).get("fingerprint") != dataset_fingerprint:
        raise ValueError("cell recovery dataset identity mismatch")
    artifact = manifest.get("artifacts", {}).get("point_cloud.ply", {})
    point_cloud_sha256 = artifact.get("sha256")
    point_cloud_bytes = artifact.get("bytes")
    if (
        not isinstance(point_cloud_sha256, str)
        or len(point_cloud_sha256) != 64
        or not isinstance(point_cloud_bytes, int)
        or isinstance(point_cloud_bytes, bool)
        or point_cloud_bytes < 1
        or point_cloud_path.stat().st_size != point_cloud_bytes
    ):
        raise ValueError("cell recovery point cloud contract is invalid")
    if gaussian_count < 1 or core_gaussian_count < 1:
        raise ValueError("cell recovery Gaussian populations must be positive")
    buffer_bytes = buffer.stat().st_size
    if buffer_bytes < 1:
        raise ValueError("cell recovery buffer is empty")
    payload: dict[str, object] = {
        "schema_version": CELL_RECOVERY_SCHEMA_VERSION,
        "cell_label": cell_label,
        "recipe_sha256": recipe_sha256,
        "dataset_fingerprint": dataset_fingerprint,
        "trainer_binary_sha256": trainer_binary_sha256,
        "trainer_manifest_sha256": sha256_file(manifest_path),
        "canary_sha256": sha256_file(canary_path),
        "point_cloud_sha256": point_cloud_sha256,
        "point_cloud_bytes": point_cloud_bytes,
        "buffer_bytes": buffer_bytes,
        "gaussian_count": gaussian_count,
        "core_gaussian_count": core_gaussian_count,
        "bounds": bounds.as_dict(),
        "subset_report": dict(subset_report),
    }
    path = output / CELL_RECOVERY_FILENAME
    atomic_write_json(path, payload)
    return path


def load_cell_recovery_record(
    output_dir: str | Path,
    *,
    expected_cell_label: str,
    expected_recipe_sha256: str,
    expected_bounds: CellBounds,
    expected_trainer_binary_sha256: str,
) -> CellRecoveryRecord | None:
    """Return a cheap reuse record or force the caller down the full path."""

    output = Path(output_dir)
    try:
        payload = json.loads(
            (output / CELL_RECOVERY_FILENAME).read_text(encoding="utf-8")
        )
        if not isinstance(payload, dict):
            return None
        record = cast(dict[str, Any], payload)
        if (
            record.get("schema_version") != CELL_RECOVERY_SCHEMA_VERSION
            or record.get("cell_label") != expected_cell_label
            or record.get("recipe_sha256") != expected_recipe_sha256
            or record.get("trainer_binary_sha256")
            != expected_trainer_binary_sha256
        ):
            return None
        manifest_path = output / "trainer_run.json"
        canary_path = output / "canary_result.json"
        point_cloud_path = output / "point_cloud.ply"
        buffer_path = output / "buffer.ply"
        if (
            sha256_file(manifest_path)
            != _required_string(record, "trainer_manifest_sha256")
            or sha256_file(canary_path)
            != _required_string(record, "canary_sha256")
        ):
            return None
        manifest = load_run_manifest(manifest_path)
        validate_run_manifest(manifest)
        canary = json.loads(canary_path.read_text(encoding="utf-8"))
        dataset_fingerprint = _required_string(record, "dataset_fingerprint")
        point_cloud_sha256 = _required_string(record, "point_cloud_sha256")
        point_cloud_bytes = _required_integer(record, "point_cloud_bytes")
        buffer_bytes = _required_integer(record, "buffer_bytes")
        artifact = manifest.get("artifacts", {}).get("point_cloud.ply", {})
        if (
            manifest.get("trainer_binary_sha256")
            != expected_trainer_binary_sha256
            or manifest.get("dataset", {}).get("fingerprint")
            != dataset_fingerprint
            or artifact.get("sha256") != point_cloud_sha256
            or artifact.get("bytes") != point_cloud_bytes
            or point_cloud_path.stat().st_size != point_cloud_bytes
            or buffer_path.stat().st_size != buffer_bytes
            or not isinstance(canary, dict)
            or canary.get("status") != "passed"
            or canary.get("failed_metrics")
        ):
            return None
        subset_report = record.get("subset_report")
        if not isinstance(subset_report, dict):
            return None
        bounds = cell_bounds_from_dict(record.get("bounds"))
        gaussian_count = _required_integer(record, "gaussian_count")
        core_gaussian_count = _required_integer(
            record,
            "core_gaussian_count",
        )
        if bounds != expected_bounds or gaussian_count < 1 or core_gaussian_count < 1:
            return None
        return CellRecoveryRecord(
            cell_label=expected_cell_label,
            recipe_sha256=expected_recipe_sha256,
            dataset_fingerprint=dataset_fingerprint,
            trainer_binary_sha256=expected_trainer_binary_sha256,
            point_cloud_sha256=point_cloud_sha256,
            point_cloud_bytes=point_cloud_bytes,
            buffer_bytes=buffer_bytes,
            gaussian_count=gaussian_count,
            core_gaussian_count=core_gaussian_count,
            bounds=bounds,
            subset_report=cast(dict[str, object], subset_report),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
