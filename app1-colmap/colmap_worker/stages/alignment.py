"""Image undistortion, GCP control and geo-alignment stage."""

from __future__ import annotations

import json
import os
import shutil

from runtime_support import run_command
from shared import storage
from shared.gcp_control import build_weighted_gcp_alignment, write_transformed_reconstruction
from shared.json_io import atomic_write_json
from shared.stage_execution import StageQualityGateRejected
from shared.tenancy import LEGACY_ORGANIZATION_ID, MissionObjectNamespace

from .. import runtime
from ..contracts import (
    PipelineAlignmentState,
    PipelinePreparation,
    PipelineReconstruction,
    PipelineRtkState,
)


def _undistort_images(
    preparation: PipelinePreparation,
    rtk_state: PipelineRtkState,
    vol_id: str,
) -> None:
    if rtk_state.ortho_only_ready:
        runtime.report_mission_progress(
            vol_id,
            "UNDISTORT",
            75,
            log="Undistorted images found. Skipping undistortion.",
        )
        return

    dense_path = preparation.dense_path
    if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
        run_command(
            [
                "colmap",
                "image_undistorter",
                "--image_path",
                preparation.clean_images_dir,
                "--input_path",
                rtk_state.active_sparse_model_path,
                "--output_path",
                dense_path,
                "--max_image_size",
                preparation.params["mvs_max_image_size"],
                "--num_threads",
                preparation.params["mvs_num_threads"],
            ],
            vol_id,
            "UNDISTORT",
            70,
            runtime.report_mission_progress,
            runtime.ensure_not_cancelled,
        )
    else:
        runtime.report_mission_progress(
            vol_id,
            "UNDISTORT",
            70,
            log="Undistorted images and fusion.cfg found. Skipping undistortion.",
        )

    undistorted_images_dir = os.path.join(dense_path, "images")
    n_undistorted = (
        len(os.listdir(undistorted_images_dir))
        if os.path.isdir(undistorted_images_dir)
        else 0
    )
    runtime.report_mission_progress(
        vol_id,
        "UNDISTORT",
        90,
        log=f"Using {n_undistorted} undistorted images for Gaussian Splatting.",
    )


def _run_weighted_gcp_alignment(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    workspace_dir: str,
    vol_id: str,
) -> str:
    params = preparation.params
    if not preparation.gcp_path:
        raise RuntimeError(
            "Weighted GCP adjustment is enabled but the dataset does not contain gcp_list.txt"
        )

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
        rtk_state.active_sparse_model_path,
        preparation.gcp_path,
        reconstruction.utm_crs,
        accuracy_path=preparation.gcp_accuracy_path,
        default_horizontal_accuracy_m=float(params["gcp_horizontal_accuracy_m"]),
        default_vertical_accuracy_m=float(params["gcp_vertical_accuracy_m"]),
        default_image_accuracy_px=float(params["gcp_image_accuracy_px"]),
        robust_loss_scale=float(params["gcp_robust_loss_scale"]),
        require_checkpoints=bool(params["gcp_require_checkpoints"]),
        minimum_checkpoint_count=int(params["gcp_min_checkpoint_count"]),
        maximum_checkpoint_horizontal_rmse_m=float(
            params["gcp_max_checkpoint_horizontal_rmse_m"]
        ),
        maximum_checkpoint_vertical_rmse_m=float(
            params["gcp_max_checkpoint_vertical_rmse_m"]
        ),
        maximum_checkpoint_normalized_error_sigma=float(
            params["gcp_max_checkpoint_normalized_error_sigma"]
        ),
        minimum_adjustment_baseline_m=float(params["gcp_min_adjustment_baseline_m"]),
    )
    gcp_report_file = os.path.join(workspace_dir, "gcp_alignment_report.json")
    atomic_write_json(gcp_report_file, gcp_report)
    quality_gate = gcp_report["quality_gate"]
    if not quality_gate["accepted"]:
        failed_checks = ", ".join(
            check["name"] for check in quality_gate["checks"] if not check["passed"]
        )
        stage_run_id = os.getenv("DRONEAI_STAGE_RUN_ID", "").strip()
        evidence: dict[str, object] = {"persisted": False}
        if stage_run_id:
            mission_s3_prefix = getattr(
                preparation,
                "mission_s3_prefix",
                MissionObjectNamespace.create(
                    LEGACY_ORGANIZATION_ID,
                    vol_id,
                ).root,
            )
            evidence_key = "/".join(
                (
                    mission_s3_prefix,
                    "stage-runs",
                    stage_run_id,
                    "diagnostics",
                    "gcp_alignment_report.json",
                )
            )
            try:
                published = storage.upload_verified_file(
                    gcp_report_file,
                    evidence_key,
                )
                evidence = {
                    **published,
                    "persisted": True,
                    "uri": f"s3://{storage.S3_BUCKET}/{evidence_key}",
                }
            except Exception as error:
                evidence = {
                    "persisted": False,
                    "key": evidence_key,
                    "publish_error": str(error)[:1000],
                }
        runtime.report_mission_progress(
            vol_id,
            "ALIGNING",
            93,
            status="error",
            log="GCP alignment rejected by the promotion gate: " + failed_checks,
            details={
                "event": "gcp_alignment_rejected",
                "quality_gate": quality_gate,
                "evidence": evidence,
            },
        )
        raise StageQualityGateRejected(
            "GCP alignment rejected by the promotion gate: " + failed_checks,
            quality_metrics={"gcp_alignment": gcp_report},
            evidence=evidence,
        )

    from shared.geo_alignment import write_alignment_transform

    transform_file = os.path.join(workspace_dir, "alignment_transform.json")
    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
    write_alignment_transform(transform_file, transform)
    write_transformed_reconstruction(
        rtk_state.active_sparse_model_path,
        sparse_geo_path,
        transform,
    )
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
    return transform_file


