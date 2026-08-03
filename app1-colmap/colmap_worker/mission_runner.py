"""Mission-level composition of independently testable pipeline stages."""

from __future__ import annotations

from typing import Any

from . import runtime
from .stages.alignment import undistort_and_align_colmap
from .stages.gaussian import run_gaussian_product
from .stages.preparation import prepare_colmap_pipeline_run
from .stages.publication import (
    cleanup_pipeline_workspace,
    complete_colmap_pipeline,
    publish_colmap_products,
)
from .stages.reconstruction import reconstruct_colmap_sparse
from .stages.rtk import refine_colmap_rtk


def run_colmap_pipeline(
    workspace_dir: str,
    input_dataset: str,
    vol_id: str,
    mission_params: dict[str, Any],
) -> None:
    try:
        preparation = prepare_colmap_pipeline_run(
            workspace_dir,
            input_dataset,
            vol_id,
            mission_params,
        )
        reconstruction = reconstruct_colmap_sparse(
            preparation,
            workspace_dir,
            vol_id,
        )
        rtk_state = refine_colmap_rtk(
            preparation,
            reconstruction,
            workspace_dir,
            vol_id,
        )
        # --- 7-8. Undistortion and geo-alignment ---
        alignment_state = undistort_and_align_colmap(
            preparation,
            reconstruction,
            rtk_state,
            workspace_dir,
            vol_id,
        )

        # --- 9. Gaussian Splatting product ---
        gaussian_state = run_gaussian_product(
            preparation,
            reconstruction,
            alignment_state,
            workspace_dir,
            vol_id,
        )
        # --- 10. Verified product publication ---
        publication_state = publish_colmap_products(
            preparation,
            reconstruction,
            rtk_state,
            alignment_state,
            gaussian_state,
            workspace_dir,
            vol_id,
        )
        # --- 11. Completion and recovery-state retirement ---
        complete_colmap_pipeline(
            preparation,
            publication_state,
            gaussian_state,
            workspace_dir,
            vol_id,
            mission_params,
        )

    except runtime.PipelineCancelledError as error:
        runtime.report_mission_progress(vol_id, "CANCELLED", 0, status="error", log=f"🚫 {error!s}")
    except Exception as error:
        runtime.report_mission_progress(vol_id, "ERROR", 0, status="error", log=f"CRITICAL ERROR: {error!s}")
        raise
    finally:
        # Always clean up local workspace to avoid filling the system disk.
        cleanup_pipeline_workspace(workspace_dir, vol_id, final_pass=True)
        # Release Python-owned memory after every mission. GPU resources are
        # intentionally left to the runtime and driver lifecycle.
        import gc

        gc.collect()
