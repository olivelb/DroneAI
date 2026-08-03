"""Artifact invalidation and small pipeline filesystem predicates."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable

from . import runtime


def remove_artifact_paths(artifact_paths: Iterable[str]) -> list[str]:
    removed_paths: list[str] = []
    for path in artifact_paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed_paths.append(path)
        elif os.path.exists(path):
            os.remove(path)
            removed_paths.append(path)
    return removed_paths


def remove_rtk_dependent_artifacts(workspace_dir: str, dense_path: str) -> list[str]:
    """Invalidate products whose geometry depends on the selected sparse model."""
    return remove_artifact_paths(
        (
            dense_path,
            os.path.join(workspace_dir, "sparse_geo"),
            os.path.join(workspace_dir, "alignment_transform.json"),
            os.path.join(workspace_dir, "orthomosaic.tif"),
            os.path.join(workspace_dir, "orthomosaic.height.tif"),
            os.path.join(workspace_dir, "gcp_alignment_report.json"),
        )
    )


def _invalidate_artifact_paths(
    artifact_paths: Iterable[str], vol_id: str, reason: str, artifact_kind: str
) -> list[str]:
    removed_paths = remove_artifact_paths(artifact_paths)
    if removed_paths:
        runtime.report_mission_progress(
            vol_id,
            "PREPARING",
            3,
            log=f"{reason} Removed {len(removed_paths)} stale {artifact_kind} artifacts.",
        )
    return removed_paths


def invalidate_pipeline_artifacts(
    clean_images_dir: str,
    workspace_dir: str,
    db_path: str,
    sparse_path: str,
    dense_path: str,
    geo_data_file: str,
    vol_id: str,
    reason: str,
) -> list[str]:
    artifact_paths = [
        db_path,
        f"{db_path}-shm",
        f"{db_path}-wal",
        sparse_path,
        os.path.join(workspace_dir, "sparse_rtk"),
        dense_path,
        os.path.join(workspace_dir, "sparse_geo"),
        os.path.join(workspace_dir, "alignment_transform.json"),
        os.path.join(workspace_dir, "orthomosaic.tif"),
        os.path.join(workspace_dir, "facade_orthophoto.tif"),
        os.path.join(workspace_dir, "facade_orthophoto.height.tif"),
        os.path.join(workspace_dir, "facade_frame.json"),
        os.path.join(workspace_dir, "gps_pairs.txt"),
        os.path.join(workspace_dir, "alignment_pair_graph.json"),
        os.path.join(workspace_dir, "rtk_prior_report.json"),
        os.path.join(workspace_dir, "imu_gravity_report.json"),
        os.path.join(workspace_dir, "gcp_alignment_report.json"),
        os.path.join(workspace_dir, ".colmap_pipeline_config.json"),
        geo_data_file,
        f"{geo_data_file}.crs",
        f"{geo_data_file}.crs.json",
        os.path.join(clean_images_dir, ".colmap_exif_sanitized"),
    ]
    return _invalidate_artifact_paths(artifact_paths, vol_id, reason, "pipeline")


def invalidate_georeferencing_artifacts(
    workspace_dir: str, geo_data_file: str, vol_id: str, reason: str
) -> list[str]:
    artifact_paths = [
        os.path.join(workspace_dir, "sparse_geo"),
        os.path.join(workspace_dir, "alignment_transform.json"),
        os.path.join(workspace_dir, "orthomosaic.tif"),
        os.path.join(workspace_dir, "orthomosaic.height.tif"),
        os.path.join(workspace_dir, "gcp_alignment_report.json"),
        geo_data_file,
        f"{geo_data_file}.crs",
        f"{geo_data_file}.crs.json",
    ]
    return _invalidate_artifact_paths(artifact_paths, vol_id, reason, "georeferencing")


def normalize_gpu_index(raw_value: object, default: str = "0") -> str:
    normalized = str(raw_value if raw_value is not None else default).strip()
    if not normalized or normalized == "-1":
        normalized = default
    visible_devices = [
        token.strip()
        for token in os.getenv("CUDA_VISIBLE_DEVICES", "").split(",")
        if token.strip()
    ]
    if len(visible_devices) == 1:
        return "0"
    if normalized.isdigit() and visible_devices and int(normalized) >= len(visible_devices):
        return "0"
    return normalized


def dense_sparse_model_ready(dense_path: str) -> bool:
    sparse_dir = os.path.join(dense_path, "sparse")
    return all(
        os.path.exists(os.path.join(sparse_dir, filename))
        for filename in ("cameras.bin", "images.bin", "points3D.bin")
    )
