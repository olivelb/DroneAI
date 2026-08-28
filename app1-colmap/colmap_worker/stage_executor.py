"""One-shot COLMAP stage adapters for bounded Kubernetes Jobs."""

from __future__ import annotations

import os
import json
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
    publish_workspace,
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
        expected_organization_id=context.organization_id,
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
    prefix = context.object_namespace.key(
        "stage-runs",
        context.run_id,
        f"{stage}-workspace",
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
    return publish_workspace(
        workspace,
        prefix,
        default_role=f"{stage}-workspace",
        role_overrides=role_overrides,
        parents=tuple(parents),
        organization_id=context.organization_id,
        cancellation_check=control.raise_if_cancelled,
    )


def _publish_product_workspace(
    context: StageExecutionContext,
    control: StageExecutionControl,
    product_root: Path,
    *,
    stage: str,
    default_role: str,
    role_overrides: dict[str, str] | None = None,
) -> PublishedWorkspace:
    """Publish only a derived product while lineage remains in artifact edges."""

    prefix = context.object_namespace.key(
        "stage-runs",
        context.run_id,
        f"{stage}-workspace",
    )
    return publish_workspace(
        product_root,
        prefix,
        default_role=default_role,
        role_overrides=role_overrides,
        parents=(),
        organization_id=context.organization_id,
        cancellation_check=control.raise_if_cancelled,
    )


def _prepare_stage_workspace(context: StageExecutionContext) -> Path:
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    try:
        runtime.cancellation_state.start_mission(
            context.vol_id,
            context.mission_attempt,
            organization_id=context.organization_id,
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    return workspace


def _cleanup_stage_workspace(workspace: Path) -> None:
    try:
        runtime.cancellation_state.clear()
    finally:
        if workspace.exists():
            shutil.rmtree(workspace)


def run_reconstruction_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Build and publish the aligned COLMAP workspace needed by DroneGS."""
    input_dataset = validate_dataset_prefix(
        str(context.mission_parameters.get("input_dataset") or "")
    )
    workspace = _prepare_stage_workspace(context)

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
                "organization_id": context.organization_id,
                "workspace_prefix": context.workspace_prefix,
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
        _cleanup_stage_workspace(workspace)


def run_gaussian_training_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Train and publish an unfiltered Gaussian model from reconstruction."""
    workspace = _prepare_stage_workspace(context)
    try:
        restored = _restore_input_workspace(
            context,
            control,
            workspace,
            expected_kind="reconstruction_workspace",
        )
        preparation, reconstruction, alignment = load_reconstruction_state(workspace)
        from gaussian_ortho.generate_gaussian_orthophoto import (
            GaussianPartitionModel,
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
        model_root = workspace / ".droneai" / "gaussian" / "training"
        model_root.mkdir(parents=True, exist_ok=True)
        model_path: Path | None = None
        partition_models = tuple(
            getattr(phase.training_state, "partition_models", ())
        )
        if partition_models:
            copied_partitions: list[GaussianPartitionModel] = []
            for partition in partition_models:
                target = (
                    model_root
                    / "partitions"
                    / f"cell-{partition.bounds.row}-{partition.bounds.col}.ply"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(partition.model_path, target)
                copied_partitions.append(
                    replace(partition, model_path=str(target))
                )
            phase = replace(
                phase,
                training_state=replace(
                    phase.training_state,
                    partition_models=tuple(copied_partitions),
                ),
            )
        else:
            if phase.training_state.final_ply is None:
                raise RuntimeError("Gaussian training produced no publishable model")
            model_path = model_root / "final.ply"
            shutil.copy2(phase.training_state.final_ply, model_path)
        write_training_artifact(
            workspace,
            product.config,
            phase,
            model_path=model_path,
        )
        role_overrides = {
            ".droneai/gaussian-training-state.json": "gaussian-training-state",
        }
        if model_path is not None:
            role_overrides[model_path.relative_to(workspace).as_posix()] = (
                "gaussian-model"
            )
        for partition in getattr(phase.training_state, "partition_models", ()):
            role_overrides[
                Path(partition.model_path).relative_to(workspace).as_posix()
            ] = "gaussian-partition-model"
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="gaussian-training",
            role_overrides=role_overrides,
        )
        capacity_plan = (
            phase.capacity_plan.as_dict()
            if phase.capacity_plan is not None
            else None
        )
        training_gaussian_count = getattr(
            phase.training_state,
            "total_gaussians",
            None,
        )
        if training_gaussian_count is None:
            training_gaussian_count = (
                phase.training_state.merged_model.num_gaussians
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
                "model_file": (
                    model_path.relative_to(workspace).as_posix()
                    if model_path is not None
                    else None
                ),
                "model_files": [
                    Path(partition.model_path).relative_to(workspace).as_posix()
                    for partition in getattr(
                        phase.training_state,
                        "partition_models",
                        (),
                    )
                ],
                "gaussian_count": int(training_gaussian_count),
                "gaussian_capacity": capacity_plan,
            },
            quality_metrics={
                "gaussian_count": int(training_gaussian_count),
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
        _cleanup_stage_workspace(workspace)


def run_gaussian_filtering_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Filter a verified training model once and persist raster geometry."""
    workspace = _prepare_stage_workspace(context)
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
            execute_partitioned_gaussian_filtering_phase,
            prepare_gaussian_scene,
        )
        from gaussian_ortho.gaussian_model import GaussianModel
        from gaussian_ortho.phase_artifacts import (
            hydrate_training_phase,
            hydrate_partitioned_training_phase,
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
        scene = prepare_gaussian_scene(product.config)
        filtered_root = workspace / ".droneai" / "gaussian" / "filtering"
        filtered_root.mkdir(parents=True, exist_ok=True)
        filtered_model_path: Path | None = None
        if getattr(artifact, "partition_models", ()):
            if artifact.capacity_plan is None:
                raise RuntimeError("Resident Gaussian artifact has no capacity plan")
            copied_artifact_partitions = []
            for partition in artifact.partition_models:
                target = (
                    filtered_root
                    / "partitions"
                    / f"cell-{partition.bounds.row}-{partition.bounds.col}.ply"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(partition.model_path, target)
                copied_artifact_partitions.append(
                    replace(partition, model_path=target)
                )
            artifact = replace(
                artifact,
                partition_models=tuple(copied_artifact_partitions),
            )
            training_phase = hydrate_partitioned_training_phase(artifact, scene)
            filtering_phase = execute_partitioned_gaussian_filtering_phase(
                product.config,
                scene,
                training_phase.training_state.partition_models,
                artifact.capacity_plan,
            )
        else:
            if artifact.model_path is None:
                raise RuntimeError("Gaussian training artifact has no global model")
            model = GaussianModel(
                sh_degree=product.config.sh_degree,
                opacity_sh_enabled=product.config.opacity_sh_enabled,
            )
            model.load_ply(str(artifact.model_path))
            filtered_model_path = filtered_root / "filtered.ply"
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
        role_overrides = {
            ".droneai/gaussian-filtering-state.json": "gaussian-filtering-state",
        }
        if filtered_model_path is not None:
            role_overrides[
                filtered_model_path.relative_to(workspace).as_posix()
            ] = "filtered-gaussian-model"
        for partition in getattr(filtering_phase, "partition_models", ()):
            role_overrides[
                Path(partition.model_path).relative_to(workspace).as_posix()
            ] = "filtered-gaussian-partition-model"
        published = _publish_stage_workspace(
            context,
            control,
            workspace,
            stage="gaussian-filtering",
            role_overrides=role_overrides,
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
                "model_file": (
                    filtered_model_path.relative_to(workspace).as_posix()
                    if filtered_model_path is not None
                    else None
                ),
                "model_files": [
                    Path(partition.model_path).relative_to(workspace).as_posix()
                    for partition in getattr(
                        filtering_phase,
                        "partition_models",
                        (),
                    )
                ],
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
        _cleanup_stage_workspace(workspace)


def run_rasterization_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Render and qualify GeoTIFF products from an already filtered model."""
    workspace = _prepare_stage_workspace(context)
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
            hydrate_partitioned_filtering_phase,
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
        if getattr(artifact, "partition_models", ()):
            filtering_phase = hydrate_partitioned_filtering_phase(artifact)
        else:
            if artifact.model_path is None:
                raise RuntimeError("Gaussian filtering artifact has no global model")
            model = GaussianModel(
                sh_degree=product.config.sh_degree,
                opacity_sh_enabled=product.config.opacity_sh_enabled,
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
            final_ply=(
                str(artifact.model_path)
                if artifact.model_path is not None
                else None
            ),
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
        seam_relative = (
            Path(result["gaussian_seam_report"])
            .relative_to(workspace)
            .as_posix()
            if result.get("gaussian_seam_report")
            else None
        )
        raster_roles = {
            ortho_relative: "raster-orthomosaic",
            height_relative: "raster-height",
        }
        if coverage_relative is not None:
            raster_roles[coverage_relative] = "raster-coverage-report"
        if seam_relative is not None:
            raster_roles[seam_relative] = "raster-seam-report"
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
        if result.get("gaussian_density") is not None:
            quality_metrics["gaussian_density"] = result["gaussian_density"]
        if result.get("gaussian_seams") is not None:
            quality_metrics["gaussian_seams"] = result["gaussian_seams"]
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
                "seam_report": seam_relative,
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
        _cleanup_stage_workspace(workspace)


def run_gaussian_viewer_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    """Build an immutable CPU-only GSTile bundle from filtered Gaussians."""

    workspace = _prepare_stage_workspace(context)
    try:
        restored = _restore_input_workspace(
            context,
            control,
            workspace,
            expected_kind="gaussian_filtering_workspace",
        )
        preparation, reconstruction, alignment = load_reconstruction_state(workspace)
        from gaussian_ortho.phase_artifacts import read_filtering_artifact
        from gaussian_ortho.ply_stream import (
            PartitionCorePly,
            merge_partition_buffers_to_ply,
        )
        from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle
        from shared.gstile_defaults import GSTILE_DEFAULTS_PROFILE

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
        source_merge: dict[str, Any]
        if artifact.partition_models:
            source_path = workspace / ".droneai" / "gaussian-viewer-source.ply"
            merge = merge_partition_buffers_to_ply(
                (
                    PartitionCorePly(partition.bounds, partition.model_path)
                    for partition in artifact.partition_models
                ),
                source_path,
            )
            source_merge = {
                "algorithm": merge.algorithm,
                "source_vertex_count": merge.source_vertex_count,
                "viewer_gaussian_count": merge.vertex_count,
            }
        else:
            if artifact.model_path is None:
                raise RuntimeError("Gaussian filtering artifact has no model")
            source_path = artifact.model_path
            source_merge = {
                "algorithm": "resident-filtered-model-v1",
                "source_vertex_count": artifact.output_gaussians,
                "viewer_gaussian_count": artifact.output_gaussians,
            }

        raw_options = context.parameters.get("gaussian_viewer") or {}
        if not isinstance(raw_options, dict):
            raise ValueError("gaussian_viewer stage parameters must be an object")
        output_root = workspace / ".droneai" / "gaussian-viewer"
        temporary_root = workspace / ".droneai" / "gaussian-viewer-temporary"
        defaults = GsTileBuildOptions()
        build_options = GsTileBuildOptions(
                leaf_size=int(raw_options.get("leaf_size", 65_536)),
                chunk_records=int(raw_options.get("chunk_records", 131_072)),
                maximum_depth=int(raw_options.get("maximum_depth", 48)),
                lod_proxy_size=(
                    int(raw_options.get("lod_proxy_size", defaults.lod_proxy_size))
                    if raw_options.get("lod_proxy_size", defaults.lod_proxy_size) is not None
                    else None
                ),
                lod_proxy_strategy=raw_options.get("lod_proxy_strategy", defaults.lod_proxy_strategy),
                pack_target_bytes=(
                    int(raw_options.get("pack_target_bytes", defaults.pack_target_bytes))
                    if raw_options.get("pack_target_bytes", defaults.pack_target_bytes) is not None
                    else None
                ),
                pack_workers=raw_options.get("pack_workers", defaults.pack_workers),
                pack_pending_bytes=raw_options.get("pack_pending_bytes", defaults.pack_pending_bytes),
                temporary_root=temporary_root,
                cancellation_check=control.raise_if_cancelled,
                invisible_gaussian_scale_threshold=(
                    float(raw_options["filter_invisible_giant_scale"])
                    if raw_options.get("filter_invisible_giant_scale") is not None
                    else None
                ),
                visibility_opacity_threshold=float(
                    raw_options.get("filter_visibility_opacity", 0.05)
                ),
        )
        result = build_gstile_bundle(source_path, output_root, options=build_options)
        control.raise_if_cancelled()
        if artifact.partition_models:
            source_path.unlink()
        manifest = cast(
            dict[str, Any],
            json.loads(result.manifest_path.read_text(encoding="ascii")),
        )
        published = _publish_product_workspace(
            context,
            control,
            output_root,
            stage="gaussian-viewer",
            default_role="gaussian-viewer-pack",
            role_overrides={"manifest.json": "gaussian-viewer-manifest"},
        )
        viewer_metadata: dict[str, Any] = {
            "build_configuration": {
                "defaults_profile": GSTILE_DEFAULTS_PROFILE,
                "leaf_size": build_options.leaf_size,
                "chunk_records": build_options.chunk_records,
                "lod_proxy_size": build_options.lod_proxy_size,
                "lod_proxy_strategy": build_options.lod_proxy_strategy,
                "pack_target_bytes": build_options.pack_target_bytes,
                "pack_workers": build_options.pack_workers,
                "pack_pending_bytes": build_options.pack_pending_bytes,
            },
            "manifest_key": published.manifest_key,
            "file_count": published.file_count,
            "viewer_manifest_file": "manifest.json",
            "bundle_id": result.bundle_id,
            "source_filtered_sha256": manifest["source"]["sha256"],
            "gaussian_count": result.gaussian_count,
            "leaf_count": result.leaf_count,
            "pack_bytes": result.pack_bytes,
            "profile": manifest["profile"],
            "lod": manifest["statistics"]["lod"],
        }
        facade_frame = artifact.scene_summary.facade_frame
        if facade_frame is not None:
            axes = cast(dict[str, object], facade_frame["axes_world"])
            viewer_metadata["recommended_view"] = {
                "kind": "facade",
                "right": axes["horizontal"],
                "up": axes["vertical"],
                "outward": axes["outward_normal"],
            }
        return StageExecutionResult(
            kind="gaussian_viewer_bundle",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata=viewer_metadata,
            quality_metrics={
                "gaussian_count": result.gaussian_count,
                "leaf_count": result.leaf_count,
                "bytes_per_gaussian": result.pack_bytes / result.gaussian_count,
                "maximum_quantization_error": result.maximum_errors,
                "scientific_qualification": "quantization-bounds-only",
            },
            provenance={
                "stage_adapter": "gaussian-viewer-v1",
                "build_configuration": viewer_metadata["build_configuration"],
                "tiler_profile": manifest["profile"],
                "lod_profile": manifest["statistics"]["lod"],
                "source_merge": source_merge,
                "workspace_transfer": workspace_transfer_provenance(
                    published,
                    restored,
                ),
            },
        )
    finally:
        _cleanup_stage_workspace(workspace)
