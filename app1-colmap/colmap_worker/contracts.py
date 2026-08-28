"""Typed state passed between the resumable COLMAP pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PipelinePreparation:
    params: dict[str, Any]
    facade_mode: bool
    orthophoto_mode: str
    mission_s3_prefix: str
    clean_images_dir: str
    db_path: str
    sparse_path: str
    geo_data_file: str
    dense_path: str
    gcp_path: str | None
    gcp_accuracy_path: str | None
    facade_selection_report_path: str
    feature_type: str
    matcher_type: str
    feature_family: str
    resolved_matcher_type: str
    feature_gpu_index: str
    ba_gpu_index: str
    projected_crs_mode: str
    requested_projected_crs: str
    image_reader_camera_model: str
    image_reader_camera_params: str | None
    images: list[Path]


@dataclass(frozen=True)
class SparseBootstrapState:
    utm_crs: str | None
    alignment_transform_path: str | None
    ortho_only_ready: bool
    gps_done: bool


@dataclass(frozen=True)
class PipelineReconstruction:
    utm_crs: str | None
    alignment_transform_path: str | None
    ortho_only_ready: bool


@dataclass(frozen=True)
class PipelineRtkState:
    active_sparse_model_path: str
    ortho_only_ready: bool
    report_path: str


@dataclass(frozen=True)
class PipelineAlignmentState:
    alignment_transform_path: str | None


@dataclass(frozen=True)
class PipelineGaussianState:
    ortho_file: str
    result: dict[str, Any]
    durable_checkpoint_dir: str
    checkpoint_s3_prefix: str
    profile_id: str
    qualification_policy_id: str
