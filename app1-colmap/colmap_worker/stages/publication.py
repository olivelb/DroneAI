"""Verified product publication, completion and cleanup stages."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from shared import storage
from shared.camera_projection import write_camera_projection_index
from shared.config import TOPIC_ORTHO as TOPIC_OUT
from shared.facade_process import FACADE_PROCESS_PROFILE_ID
from shared.geospatial_assets import convert_to_cog, metadata_path, preview_path
from shared.pipeline_params import normalize_ai_backend
from shared.product_manifest import build_product_manifest, write_product_manifest
from worker_support import publish_next_stage_message

from .. import runtime
from ..contracts import (
    PipelineAlignmentState,
    PipelineGaussianState,
    PipelinePreparation,
    PipelinePublicationState,
    PipelineReconstruction,
    PipelineRtkState,
)
from ..recovery_publication import upload_optional_recovery_artifacts

ROOT_DIR = Path(__file__).resolve().parents[3]
logger = logging.getLogger("app1-colmap")


@dataclass(frozen=True)
class VerifiedGaussianPartition:
    path: str
    row: int
    column: int
    gaussian_count: int
    core_gaussian_count: int


@dataclass(frozen=True)
class VerifiedProductAssets:
    height_tif: str
    final_ply: str | None
    gaussian_partitions: tuple[VerifiedGaussianPartition, ...]
    trainer_manifests: tuple[Path, ...]
    qualification_manifests: tuple[Path, ...]
    required_reports: dict[str, str | None]
    gcp_enabled: bool
    gcp_sparse_files: tuple[str, ...]


def _verify_gaussian_models(
    result: dict[str, Any],
) -> tuple[str | None, tuple[VerifiedGaussianPartition, ...]]:
    """Accept exactly one portable Gaussian model layout."""

    raw_final = result.get("final_ply")
    raw_partitions = result.get("gaussian_partition_models", [])
    if raw_partitions is None:
        raw_partitions = []
    if not isinstance(raw_partitions, list):
        raise ValueError("Gaussian partition model inventory is invalid")
    if raw_final and raw_partitions:
        raise ValueError("Gaussian product cannot mix global and resident models")
    if raw_final:
        final_ply = str(raw_final)
        if not os.path.isfile(final_ply):
            raise FileNotFoundError(
                f"Required reusable Gaussian artifact is missing: {final_ply}"
            )
        return final_ply, ()
    if not raw_partitions:
        raise FileNotFoundError("Gaussian product has no reusable model artifact")

    partitions: list[VerifiedGaussianPartition] = []
    coordinates: set[tuple[int, int]] = set()
    for raw_partition in raw_partitions:
        if not isinstance(raw_partition, dict):
            raise ValueError("Gaussian partition model entry is invalid")
        path = raw_partition.get("path")
        values = {
            name: raw_partition.get(name)
            for name in (
                "row",
                "column",
                "gaussian_count",
                "core_gaussian_count",
            )
        }
        if not isinstance(path, str) or not path or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in values.values()
        ):
            raise ValueError("Gaussian partition model metadata is invalid")
        row = cast(int, values["row"])
        column = cast(int, values["column"])
        gaussian_count = cast(int, values["gaussian_count"])
        core_gaussian_count = cast(int, values["core_gaussian_count"])
        if (
            row < 0
            or column < 0
            or gaussian_count < 1
            or not 1 <= core_gaussian_count <= gaussian_count
        ):
            raise ValueError("Gaussian partition model counts are invalid")
        coordinate = (row, column)
        if coordinate in coordinates:
            raise ValueError("Gaussian partition model coordinates are duplicated")
        coordinates.add(coordinate)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Required resident Gaussian artifact is missing: {path}"
            )
        partitions.append(
            VerifiedGaussianPartition(
                path=path,
                row=row,
                column=column,
                gaussian_count=gaussian_count,
                core_gaussian_count=core_gaussian_count,
            )
        )
    partitions.sort(key=lambda partition: (partition.row, partition.column))
    return None, tuple(partitions)


def _publish_camera_projection_index(
    *,
    workspace_dir: str,
    projected_crs: str,
    mission_s3_prefix: str,
    vol_id: str,
) -> bool:
    """Publish portable camera visibility metadata when geo-alignment exists."""

    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
    if not os.path.isfile(os.path.join(sparse_geo_path, "images.bin")):
        return False
    destination = os.path.join(workspace_dir, "camera_projection_index.json")
    try:
        write_camera_projection_index(sparse_geo_path, projected_crs, destination)
        storage.upload_file(
            destination,
            f"{mission_s3_prefix}/camera_projection_index.json",
        )
    except Exception as error:
        runtime.report_mission_progress(
            vol_id,
            "UPLOADING",
            94,
            log=f"Warning: camera projection index could not be published: {error}",
        )
        return False
    return True


def _verify_product_assets(
    preparation: PipelinePreparation,
    rtk_state: PipelineRtkState,
    alignment_state: PipelineAlignmentState,
    gaussian_state: PipelineGaussianState,
    workspace_dir: str,
) -> VerifiedProductAssets:
    params = preparation.params
    result = gaussian_state.result
    ortho_file = gaussian_state.ortho_file
    if not os.path.isfile(ortho_file):
        raise FileNotFoundError(f"Required orthomosaic artifact is missing: {ortho_file}")
    convert_to_cog(ortho_file)

    height_tif = str(result["height_file"])
    if not os.path.isfile(height_tif):
        raise FileNotFoundError(f"Required DSM artifact is missing: {height_tif}")
    convert_to_cog(height_tif)

    final_ply, gaussian_partitions = _verify_gaussian_models(result)
    trainer_manifests = tuple(sorted(Path(result["checkpoint_dir"]).rglob("trainer_run.json")))
    if not trainer_manifests:
        raise FileNotFoundError("Required DroneGS training manifest is missing")
    qualification_manifests = tuple(sorted(Path(result["checkpoint_dir"]).rglob("canary_result.json")))
    if len(qualification_manifests) != len(trainer_manifests):
        raise FileNotFoundError("Every reusable DroneGS model requires a canary qualification manifest")
    reusable_model_count = 1 if final_ply is not None else len(gaussian_partitions)
    if len(trainer_manifests) < reusable_model_count:
        raise FileNotFoundError(
            "Every reusable Gaussian model requires a trainer and canary manifest"
        )

    gcp_enabled = bool(params.get("gcp_adjustment_enabled", False))
    gcp_report_file = os.path.join(workspace_dir, "gcp_alignment_report.json")
    required_reports = {
        "rtk_prior_report": (rtk_state.report_path if os.path.isfile(rtk_state.report_path) else None),
        "imu_gravity_report": os.path.join(workspace_dir, "imu_gravity_report.json"),
        "alignment_transform": alignment_state.alignment_transform_path,
        "gcp_alignment_report": gcp_report_file if gcp_enabled else None,
        "facade_frame_report": (result.get("facade_frame_report") if preparation.facade_mode else None),
        "facade_selection_report": (preparation.facade_selection_report_path if preparation.facade_mode else None),
        "gaussian_coverage_report": (None if preparation.facade_mode else result.get("gaussian_coverage_report")),
    }

    coverage_report = required_reports["gaussian_coverage_report"]
    if not preparation.facade_mode and (coverage_report is None or not os.path.isfile(coverage_report)):
        raise FileNotFoundError("Aerial map product is missing its Gaussian spatial coverage report")

    gcp_sparse_files: tuple[str, ...] = ()
    if gcp_enabled:
        align_tf = alignment_state.alignment_transform_path
        if not align_tf or not os.path.isfile(align_tf):
            raise FileNotFoundError("GCP mission is missing its required alignment transform")
        if not os.path.isfile(gcp_report_file):
            raise FileNotFoundError("GCP mission is missing its required alignment report")
        sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
        gcp_sparse_files = tuple(
            os.path.join(sparse_geo_path, name) for name in ("cameras.bin", "images.bin", "points3D.bin")
        )
        missing_sparse_files = [path for path in gcp_sparse_files if not os.path.isfile(path)]
        if missing_sparse_files:
            raise FileNotFoundError(f"GCP mission is missing sparse_geo/{os.path.basename(missing_sparse_files[0])}")

    return VerifiedProductAssets(
        height_tif=height_tif,
        final_ply=final_ply,
        gaussian_partitions=gaussian_partitions,
        trainer_manifests=trainer_manifests,
        qualification_manifests=qualification_manifests,
        required_reports=required_reports,
        gcp_enabled=gcp_enabled,
        gcp_sparse_files=gcp_sparse_files,
    )


def _write_verified_product_manifest(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    gaussian_state: PipelineGaussianState,
    assets: VerifiedProductAssets,
    workspace_dir: str,
    vol_id: str,
) -> str:
    facade_mode = preparation.facade_mode
    result = gaussian_state.result
    product_manifest_path = os.path.join(workspace_dir, "product_manifest.json")
    gaussian_products = (
        {"gaussian_model": assets.final_ply}
        if assets.final_ply is not None
        else {
            (
                f"gaussian_partition_r{partition.row:03d}_"
                f"c{partition.column:03d}"
            ): partition.path
            for partition in assets.gaussian_partitions
        }
    )
    product_manifest = build_product_manifest(
        mission_id=vol_id,
        projected_crs=("LOCAL_FACADE" if facade_mode else reconstruction.utm_crs),
        parameters={
            "pipeline": preparation.params,
            "effective_product_profile_id": (FACADE_PROCESS_PROFILE_ID if facade_mode else "AERIAL_MAP"),
            "effective_training_profile_id": gaussian_state.profile_id,
            "effective_qualification_policy_id": gaussian_state.qualification_policy_id,
            "renderer": {
                "render_mode": result["render_mode"],
                "coordinate_system": result["coordinate_system"],
                "width": result["width"],
                "height": result["height"],
                "pixel_size": result["gsd"],
                "pixel_size_units": result["gsd_units"],
                "scale_source": result["scale_source"],
                "meters_per_model_unit": result["meters_per_model_unit"],
                **({"gsd_m": result["gsd"]} if not facade_mode else {}),
                "raster_extent": result["raster_extent"],
                "projected_extent": result["projected_extent"],
                "vertical_reference": result["vertical_reference"],
                "vertical_offset_m": result["vertical_offset_m"],
                "gaussians": result["n_gaussians"],
                "gaussian_model_layout": (
                    "global"
                    if assets.final_ply is not None
                    else "resident-partitions"
                ),
                "gaussian_model_count": (
                    1
                    if assets.final_ply is not None
                    else len(assets.gaussian_partitions)
                ),
                "gaussian_density": result.get("gaussian_density"),
                "renderer_contract": result["renderer_contract"],
                "cupy_version": result["cupy_version"],
                "mip_filter_variance": result["ortho_mip_filter_variance"],
                "mip_filter_compensation": result["ortho_mip_filter_compensation"],
                "spatial_coverage": result.get("gaussian_coverage"),
                "sh_frame_policy": (
                    "colmap-view-direction-local-facade-v1" if facade_mode else "inverse-sim3-view-direction-v1"
                ),
            },
        },
        products={
            ("facade_orthophoto_cog" if facade_mode else "orthomosaic_cog"): gaussian_state.ortho_file,
            ("facade_orthophoto_metadata" if facade_mode else "orthomosaic_metadata"): metadata_path(
                gaussian_state.ortho_file
            ),
            ("facade_orthophoto_preview" if facade_mode else "orthomosaic_preview"): preview_path(
                gaussian_state.ortho_file
            ),
            ("facade_depth_cog" if facade_mode else "dsm_cog"): assets.height_tif,
            ("facade_depth_metadata" if facade_mode else "dsm_metadata"): metadata_path(assets.height_tif),
            ("facade_depth_preview" if facade_mode else "dsm_preview"): preview_path(assets.height_tif),
            **gaussian_products,
        },
        sparse_model_path=rtk_state.active_sparse_model_path,
        reports=assets.required_reports,
        trainer_manifests=list(assets.trainer_manifests),
        qualification_manifests=list(assets.qualification_manifests),
        git_revision=os.getenv("DRONEAI_GIT_REVISION"),
        software_components={
            "pipeline": ROOT_DIR / "app1-colmap" / "colmap_worker" / "mission_runner.py",
            "product_manifest": ROOT_DIR / "shared" / "product_manifest.py",
            "rtk_refinement": ROOT_DIR / "shared" / "rtk_refinement.py",
            "gcp_control": ROOT_DIR / "shared" / "gcp_control.py",
            "facade_selection": ROOT_DIR / "shared" / "facade_selection.py",
            "facade_process": ROOT_DIR / "shared" / "facade_process.py",
            "facade_frame": ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "facade_frame.py",
            "ortho_generator": (ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "generate_gaussian_orthophoto.py"),
            "ortho_renderer": ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "ortho_renderer.py",
            "cuda_rasterizer": ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "cuda_rasterizer.py",
        },
    )
    write_product_manifest(product_manifest_path, product_manifest)
    return product_manifest_path


def publish_colmap_products(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    alignment_state: PipelineAlignmentState,
    gaussian_state: PipelineGaussianState,
    workspace_dir: str,
    vol_id: str,
) -> PipelinePublicationState:
    facade_mode = preparation.facade_mode
    mission_s3_prefix = preparation.mission_s3_prefix
    db_path = preparation.db_path
    sparse_path = preparation.sparse_path
    geo_data_file = preparation.geo_data_file
    dense_path = preparation.dense_path
    ortho_file = gaussian_state.ortho_file
    result = gaussian_state.result
    durable_checkpoint_dir = gaussian_state.durable_checkpoint_dir
    gaussian_upload_complete = False

    # --- Upload ALL artifacts to S3 (well-organized folders) ---
    # S3 layout:
    #   {durable_mission_workspace}/
    #     orthomosaic.tif          — final GeoTIFF
    #     orthomosaic.height.tif   — height map (if generated)
    #     alignment_transform.json — Sim3 geo-alignment
    #     gcp_alignment_report.json — weighted controls/checkpoints (optional)
    #     imu_gravity_report.json   — attitude coverage/provenance (optional)
    #     geo_data.txt             — GPS from EXIF
    #     geo_data.txt.crs         — projected metric CRS code
    #     geo_data.txt.crs.json    — CRS selection policy and provenance
    #     colmap/
    #       database.db            — COLMAP feature database
    #       sparse/0/              — SfM sparse model (cameras.bin, images.bin, points3D.bin)
    #       sparse_geo/            — Geo-registered sparse model
    #     dense/
    #       sparse/0/              — Undistorted model
    #       images/                — Undistorted images
    #     gaussian/
    #       final.ply              — Monolithic Gaussian model, or
    #       partitions/            — Resident core/buffer models
    #       full/splat_*.ply       — Training output PLY
    #       full/checkpoints/      — Resume checkpoint

    runtime.report_mission_progress(
        vol_id,
        "UPLOADING",
        90,
        log="Converting orthomosaic to a tiled COG and publishing verified assets...",
    )
    upload_count = 0
    product_stem = "facade_orthophoto" if facade_mode else "orthomosaic"
    ortho_s3_key = f"{mission_s3_prefix}/{product_stem}.tif"
    assets = _verify_product_assets(
        preparation,
        rtk_state,
        alignment_state,
        gaussian_state,
        workspace_dir,
    )
    product_manifest_path = _write_verified_product_manifest(
        preparation,
        reconstruction,
        rtk_state,
        gaussian_state,
        assets,
        workspace_dir,
        vol_id,
    )

    storage.upload_verified_file(ortho_file, ortho_s3_key)
    storage.upload_verified_file(
        metadata_path(ortho_file),
        f"{ortho_s3_key}.cog.json",
    )
    storage.upload_verified_file(
        preview_path(ortho_file),
        f"{mission_s3_prefix}/{product_stem}.preview.webp",
    )
    upload_count += 3
    height_key = f"{mission_s3_prefix}/{product_stem}.height.tif"
    for local_path, remote_key in (
        (assets.height_tif, height_key),
        (metadata_path(assets.height_tif), f"{height_key}.cog.json"),
        (
            preview_path(assets.height_tif),
            f"{mission_s3_prefix}/{product_stem}.height.preview.webp",
        ),
    ):
        storage.upload_verified_file(local_path, remote_key)
        upload_count += 1
    if assets.final_ply is not None:
        storage.upload_verified_file(
            assets.final_ply,
            f"{mission_s3_prefix}/gaussian/final.ply",
        )
        upload_count += 1
    else:
        for partition in assets.gaussian_partitions:
            storage.upload_verified_file(
                partition.path,
                (
                    f"{mission_s3_prefix}/gaussian/partitions/"
                    f"cell-{partition.row}-{partition.column}.ply"
                ),
            )
            upload_count += 1
    checkpoint_root_path = Path(result["checkpoint_dir"]).resolve()
    for required_manifest in [
        *assets.trainer_manifests,
        *assets.qualification_manifests,
    ]:
        relative = required_manifest.resolve().relative_to(checkpoint_root_path)
        storage.upload_verified_file(
            required_manifest,
            f"{mission_s3_prefix}/gaussian/{relative.as_posix()}",
        )
        upload_count += 1

    report_remote_names = {
        "rtk_prior_report": "rtk_prior_report.json",
        "imu_gravity_report": "imu_gravity_report.json",
        "alignment_transform": "alignment_transform.json",
        "gcp_alignment_report": "gcp_alignment_report.json",
        "facade_frame_report": "facade_frame.json",
        "facade_selection_report": "facade_selection_report.json",
        "gaussian_coverage_report": "gaussian_coverage_report.json",
    }
    for report_name, report_path in assets.required_reports.items():
        if report_path is None or not os.path.isfile(report_path):
            continue
        storage.upload_verified_file(
            report_path,
            f"{mission_s3_prefix}/{report_remote_names[report_name]}",
        )
        upload_count += 1

    if assets.gcp_enabled:
        for required_sparse_file in assets.gcp_sparse_files:
            required_name = os.path.basename(required_sparse_file)
            storage.upload_verified_file(
                required_sparse_file,
                f"{mission_s3_prefix}/colmap/sparse_geo/{required_name}",
            )
            upload_count += 1

    storage.upload_verified_file(
        product_manifest_path,
        f"{mission_s3_prefix}/product_manifest.json",
    )
    upload_count += 1
    runtime.report_mission_progress(
        vol_id,
        "UPLOADING",
        92,
        log="Verified COG, raster metadata and bounded preview uploaded",
    )

    if reconstruction.utm_crs and _publish_camera_projection_index(
        workspace_dir=workspace_dir,
        projected_crs=reconstruction.utm_crs,
        mission_s3_prefix=mission_s3_prefix,
        vol_id=vol_id,
    ):
        upload_count += 1

    # Remaining recovery/debug products are best-effort. They cannot invalidate
    # a successfully published and independently verified orthomosaic.
    upload_count, gaussian_upload_complete = upload_optional_recovery_artifacts(
        geo_data_file=geo_data_file,
        mission_s3_prefix=mission_s3_prefix,
        vol_id=vol_id,
        db_path=db_path,
        sparse_path=sparse_path,
        workspace_dir=workspace_dir,
        gcp_enabled=assets.gcp_enabled,
        dense_path=dense_path,
        durable_checkpoint_dir=durable_checkpoint_dir,
        upload_count=upload_count,
    )

    return PipelinePublicationState(
        ortho_s3_key=ortho_s3_key,
        gaussian_upload_complete=gaussian_upload_complete,
    )


def cleanup_pipeline_workspace(
    workspace_dir: str,
    vol_id: str,
    *,
    final_pass: bool = False,
) -> bool:
    if not os.path.isdir(workspace_dir):
        return True
    try:
        shutil.rmtree(workspace_dir)
    except OSError as cleanup_error:
        log_message = f"Warning: workspace cleanup failed for {workspace_dir}: {cleanup_error}"
        details = {
            "event": "workspace_cleanup_failed",
            "workspace_dir": workspace_dir,
            "final_pass": final_pass,
            "error": f"{type(cleanup_error).__name__}: {cleanup_error}",
        }
        if final_pass:
            logger.warning(log_message, extra={"cleanup": details})
        else:
            runtime.report_mission_progress(
                vol_id,
                "CLEANUP",
                99,
                log=log_message,
                details=details,
            )
        return False

    log_message = f"Local workspace {workspace_dir} cleaned up"
    details = {
        "event": "workspace_cleanup_succeeded",
        "workspace_dir": workspace_dir,
        "final_pass": final_pass,
    }
    if final_pass:
        logger.info(log_message, extra={"cleanup": details})
    else:
        runtime.report_mission_progress(
            vol_id,
            "CLEANUP",
            99,
            log=log_message,
            details=details,
        )
    return True


def complete_colmap_pipeline(
    preparation: PipelinePreparation,
    publication_state: PipelinePublicationState,
    gaussian_state: PipelineGaussianState,
    workspace_dir: str,
    vol_id: str,
    mission_params: dict[str, Any],
) -> None:
    facade_mode = preparation.facade_mode
    ortho_s3_key = publication_state.ortho_s3_key
    gaussian_upload_complete = publication_state.gaussian_upload_complete
    durable_checkpoint_dir = gaussian_state.durable_checkpoint_dir
    checkpoint_s3_prefix = gaussian_state.checkpoint_s3_prefix
    requested_phases = mission_params.get("phases")
    detection_requested = requested_phases is None or "detection" in requested_phases

    cleanup_pipeline_workspace(workspace_dir, vol_id)

    completion_details = None
    completion_log = "Pipeline complete!"
    if facade_mode:
        completion_details = {
            "event": "process_complete",
            "process": "facade",
            "terminal": True,
        }
        completion_log = (
            "Facade orthophoto published in a local coordinate frame; "
            "aerial detection stages were intentionally skipped."
        )
    elif not detection_requested:
        completion_details = {
            "event": "selected_pipeline_complete",
            "terminal": True,
            "selected_phases": requested_phases,
        }
        completion_log = "Detection was not selected; orthomosaic publication is terminal."
    runtime.report_mission_progress(
        vol_id,
        "DONE",
        100,
        status="success",
        log=completion_log,
        details=completion_details,
    )
    if not facade_mode and detection_requested:
        publish_next_stage_message(
            runtime.require_producer(),
            TOPIC_OUT,
            vol_id,
            ortho_s3_key,
            mission_params,
            normalize_ai_backend,
        )
    if gaussian_upload_complete and durable_checkpoint_dir and os.path.isdir(durable_checkpoint_dir):
        try:
            storage.delete_prefix(checkpoint_s3_prefix + "/")
            shutil.rmtree(durable_checkpoint_dir, ignore_errors=True)
            runtime.report_mission_progress(
                vol_id,
                "CLEANUP",
                100,
                status="success",
                log=("Durable DroneGS recovery state retired after PLY/manifest/canary promotion."),
            )
        except Exception as retirement_error:
            runtime.report_mission_progress(
                vol_id,
                "CLEANUP",
                100,
                status="success",
                log=(f"Completed artifacts are promoted; recovery state was retained: {retirement_error}"),
            )
