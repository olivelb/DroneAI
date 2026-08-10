"""One-shot COLMAP stage adapters for bounded Kubernetes Jobs."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pipeline_support import inspect_sparse_quality
from shared.artifact_manifest import ManifestParent
from shared.stage_execution import (
    StageExecutionContext,
    StageExecutionControl,
    StageExecutionResult,
)
from shared.stage_workspace import (
    PublishedWorkspace,
    RestoredWorkspace,
    artifact_manifest_v2_write_enabled,
    publish_workspace,
    publish_workspace_v2,
    restore_workspace_measured,
    workspace_transfer_provenance,
)
from shared.validation import safe_child_path, validate_dataset_prefix

from . import runtime
from .stages.alignment import undistort_and_align_colmap
from .stages.preparation import prepare_colmap_pipeline_run
from .stages.reconstruction import reconstruct_colmap_sparse
from .stages.rtk import refine_colmap_rtk
from .stage_state import STATE_RELATIVE_PATH, write_reconstruction_state
from .stage_state import load_reconstruction_state


def _workspace_path(run_id: str) -> Path:
    root = Path(os.getenv("DRONEAI_STAGE_WORK_ROOT", "/work")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return cast(Path, safe_child_path(root, run_id, field_name="stage run id"))


def _restore_input_workspace(
    context: StageExecutionContext,
    control: StageExecutionControl,
    workspace: Path,
    *,
    expected_kind: str,
) -> RestoredWorkspace:
    if len(context.inputs) != 1 or context.inputs[0].kind != expected_kind:
        raise ValueError(
            f"{context.stage} requires exactly one {expected_kind} artifact"
        )
    source = context.inputs[0]
    manifest_key = source.metadata.get("manifest_key")
    if not isinstance(manifest_key, str) or not manifest_key:
        raise ValueError("Upstream workspace artifact has no manifest key")
    restored = restore_workspace_measured(
        manifest_key,
        workspace,
        source.checksum_sha256,
        cancellation_check=control.raise_if_cancelled,
    )
    return restored


def _publish_stage_workspace(
    context: StageExecutionContext,
    control: StageExecutionControl,
    workspace: Path,
    *,
    stage: str,
    role_overrides: dict[str, str] | None = None,
) -> PublishedWorkspace:
    prefix = (
        f"missions/{context.vol_id}/stage-runs/"
        f"{context.run_id}/{stage}-workspace"
    )
    if not artifact_manifest_v2_write_enabled():
        return publish_workspace(
            workspace,
            prefix,
            cancellation_check=control.raise_if_cancelled,
        )
    parents: list[ManifestParent] = []
    for source in context.inputs:
        manifest_key = source.metadata.get("manifest_key")
        if not isinstance(manifest_key, str) or not manifest_key:
            raise ValueError("Upstream workspace artifact has no manifest key")
        parents.append(
            ManifestParent(
                artifact_id=source.artifact_id,
                manifest_key=manifest_key,
                checksum_sha256=source.checksum_sha256,
            )
        )
    return publish_workspace_v2(
        workspace,
        prefix,
        default_role=f"{stage}-workspace",
        role_overrides=role_overrides,
        parents=tuple(parents),
        cancellation_check=control.raise_if_cancelled,
    )


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
            {
                **context.mission_parameters,
                "stage_parameters": context.parameters,
            },
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
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="reconstruction",
            role_overrides={
                STATE_RELATIVE_PATH.as_posix(): "reconstruction-state",
            },
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
                "workspace_transfer": workspace_transfer_provenance(published),
            },
        )
    finally:
        runtime.cancellation_state.clear()
        if workspace.exists():
            shutil.rmtree(workspace)


def run_gaussian_training_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Train and publish an unfiltered Gaussian model from reconstruction."""
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    runtime.cancellation_state.start_mission(
        context.vol_id,
        context.mission_attempt,
    )
    try:
        restored = _restore_input_workspace(
            context,
            control,
            workspace,
            expected_kind="reconstruction_workspace",
        )
        preparation, reconstruction, alignment = load_reconstruction_state(workspace)
        from gaussian_ortho.generate_gaussian_orthophoto import (
            execute_gaussian_training_phase,
        )
        from gaussian_ortho.phase_artifacts import write_training_artifact

        from .stages.gaussian import prepare_gaussian_product_run

        product = prepare_gaussian_product_run(
            preparation,
            reconstruction,
            alignment,
            str(workspace),
            context.vol_id,
        )
        phase = execute_gaussian_training_phase(
            product.config,
            trainer_backend=product.trainer_backend,
        )
        control.raise_if_cancelled()
        model_path = workspace / ".droneai" / "gaussian" / "training" / "final.ply"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(phase.training_state.final_ply, model_path)
        write_training_artifact(
            workspace,
            product.config,
            phase,
            model_path=model_path,
        )
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="gaussian-training",
            role_overrides={
                ".droneai/gaussian-training-state.json": "gaussian-training-state",
                model_path.relative_to(workspace).as_posix(): "gaussian-model",
            },
        )
        capacity_plan = (
            phase.capacity_plan.as_dict()
            if phase.capacity_plan is not None
            else None
        )
        return StageExecutionResult(
            kind="gaussian_training_workspace",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata={
                "manifest_key": published.manifest_key,
                "file_count": published.file_count,
                "state_file": ".droneai/gaussian-training-state.json",
                "model_file": model_path.relative_to(workspace).as_posix(),
                "gaussian_count": phase.training_state.merged_model.num_gaussians,
                "gaussian_capacity": capacity_plan,
            },
            quality_metrics={
                "gaussian_count": phase.training_state.merged_model.num_gaussians,
            },
            provenance={
                "stage_adapter": "gaussian-training-v1",
                "backend": phase.backend_name,
                "trainer_binary_sha256": phase.trainer_binary_sha256,
                "profile_id": product.config.dronegs_profile_id,
                "gaussian_capacity": capacity_plan,
                "workspace_transfer": workspace_transfer_provenance(
                    published,
                    restored,
                ),
            },
        )
    finally:
        runtime.cancellation_state.clear()
        if workspace.exists():
            shutil.rmtree(workspace)


