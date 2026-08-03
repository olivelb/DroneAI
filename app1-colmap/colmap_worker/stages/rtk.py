"""RTK prior refinement and candidate promotion stage."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from pipeline_support import inspect_sparse_quality
from runtime_support import run_command
from shared.json_io import atomic_write_json
from shared.rtk_refinement import (
    assess_rtk_refinement_quality,
    inject_database_pose_priors,
    load_rtk_records,
)

from .. import runtime
from ..artifacts import remove_rtk_dependent_artifacts
from ..contracts import PipelinePreparation, PipelineReconstruction, PipelineRtkState


def refine_colmap_rtk(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    workspace_dir: str,
    vol_id: str,
) -> PipelineRtkState:
    params = preparation.params
    clean_images_dir = preparation.clean_images_dir
    db_path = preparation.db_path
    dense_path = preparation.dense_path
    sparse_path = preparation.sparse_path
    ba_gpu_index = preparation.ba_gpu_index
    ortho_only_ready = reconstruction.ortho_only_ready

    # --- 6b. Optional covariance-aware RTK/PPK refinement ---
    base_sparse_model_path = os.path.join(sparse_path, "0")
    rtk_sparse_model_path = os.path.join(workspace_dir, "sparse_rtk")
    rtk_report_path = os.path.join(workspace_dir, "rtk_prior_report.json")
    rtk_refinement_enabled = bool(params.get("rtk_refinement_enabled", True))
    active_sparse_model_path = base_sparse_model_path

    def evaluate_rtk_candidate():
        return assess_rtk_refinement_quality(
            inspect_sparse_quality(base_sparse_model_path),
            inspect_sparse_quality(rtk_sparse_model_path),
            minimum_point_ratio=float(params["rtk_minimum_point_ratio"]),
            maximum_reprojection_degradation_px=float(params["rtk_maximum_reprojection_degradation_px"]),
            maximum_track_length_loss_ratio=float(params["rtk_maximum_track_length_loss_ratio"]),
            maximum_focal_length_change_ratio=float(params["rtk_maximum_focal_length_change_ratio"]),
        )

    if rtk_refinement_enabled and os.path.exists(os.path.join(base_sparse_model_path, "cameras.bin")):
        if os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin")):
            quality_gate = evaluate_rtk_candidate()
            cached_report = {"schema_version": 1}
            try:
                with open(rtk_report_path, encoding="utf-8") as handle:
                    cached_report.update(json.load(handle))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
            cached_report["quality_gate"] = quality_gate
            previous_selected_model = cached_report.get("selected_model")
            selected_model = "rtk_candidate" if quality_gate["accepted"] else "visual_baseline"
            cached_report["selected_model"] = selected_model
            cached_report["status"] = "completed" if quality_gate["accepted"] else "rejected-quality-gate"
            atomic_write_json(rtk_report_path, cached_report)
            if quality_gate["accepted"]:
                active_sparse_model_path = rtk_sparse_model_path
            if (previous_selected_model is not None and previous_selected_model != selected_model) or (
                previous_selected_model is None and selected_model == "visual_baseline"
            ):
                # Cached dense/render products may have been derived from
                # the previously auto-promoted RTK model. Force a rebuild
                # when the gate changes (or first rejects) the selection.
                ortho_only_ready = False
                remove_rtk_dependent_artifacts(workspace_dir, dense_path)
            runtime.report_mission_progress(
                vol_id,
                "RTK_REFINEMENT",
                57,
                log=(
                    "Reusing the quality-gated covariance-aware RTK sparse model."
                    if quality_gate["accepted"]
                    else "Cached RTK candidate failed the comparison gate; retaining the visual baseline."
                ),
                details={
                    "event": "rtk_refinement_reuse_gate",
                    "quality_gate": quality_gate,
                },
            )
        elif not ortho_only_ready:
            try:
                rtk_records = load_rtk_records(clean_images_dir)
                rtk_report = inject_database_pose_priors(db_path, rtk_records)
                rtk_report["status"] = "priors-injected"
                atomic_write_json(rtk_report_path, rtk_report)
                os.makedirs(rtk_sparse_model_path, exist_ok=True)
                rtk_timeout = float(params["rtk_refinement_timeout_seconds"])
                rtk_iterations = int(float(params["rtk_refinement_iterations"]))
                rtk_loss_scale = float(params["rtk_refinement_loss_scale"])
                runtime.report_mission_progress(
                    vol_id,
                    "RTK_REFINEMENT",
                    55,
                    log=(
                        f"Constraining the completed visual reconstruction with "
                        f"{rtk_report['updated_pose_priors']} camera-position priors "
                        f"and MRK/XMP RTK covariance using robust Ceres GPU BA, "
                        f"{rtk_iterations} iterations and a {rtk_timeout:.0f}s budget."
                    ),
                    details={"event": "rtk_refinement_started", **rtk_report},
                )
                rtk_started_at = time.monotonic()
                run_command(
                    [
                        "colmap",
                        "pose_prior_mapper",
                        "--database_path",
                        db_path,
                        "--image_path",
                        clean_images_dir,
                        "--input_path",
                        base_sparse_model_path,
                        "--output_path",
                        rtk_sparse_model_path,
                        "--Mapper.ba_use_gpu",
                        "1",
                        "--Mapper.ba_gpu_index",
                        ba_gpu_index,
                        "--Mapper.ba_local_backend",
                        "CERES",
                        "--Mapper.ba_global_backend",
                        "CERES",
                        "--Mapper.ba_local_max_num_iterations",
                        str(rtk_iterations),
                        "--Mapper.ba_global_max_num_iterations",
                        str(rtk_iterations),
                        "--Mapper.ba_local_max_refinements",
                        "1",
                        "--Mapper.ba_global_max_refinements",
                        "1",
                        "--Mapper.ba_global_ignore_redundant_points3D",
                        "1",
                        "--use_robust_loss_on_prior_position",
                        "1",
                        "--prior_position_loss_scale",
                        str(rtk_loss_scale),
                    ],
                    vol_id,
                    "RTK_REFINEMENT",
                    56,
                    runtime.report_mission_progress,
                    runtime.ensure_not_cancelled,
                    timeout_seconds=rtk_timeout,
                )
                if not os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin")):
                    raise RuntimeError("pose_prior_mapper did not write a usable RTK model")
                quality_gate = evaluate_rtk_candidate()
                rtk_report.update(
                    {
                        "status": ("completed" if quality_gate["accepted"] else "rejected-quality-gate"),
                        "elapsed_seconds": time.monotonic() - rtk_started_at,
                        "iterations": rtk_iterations,
                        "timeout_seconds": rtk_timeout,
                        "ba_backend": "CERES_GPU",
                        "robust_loss": "cauchy",
                        "robust_loss_scale": rtk_loss_scale,
                        "quality_gate": quality_gate,
                        "selected_model": ("rtk_candidate" if quality_gate["accepted"] else "visual_baseline"),
                    }
                )
                atomic_write_json(rtk_report_path, rtk_report)
                if quality_gate["accepted"]:
                    active_sparse_model_path = rtk_sparse_model_path
                    remove_rtk_dependent_artifacts(workspace_dir, dense_path)
                runtime.report_mission_progress(
                    vol_id,
                    "RTK_REFINEMENT",
                    58,
                    log=(
                        "RTK pose refinement passed the comparison gate; downstream "
                        "processing will use the constrained model."
                        if quality_gate["accepted"]
                        else "RTK candidate degraded visual sparse metrics and was rejected; downstream processing retains the baseline model."
                    ),
                    details={
                        "event": (
                            "rtk_refinement_completed" if quality_gate["accepted"] else "rtk_refinement_rejected"
                        ),
                        **rtk_report,
                    },
                )
            except (
                RuntimeError,
                subprocess.CalledProcessError,
                TimeoutError,
            ) as error:
                shutil.rmtree(rtk_sparse_model_path, ignore_errors=True)
                fallback_report = {
                    "schema_version": 1,
                    "status": "skipped-or-fallback",
                    "reason": str(error),
                    "fallback_model": base_sparse_model_path,
                    "selected_model": "visual_baseline",
                }
                atomic_write_json(rtk_report_path, fallback_report)
                runtime.report_mission_progress(
                    vol_id,
                    "RTK_REFINEMENT",
                    58,
                    log=(
                        f"RTK refinement unavailable or bounded attempt failed "
                        f"({error}); retaining the verified fast sparse model."
                    ),
                    details={"event": "rtk_refinement_fallback", **fallback_report},
                )

    return PipelineRtkState(
        active_sparse_model_path=active_sparse_model_path,
        ortho_only_ready=ortho_only_ready,
        report_path=rtk_report_path,
    )
