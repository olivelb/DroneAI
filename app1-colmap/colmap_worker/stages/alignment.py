"""Image undistortion, GCP control and geo-alignment stage."""

from __future__ import annotations

import json
import os
import shutil

from runtime_support import run_command
from shared.gcp_control import build_weighted_gcp_alignment, write_transformed_reconstruction
from shared.json_io import atomic_write_json

from .. import runtime
from ..contracts import (
    PipelineAlignmentState,
    PipelinePreparation,
    PipelineReconstruction,
    PipelineRtkState,
)


def undistort_and_align_colmap(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    workspace_dir: str,
    vol_id: str,
) -> PipelineAlignmentState:
    params = preparation.params
    facade_mode = preparation.facade_mode
    clean_images_dir = preparation.clean_images_dir
    dense_path = preparation.dense_path
    geo_data_file = preparation.geo_data_file
    gcp_path = preparation.gcp_path
    gcp_accuracy_path = preparation.gcp_accuracy_path
    utm_crs = reconstruction.utm_crs
    align_tf = reconstruction.alignment_transform_path
    ortho_only_ready = rtk_state.ortho_only_ready
    active_sparse_model_path = rtk_state.active_sparse_model_path

    # --- 7. Undistort images for Gaussian Splatting ---
    # GS only needs the undistorted images + dense/sparse model.
    if ortho_only_ready:
        runtime.report_mission_progress(vol_id, "UNDISTORT", 75, log="Undistorted images found. Skipping undistortion.")
    else:
        if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
            run_command(
                [
                    "colmap",
                    "image_undistorter",
                    "--image_path",
                    clean_images_dir,
                    "--input_path",
                    active_sparse_model_path,
                    "--output_path",
                    dense_path,
                    "--max_image_size",
                    params["mvs_max_image_size"],
                    "--num_threads",
                    params["mvs_num_threads"],
                ],
                vol_id,
                "UNDISTORT",
                70,
                runtime.report_mission_progress,
                runtime.ensure_not_cancelled,
            )
        else:
            runtime.report_mission_progress(
                vol_id, "UNDISTORT", 70, log="Undistorted images and fusion.cfg found. Skipping undistortion."
            )

        n_undistorted = (
            len(os.listdir(os.path.join(dense_path, "images")))
            if os.path.isdir(os.path.join(dense_path, "images"))
            else 0
        )
        runtime.report_mission_progress(
            vol_id,
            "UNDISTORT",
            90,
            log=f"Using {n_undistorted} undistorted images for Gaussian Splatting.",
        )

    # --- 8. Geo-alignment ---
    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")

    def ensure_alignment_transform():
        nonlocal align_tf
        gcp_adjustment_enabled = bool(params.get("gcp_adjustment_enabled", False))
        transform_file = os.path.join(
            workspace_dir,
            "alignment_transform.json",
        )
        if gcp_adjustment_enabled:
            if not gcp_path:
                raise RuntimeError("Weighted GCP adjustment is enabled but the dataset does not contain gcp_list.txt")
            runtime.report_mission_progress(
                vol_id,
                "ALIGNING",
                93,
                log=(
                    "Triangulating surveyed controls and fitting a robust "
                    "covariance-weighted GCP similarity transform..."
                ),
            )
            transform, gcp_report = build_weighted_gcp_alignment(
                active_sparse_model_path,
                gcp_path,
                utm_crs,
                accuracy_path=gcp_accuracy_path,
                default_horizontal_accuracy_m=float(params["gcp_horizontal_accuracy_m"]),
                default_vertical_accuracy_m=float(params["gcp_vertical_accuracy_m"]),
                default_image_accuracy_px=float(params["gcp_image_accuracy_px"]),
                robust_loss_scale=float(params["gcp_robust_loss_scale"]),
                require_checkpoints=bool(params["gcp_require_checkpoints"]),
                minimum_checkpoint_count=int(params["gcp_min_checkpoint_count"]),
                maximum_checkpoint_horizontal_rmse_m=float(params["gcp_max_checkpoint_horizontal_rmse_m"]),
                maximum_checkpoint_vertical_rmse_m=float(params["gcp_max_checkpoint_vertical_rmse_m"]),
                maximum_checkpoint_normalized_error_sigma=float(params["gcp_max_checkpoint_normalized_error_sigma"]),
                minimum_adjustment_baseline_m=float(params["gcp_min_adjustment_baseline_m"]),
            )
            gcp_report_file = os.path.join(workspace_dir, "gcp_alignment_report.json")
            atomic_write_json(gcp_report_file, gcp_report)
            quality_gate = gcp_report["quality_gate"]
            if not quality_gate["accepted"]:
                failed_checks = ", ".join(check["name"] for check in quality_gate["checks"] if not check["passed"])
                raise RuntimeError("GCP alignment rejected by the promotion gate: " + failed_checks)
            from shared.geo_alignment import write_alignment_transform

            write_alignment_transform(transform_file, transform)
            write_transformed_reconstruction(
                active_sparse_model_path,
                sparse_geo_path,
                transform,
            )
            align_tf = transform_file
            fit = transform["fit"]
            runtime.report_mission_progress(
                vol_id,
                "ALIGNING",
                94,
                log=(
                    "Saved weighted GCP alignment using "
                    f"{gcp_report['adjustment_points']} adjustment controls "
                    f"and {gcp_report['checkpoint_points']} independent "
                    f"checkpoints (RMSE={fit['rmse']:.3f} m, "
                    f"weighted RMSE={fit['weighted_rmse']:.2f} standard deviations, "
                    f"status={quality_gate['status']})."
                ),
                details={
                    "event": "gcp_alignment_completed",
                    "adjustment_points": gcp_report["adjustment_points"],
                    "checkpoint_points": gcp_report["checkpoint_points"],
                    "quality_gate": quality_gate,
                    **fit,
                },
            )
            return align_tf

        stale_gcp_report = os.path.join(
            workspace_dir,
            "gcp_alignment_report.json",
        )
        stale_gcp_alignment = os.path.exists(stale_gcp_report)
        if os.path.exists(stale_gcp_report):
            os.remove(stale_gcp_report)

        if align_tf and os.path.exists(align_tf):
            try:
                with open(align_tf, encoding="utf-8") as handle:
                    previous_alignment = json.load(handle)
                if previous_alignment.get("fit", {}).get("source") == "covariance_weighted_gcp":
                    os.remove(align_tf)
                    align_tf = None
                    stale_gcp_alignment = True
            except json.JSONDecodeError:
                os.remove(align_tf)
                align_tf = None
                stale_gcp_alignment = True
            except FileNotFoundError:
                align_tf = None
            except OSError as error:
                raise RuntimeError(f"Cannot inspect cached alignment transform: {error}") from error

        if stale_gcp_alignment:
            shutil.rmtree(sparse_geo_path, ignore_errors=True)

        if not (os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0):
            return None

        os.makedirs(sparse_geo_path, exist_ok=True)
        align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin"))
        if not align_done:
            run_command(
                [
                    "colmap",
                    "model_aligner",
                    "--input_path",
                    active_sparse_model_path,
                    "--output_path",
                    sparse_geo_path,
                    "--ref_images_path",
                    geo_data_file,
                    "--ref_is_gps",
                    "0",
                    "--alignment_max_error",
                    str(params["alignment_max_error"]),
                ],
                vol_id,
                "ALIGNING",
                93,
                runtime.report_mission_progress,
                runtime.ensure_not_cancelled,
            )

        if align_tf and os.path.exists(align_tf):
            return align_tf

        runtime.report_mission_progress(
            vol_id, "ALIGNING", 94, log="Computing sparse-model alignment transform for orthorectification..."
        )
        try:
            from shared.geo_alignment import (
                compute_reconstruction_alignment,
                write_alignment_transform,
            )

            transform = compute_reconstruction_alignment(
                active_sparse_model_path,
                sparse_geo_path,
            )
            write_alignment_transform(transform_file, transform)
            align_tf = transform_file
            fit = transform["fit"]
            runtime.report_mission_progress(
                vol_id,
                "ALIGNING",
                94,
                log=(
                    "Saved sparse-model alignment transform using "
                    f"{fit['correspondences']} images "
                    f"(scale={transform['scale']:.4f}, RMSE={fit['rmse']:.3f} m)"
                ),
            )
            return align_tf
        except Exception as e:
            runtime.report_mission_progress(
                vol_id,
                "ALIGNING",
                94,
                log=f"Failed to compute alignment transform ({e}); using raw COLMAP coordinates.",
            )
            return None

    if facade_mode:
        align_tf = None
        runtime.report_mission_progress(
            vol_id,
            "ALIGNING",
            94,
            log="Skipping geographic alignment; the dominant facade plane defines the local frame.",
        )
    else:
        ensure_alignment_transform()

    return PipelineAlignmentState(alignment_transform_path=align_tf)