def run_gaussian_filtering_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Filter a verified training model once and persist raster geometry."""
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    runtime.cancellation_state.start_mission(
        context.vol_id,
        context.mission_attempt,
    )
    try:
        restored = _restore_input_workspace(
            context,
            control,
            workspace,
            expected_kind="gaussian_training_workspace",
        )
        preparation, reconstruction, alignment = load_reconstruction_state(workspace)
        from gaussian_ortho.generate_gaussian_orthophoto import (
            execute_gaussian_filtering_phase,
            prepare_gaussian_scene,
        )
        from gaussian_ortho.gaussian_model import GaussianModel
        from gaussian_ortho.phase_artifacts import (
            hydrate_training_phase,
            read_training_artifact,
            write_filtering_artifact,
        )

        from .stages.gaussian import prepare_gaussian_product_run

        product = prepare_gaussian_product_run(
            preparation,
            reconstruction,
            alignment,
            str(workspace),
            context.vol_id,
            prepare_checkpoints=False,
        )
        artifact = read_training_artifact(workspace, product.config)
        model = GaussianModel(
            sh_degree=product.config.sh_degree,
            fagk_enabled=product.config.fagk,
        )
        model.load_ply(str(artifact.model_path))
        scene = prepare_gaussian_scene(product.config)
        filtered_model_path = (
            workspace / ".droneai" / "gaussian" / "filtering" / "filtered.ply"
        )
        filtered_model_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact.model_path, filtered_model_path)
        training_phase = hydrate_training_phase(
            replace(artifact, model_path=filtered_model_path),
            scene,
            model,
        )
        filtering_phase = execute_gaussian_filtering_phase(
            product.config,
            training_phase,
        )
        control.raise_if_cancelled()
        write_filtering_artifact(
            workspace,
            product.config,
            training_phase,
            filtering_phase,
            model_path=filtered_model_path,
        )
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="gaussian-filtering",
            role_overrides={
                ".droneai/gaussian-filtering-state.json": "gaussian-filtering-state",
                filtered_model_path.relative_to(workspace).as_posix(): (
                    "filtered-gaussian-model"
                ),
            },
        )
        return StageExecutionResult(
            kind="gaussian_filtering_workspace",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata={
                "manifest_key": published.manifest_key,
                "file_count": published.file_count,
                "state_file": ".droneai/gaussian-filtering-state.json",
                "model_file": filtered_model_path.relative_to(workspace).as_posix(),
                "input_gaussians": filtering_phase.input_gaussians,
                "output_gaussians": filtering_phase.output_gaussians,
            },
            quality_metrics={
                "input_gaussians": filtering_phase.input_gaussians,
                "output_gaussians": filtering_phase.output_gaussians,
                "retained_ratio": (
                    filtering_phase.output_gaussians
                    / max(1, filtering_phase.input_gaussians)
                ),
            },
            provenance={
                "stage_adapter": "gaussian-filtering-v1",
                "profile_id": product.config.dronegs_profile_id,
                "workspace_transfer": workspace_transfer_provenance(
                    published,
                    restored,
                ),
            },
        )
    finally:
        runtime.cancellation_state.clear()
        if workspace.exists():
            shutil.rmtree(workspace)


def run_rasterization_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Render and qualify GeoTIFF products from an already filtered model."""
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    runtime.cancellation_state.start_mission(
        context.vol_id,
        context.mission_attempt,
    )
    try:
        restored = _restore_input_workspace(
            context,
            control,
            workspace,
            expected_kind="gaussian_filtering_workspace",
        )
        preparation, reconstruction, alignment = load_reconstruction_state(workspace)
        from gaussian_ortho.generate_gaussian_orthophoto import (
            execute_gaussian_rasterization_phase,
        )
        from gaussian_ortho.gaussian_model import GaussianModel
        from gaussian_ortho.phase_artifacts import (
            hydrate_filtering_phase,
            read_filtering_artifact,
        )
        from gaussian_ortho.raster_product import finalize_gaussian_raster_product

        from .stages.gaussian import prepare_gaussian_product_run

        product = prepare_gaussian_product_run(
            preparation,
            reconstruction,
            alignment,
            str(workspace),
            context.vol_id,
            prepare_checkpoints=False,
        )
        artifact = read_filtering_artifact(workspace, product.config)
        model = GaussianModel(
            sh_degree=product.config.sh_degree,
            fagk_enabled=product.config.fagk,
        )
        model.load_ply(str(artifact.model_path))
        filtering_phase = hydrate_filtering_phase(artifact, model)
        rasterization_phase = execute_gaussian_rasterization_phase(
            product.config,
            filtering_phase,
        )
        control.raise_if_cancelled()
        import cupy as cp

        result = finalize_gaussian_raster_product(
            product.config,
            filtering_phase,
            rasterization_phase,
            artifact.scene_summary,
            final_ply=str(artifact.model_path),
            cupy_version=cp.__version__,
        )
        ortho_relative = Path(result["ortho_file"]).relative_to(workspace).as_posix()
        height_relative = Path(result["height_file"]).relative_to(workspace).as_posix()
        coverage_relative = (
            Path(result["gaussian_coverage_report"])
            .relative_to(workspace)
            .as_posix()
            if result.get("gaussian_coverage_report")
            else None
        )
        raster_roles = {
            ortho_relative: "raster-orthomosaic",
            height_relative: "raster-height",
        }
        if coverage_relative is not None:
            raster_roles[coverage_relative] = "raster-coverage-report"
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="rasterization",
            role_overrides=raster_roles,
        )
        coverage = cast(dict[str, Any] | None, result.get("gaussian_coverage"))
        quality_metrics: dict[str, Any] = {
            "width": result["width"],
            "height": result["height"],
            "gaussian_count": result["n_gaussians"],
        }
        if coverage is not None:
            quality_metrics["coverage"] = coverage
        return StageExecutionResult(
            kind="raster_product_workspace",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata={
                "manifest_key": published.manifest_key,
                "file_count": published.file_count,
                "ortho_file": ortho_relative,
                "height_file": height_relative,
                "coverage_report": coverage_relative,
                "crs": result["coordinate_system"],
                "raster_extent": result["raster_extent"],
            },
            quality_metrics=quality_metrics,
            provenance={
                "stage_adapter": "gaussian-rasterization-v1",
                "profile_id": product.config.dronegs_profile_id,
                "renderer_contract": result["renderer_contract"],
                "cupy_version": result["cupy_version"],
                "workspace_transfer": workspace_transfer_provenance(
                    published,
                    restored,
                ),
            },
        )
    finally:
        runtime.cancellation_state.clear()
        if workspace.exists():
            shutil.rmtree(workspace)
