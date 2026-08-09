"""Portable COLMAP state persisted inside stage workspace artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from shared.json_io import atomic_write_json

from .contracts import (
    PipelineAlignmentState,
    PipelinePreparation,
    PipelineReconstruction,
)

STATE_RELATIVE_PATH = Path(".droneai/reconstruction-state.json")
STATE_SCHEMA_VERSION = 1


def _relative(workspace: Path, raw_path: str | Path) -> str:
    path = Path(raw_path).resolve()
    return path.relative_to(workspace).as_posix()


def _optional_relative(workspace: Path, raw_path: str | None) -> str | None:
    return _relative(workspace, raw_path) if raw_path else None


def write_reconstruction_state(
    workspace_dir: str | Path,
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    alignment: PipelineAlignmentState,
) -> Path:
    workspace = Path(workspace_dir).resolve(strict=True)
    payload: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "preparation": {
            "params": preparation.params,
            "facade_mode": preparation.facade_mode,
            "orthophoto_mode": preparation.orthophoto_mode,
            "mission_s3_prefix": preparation.mission_s3_prefix,
            "clean_images_dir": _relative(workspace, preparation.clean_images_dir),
            "db_path": _relative(workspace, preparation.db_path),
            "sparse_path": _relative(workspace, preparation.sparse_path),
            "geo_data_file": _relative(workspace, preparation.geo_data_file),
            "dense_path": _relative(workspace, preparation.dense_path),
            "gcp_path": _optional_relative(workspace, preparation.gcp_path),
            "gcp_accuracy_path": _optional_relative(
                workspace,
                preparation.gcp_accuracy_path,
            ),
            "facade_selection_report_path": _relative(
                workspace,
                preparation.facade_selection_report_path,
            ),
            "feature_type": preparation.feature_type,
            "matcher_type": preparation.matcher_type,
            "feature_family": preparation.feature_family,
            "resolved_matcher_type": preparation.resolved_matcher_type,
            "feature_gpu_index": preparation.feature_gpu_index,
            "ba_gpu_index": preparation.ba_gpu_index,
            "projected_crs_mode": preparation.projected_crs_mode,
            "requested_projected_crs": preparation.requested_projected_crs,
            "image_reader_camera_model": preparation.image_reader_camera_model,
            "image_reader_camera_params": preparation.image_reader_camera_params,
            "images": [_relative(workspace, image) for image in preparation.images],
        },
        "reconstruction": {
            "utm_crs": reconstruction.utm_crs,
            "alignment_transform_path": _optional_relative(
                workspace,
                reconstruction.alignment_transform_path,
            ),
            "ortho_only_ready": reconstruction.ortho_only_ready,
        },
        "alignment": {
            "alignment_transform_path": _optional_relative(
                workspace,
                alignment.alignment_transform_path,
            ),
        },
    }
    state_path = workspace / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, payload)
    return state_path


def _workspace_path(workspace: Path, raw_path: object) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("COLMAP stage state contains an invalid path")
    candidate = (workspace / raw_path).resolve()
    if workspace not in candidate.parents:
        raise ValueError("COLMAP stage state path escapes the workspace")
    return str(candidate)


def _optional_workspace_path(workspace: Path, raw_path: object) -> str | None:
    return _workspace_path(workspace, raw_path) if raw_path is not None else None


def load_reconstruction_state(
    workspace_dir: str | Path,
) -> tuple[PipelinePreparation, PipelineReconstruction, PipelineAlignmentState]:
    workspace = Path(workspace_dir).resolve(strict=True)
    with (workspace / STATE_RELATIVE_PATH).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("Unsupported COLMAP reconstruction state schema")
    preparation = cast(dict[str, Any], payload.get("preparation"))
    reconstruction = cast(dict[str, Any], payload.get("reconstruction"))
    alignment = cast(dict[str, Any], payload.get("alignment"))
    if not all(isinstance(item, dict) for item in (preparation, reconstruction, alignment)):
        raise ValueError("COLMAP reconstruction state is incomplete")
    images = preparation.get("images")
    if not isinstance(images, list):
        raise ValueError("COLMAP reconstruction image state is invalid")
    return (
        PipelinePreparation(
            params=cast(dict[str, Any], preparation["params"]),
            facade_mode=bool(preparation["facade_mode"]),
            orthophoto_mode=str(preparation["orthophoto_mode"]),
            mission_s3_prefix=str(preparation["mission_s3_prefix"]),
            clean_images_dir=_workspace_path(workspace, preparation["clean_images_dir"]),
            db_path=_workspace_path(workspace, preparation["db_path"]),
            sparse_path=_workspace_path(workspace, preparation["sparse_path"]),
            geo_data_file=_workspace_path(workspace, preparation["geo_data_file"]),
            dense_path=_workspace_path(workspace, preparation["dense_path"]),
            gcp_path=_optional_workspace_path(workspace, preparation.get("gcp_path")),
            gcp_accuracy_path=_optional_workspace_path(
                workspace,
                preparation.get("gcp_accuracy_path"),
            ),
            facade_selection_report_path=_workspace_path(
                workspace,
                preparation["facade_selection_report_path"],
            ),
            feature_type=str(preparation["feature_type"]),
            matcher_type=str(preparation["matcher_type"]),
            feature_family=str(preparation["feature_family"]),
            resolved_matcher_type=str(preparation["resolved_matcher_type"]),
            feature_gpu_index=str(preparation["feature_gpu_index"]),
            ba_gpu_index=str(preparation["ba_gpu_index"]),
            projected_crs_mode=str(preparation["projected_crs_mode"]),
            requested_projected_crs=str(preparation["requested_projected_crs"]),
            image_reader_camera_model=str(preparation["image_reader_camera_model"]),
            image_reader_camera_params=cast(
                str | None,
                preparation.get("image_reader_camera_params"),
            ),
            images=[Path(_workspace_path(workspace, item)) for item in images],
        ),
        PipelineReconstruction(
            utm_crs=cast(str | None, reconstruction.get("utm_crs")),
            alignment_transform_path=_optional_workspace_path(
                workspace,
                reconstruction.get("alignment_transform_path"),
            ),
            ortho_only_ready=bool(reconstruction["ortho_only_ready"]),
        ),
        PipelineAlignmentState(
            alignment_transform_path=_optional_workspace_path(
                workspace,
                alignment.get("alignment_transform_path"),
            ),
        ),
    )