def _remove_stale_gcp_alignment(
    workspace_dir: str,
    alignment_transform_path: str | None,
    sparse_geo_path: str,
) -> str | None:
    stale_gcp_report = os.path.join(workspace_dir, "gcp_alignment_report.json")
    stale_gcp_alignment = os.path.exists(stale_gcp_report)
    if stale_gcp_alignment:
        os.remove(stale_gcp_report)

    if alignment_transform_path and os.path.exists(alignment_transform_path):
        cached_transform_path = alignment_transform_path
        try:
            with open(cached_transform_path, encoding="utf-8") as handle:
                previous_alignment = json.load(handle)
            if previous_alignment.get("fit", {}).get("source") == "covariance_weighted_gcp":
                os.remove(cached_transform_path)
                alignment_transform_path = None
                stale_gcp_alignment = True
        except json.JSONDecodeError:
            os.remove(cached_transform_path)
            alignment_transform_path = None
            stale_gcp_alignment = True
        except FileNotFoundError:
            alignment_transform_path = None
        except OSError as error:
            raise RuntimeError(f"Cannot inspect cached alignment transform: {error}") from error

    if stale_gcp_alignment:
        shutil.rmtree(sparse_geo_path, ignore_errors=True)
    return alignment_transform_path


def _run_reference_alignment(
    preparation: PipelinePreparation,
    rtk_state: PipelineRtkState,
    workspace_dir: str,
    vol_id: str,
    alignment_transform_path: str | None,
) -> str | None:
    geo_data_file = preparation.geo_data_file
    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
    alignment_transform_path = _remove_stale_gcp_alignment(
        workspace_dir,
        alignment_transform_path,
        sparse_geo_path,
    )
    if not (os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0):
        return None

    os.makedirs(sparse_geo_path, exist_ok=True)
    if not os.path.exists(os.path.join(sparse_geo_path, "cameras.bin")):
        run_command(
            [
                "colmap",
                "model_aligner",
                "--input_path",
                rtk_state.active_sparse_model_path,
                "--output_path",
                sparse_geo_path,
                "--ref_images_path",
                geo_data_file,
                "--ref_is_gps",
                "0",
                "--alignment_max_error",
                str(preparation.params["alignment_max_error"]),
            ],
            vol_id,
            "ALIGNING",
            93,
            runtime.report_mission_progress,
            runtime.ensure_not_cancelled,
        )

    if alignment_transform_path and os.path.exists(alignment_transform_path):
        return alignment_transform_path

    runtime.report_mission_progress(
        vol_id,
        "ALIGNING",
        94,
        log="Computing sparse-model alignment transform for orthorectification...",
    )
    try:
        from shared.geo_alignment import (
            compute_reconstruction_alignment,
            write_alignment_transform,
        )

        transform = compute_reconstruction_alignment(
            rtk_state.active_sparse_model_path,
            sparse_geo_path,
        )
        transform_file = os.path.join(workspace_dir, "alignment_transform.json")
        write_alignment_transform(transform_file, transform)
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
        return transform_file
    except Exception as error:
        runtime.report_mission_progress(
            vol_id,
            "ALIGNING",
            94,
            log=(
                f"Failed to compute alignment transform ({error}); "
                "using raw COLMAP coordinates."
            ),
        )
        return None


def undistort_and_align_colmap(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    workspace_dir: str,
    vol_id: str,
) -> PipelineAlignmentState:
    if preparation.facade_mode:
        runtime.report_mission_progress(
            vol_id,
            "ALIGNING",
            94,
            log="Skipping geographic alignment; the dominant facade plane defines the local frame.",
        )
        alignment_transform_path = None
    elif bool(preparation.params.get("gcp_adjustment_enabled", False)):
        alignment_transform_path = _run_weighted_gcp_alignment(
            preparation,
            reconstruction,
            rtk_state,
            workspace_dir,
            vol_id,
        )
    else:
        alignment_transform_path = _run_reference_alignment(
            preparation,
            rtk_state,
            workspace_dir,
            vol_id,
            reconstruction.alignment_transform_path,
        )

    # Reject invalid geographic control before the comparatively expensive
    # image-undistortion pass. The undistorted imagery is independent from the
    # global similarity transform, so accepted missions retain identical data.
    _undistort_images(preparation, rtk_state, vol_id)

    return PipelineAlignmentState(alignment_transform_path=alignment_transform_path)
