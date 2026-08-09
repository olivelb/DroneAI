"""One-shot COLMAP stage adapters for bounded Kubernetes Jobs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, cast

from pipeline_support import inspect_sparse_quality
from shared.stage_execution import (
    StageExecutionContext,
    StageExecutionControl,
    StageExecutionResult,
)
from shared.stage_workspace import publish_workspace
from shared.validation import safe_child_path, validate_dataset_prefix

from . import runtime
from .stages.alignment import undistort_and_align_colmap
from .stages.preparation import prepare_colmap_pipeline_run
from .stages.reconstruction import reconstruct_colmap_sparse
from .stages.rtk import refine_colmap_rtk
from .stage_state import STATE_RELATIVE_PATH, write_reconstruction_state


def _workspace_path(run_id: str) -> Path:
    root = Path(os.getenv("DRONEAI_STAGE_WORK_ROOT", "/work")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return cast(Path, safe_child_path(root, run_id, field_name="stage run id"))


def run_reconstruction_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Build and publish the aligned COLMAP workspace needed by DroneGS."""
    input_dataset = validate_dataset_prefix(
        str(context.mission_parameters.get("input_dataset") or "")
    )
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    runtime.cancellation_state.start_mission(
        context.vol_id,
        context.mission_attempt,
    )

    def ensure_active() -> None:
        control.raise_if_cancelled()
        runtime.ensure_not_cancelled()

    try:
        ensure_active()
        preparation = prepare_colmap_pipeline_run(
            str(workspace),
            input_dataset,
            context.vol_id,
            context.mission_parameters,
        )
        reconstruction = reconstruct_colmap_sparse(
            preparation,
            str(workspace),
            context.vol_id,
        )
        rtk_state = refine_colmap_rtk(
            preparation,
            reconstruction,
            str(workspace),
            context.vol_id,
        )
        alignment = undistort_and_align_colmap(
            preparation,
            reconstruction,
            rtk_state,
            str(workspace),
            context.vol_id,
        )
        write_reconstruction_state(
            workspace,
            preparation,
            reconstruction,
            alignment,
        )
        ensure_active()
        prefix = (
            f"missions/{context.vol_id}/stage-runs/"
            f"{context.run_id}/reconstruction-workspace"
        )
        published = publish_workspace(
            workspace,
            prefix,
            cancellation_check=ensure_active,
        )
        quality = cast(
            dict[str, Any],
            inspect_sparse_quality(rtk_state.active_sparse_model_path),
        )
        return StageExecutionResult(
            kind="reconstruction_workspace",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata={
                "manifest_key": published.manifest_key,
                "file_count": published.file_count,
                "state_file": STATE_RELATIVE_PATH.as_posix(),
                "utm_crs": reconstruction.utm_crs,
                "active_sparse_model": str(
                    Path(rtk_state.active_sparse_model_path).relative_to(workspace)
                ),
                "alignment_transform": (
                    str(Path(alignment.alignment_transform_path).relative_to(workspace))
                    if alignment.alignment_transform_path
                    else None
                ),
            },
            quality_metrics=quality,
            provenance={
                "stage_adapter": "colmap-reconstruction-v1",
                "feature_type": preparation.feature_type,
                "matcher_type": preparation.matcher_type,
            },
        )
    finally:
        runtime.cancellation_state.clear()
        if workspace.exists():
            shutil.rmtree(workspace)
