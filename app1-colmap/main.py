"""Compatibility entry point and composition root for the COLMAP worker."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from colmap_worker import runtime
from colmap_worker.artifacts import (
    dense_sparse_model_ready,
    invalidate_georeferencing_artifacts,
    invalidate_pipeline_artifacts,
    normalize_gpu_index,
    remove_artifact_paths as _remove_artifact_paths,
    remove_rtk_dependent_artifacts as _remove_rtk_dependent_artifacts,
)
from colmap_worker.contracts import (
    PipelineAlignmentState,
    PipelineGaussianState,
    PipelinePreparation,
    PipelinePublicationState,
    PipelineReconstruction,
    PipelineRtkState,
    SparseBootstrapState,
)
from colmap_worker.mission_runner import run_colmap_pipeline
from colmap_worker.stages.alignment import undistort_and_align_colmap
from colmap_worker.stages.gaussian import run_gaussian_product
from colmap_worker.stages.preparation import prepare_colmap_pipeline_run
from colmap_worker.stages.publication import (
    cleanup_pipeline_workspace,
    complete_colmap_pipeline,
    publish_colmap_products,
)
from colmap_worker.stages.reconstruction import prepare_sparse_bootstrap, reconstruct_colmap_sparse
from colmap_worker.stages.rtk import refine_colmap_rtk
from colmap_worker.worker import worker_main

PipelineCancelledError = runtime.PipelineCancelledError
cancellation_state = runtime.cancellation_state
mission_state_tracker = runtime.mission_state_tracker
report_mission_progress = runtime.report_mission_progress
ensure_not_cancelled = runtime.ensure_not_cancelled

__all__ = [
    "PipelineAlignmentState",
    "PipelineCancelledError",
    "PipelineGaussianState",
    "PipelinePreparation",
    "PipelinePublicationState",
    "PipelineReconstruction",
    "PipelineRtkState",
    "SparseBootstrapState",
    "_remove_artifact_paths",
    "_remove_rtk_dependent_artifacts",
    "cancellation_state",
    "cleanup_pipeline_workspace",
    "complete_colmap_pipeline",
    "dense_sparse_model_ready",
    "ensure_not_cancelled",
    "invalidate_georeferencing_artifacts",
    "invalidate_pipeline_artifacts",
    "mission_state_tracker",
    "normalize_gpu_index",
    "prepare_colmap_pipeline_run",
    "prepare_sparse_bootstrap",
    "publish_colmap_products",
    "reconstruct_colmap_sparse",
    "refine_colmap_rtk",
    "report_mission_progress",
    "run_colmap_pipeline",
    "run_gaussian_product",
    "undistort_and_align_colmap",
    "worker_main",
]


if __name__ == "__main__":
    worker_main()
