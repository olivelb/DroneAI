"""Recovery of raster-ready resident Gaussian buffers without retraining."""

from __future__ import annotations

import gc
from pathlib import Path

import numpy as np

from .capacity_planning import assess_gaussian_density
from .generate_gaussian_orthophoto import (
    GaussianFilteredPartition,
    GaussianFilteringPhaseState,
    GaussianOrthoConfig,
    GaussianPartitionModel,
    GaussianTrainingPhaseState,
    GaussianTrainingState,
    _apply_required_resident_partition,
    _plan_scene_capacity,
    _report,
    detected_vram_bytes,
    prepare_gaussian_scene,
)
from .ortho_renderer import compute_ortho_extent
from .phase_artifacts import (
    FILTERING_ARTIFACT_PATH,
    hydrate_partitioned_filtering_phase,
    read_filtering_artifact,
    write_filtering_artifact,
)
from .ply_stream import (
    FEATHERED_MERGE_COMMENT,
    PartitionCorePly,
    count_partition_core_vertices,
    merge_partition_buffers_to_ply,
)
from .raster_product import GaussianSceneSummary
from .render_geometry import GaussianRenderGeometry


def _scene_summary(
    scene: object,
    *,
    facade_subset_result: dict[str, object] | None = None,
) -> GaussianSceneSummary:
    return GaussianSceneSummary(
        sim3_aligned=scene.transform_data is not None,
        exif_altitude_available=scene.mean_exif_alt is not None,
        colmap_to_meters=scene.colmap_to_meters,
        scale_source=scene.scale_source,
        facade_frame=(scene.facade_frame.as_dict() if scene.facade_frame is not None else None),
        registered_camera_count=len(scene.registered_cameras),
        texture_camera_count=scene.texture_camera_count,
        texture_filter_applied=scene.texture_filter_applied,
        minimum_sparse_observations=scene.minimum_sparse_observations,
        seed_max_error=scene.seed_max_error,
        seed_min_track=scene.seed_min_track,
        gaussian_seed_point_count=scene.gaussian_seed_point_count,
        facade_subset_result=facade_subset_result,
    )


def _merge_unified_ply(
    filtering_phase: GaussianFilteringPhaseState,
    unified_ply_file: str | None,
    *,
    expected_core_gaussians: int | None,
    config: GaussianOrthoConfig,
) -> None:
    if unified_ply_file is None:
        return
    output = Path(unified_ply_file)
    if output.is_file():
        from .ply_stream import read_binary_ply_layout

        layout = read_binary_ply_layout(output)
        if FEATHERED_MERGE_COMMENT in layout.comments:
            _report(
                config.vol_id,
                "GAUSS",
                95,
                "Reusing validated seam-safe unified PLY "
                f"({layout.vertex_count:,} weighted Gaussians)",
                config.report_fn,
            )
            return
        raise RuntimeError(
            "Existing unified PLY does not use the seam-safe opacity-feather "
            "contract; choose a new output path to preserve the old evidence"
        )
    result = merge_partition_buffers_to_ply(
        (
            PartitionCorePly(partition.bounds, Path(partition.model_path))
            for partition in filtering_phase.partition_models
        ),
        output,
    )
    _report(
        config.vol_id,
        "GAUSS",
        95,
        f"Saved seam-safe unified PLY: {result.path} "
        f"({result.vertex_count:,} opacity-weighted Gaussians from "
        f"{result.source_vertex_count:,} buffer records, "
        f"{result.size_bytes / 1024**3:.2f} GiB)",
        config.report_fn,
    )


def _load_cached_artifact(
    config: GaussianOrthoConfig,
) -> tuple[GaussianFilteringPhaseState, GaussianSceneSummary] | None:
    workspace = Path(config.checkpoint_dir)
    if not (workspace / FILTERING_ARTIFACT_PATH).is_file():
        return None
    artifact = read_filtering_artifact(workspace, config)
    filtering_phase = hydrate_partitioned_filtering_phase(artifact)
    _report(
        config.vol_id,
        "GAUSS",
        94,
        f"Restored portable filtering artifact with "
        f"{filtering_phase.output_gaussians:,} unique core Gaussians",
        config.report_fn,
    )
    return filtering_phase, artifact.scene_summary


def recover_partitioned_filtering_phase(
    config: GaussianOrthoConfig,
    *,
    expected_core_gaussians: int | None,
    unified_ply_file: str | None,
    cupy_module: object,
) -> tuple[GaussianFilteringPhaseState, GaussianSceneSummary]:
    """Hydrate already-filtered ``cell_N/buffer.ply`` files for rendering."""

    checkpoint_root = Path(config.checkpoint_dir).resolve(strict=True)
    if config.render_mode != "facade":
        raise ValueError("Filtered partition recovery currently supports facade products only")
    cached = _load_cached_artifact(config)
    if cached is not None:
        filtering_phase, summary = cached
        if (
            expected_core_gaussians is not None
            and filtering_phase.output_gaussians != expected_core_gaussians
        ):
            raise RuntimeError(
                "Recovered filtering artifact count drifted: "
                f"{filtering_phase.output_gaussians:,} versus expected "
                f"{expected_core_gaussians:,}"
            )
        _merge_unified_ply(
            filtering_phase,
            unified_ply_file,
            expected_core_gaussians=expected_core_gaussians,
            config=config,
        )
        return filtering_phase, summary

    scene = prepare_gaussian_scene(config)
    overlap = float(config.partition_overlap)
    detected_vram = detected_vram_bytes(cupy_module)
    preliminary_plan = _plan_scene_capacity(
        config,
        scene,
        vram_bytes=detected_vram,
        overlap=overlap,
        cell_count=1,
    )
    _apply_required_resident_partition(
        scene,
        config,
        required_cell_count=preliminary_plan.required_cell_count,
    )
    capacity_plan = _plan_scene_capacity(
        config,
        scene,
        vram_bytes=detected_vram,
        overlap=overlap,
        cell_count=len(scene.cells),
    )
    if not capacity_plan.cells_sufficient:
        raise RuntimeError("Recovered resident grid is below the density plan")

    from .gaussian_model import GaussianModel

    filtered: list[GaussianFilteredPartition] = []
    training_partitions: list[GaussianPartitionModel] = []
    extents: list[tuple[float, float, float, float, float, float]] = []
    training_sources: list[PartitionCorePly] = []
    for index, (bounds, _cell_scene) in enumerate(scene.cells):
        if bounds is None:
            raise RuntimeError("Recovered resident cell has no core bounds")
        cell_root = checkpoint_root / f"cell_{index}"
        buffer_path = cell_root / "buffer.ply"
        training_path = cell_root / "point_cloud.ply"
        if not buffer_path.is_file() or not training_path.is_file():
            raise FileNotFoundError(
                f"Resident recovery is missing cell_{index} PLY products"
            )
        model = GaussianModel(
            sh_degree=config.sh_degree,
            opacity_sh_enabled=config.opacity_sh_enabled,
        )
        model.load_ply(str(buffer_path))
        model.active_sh_degree = config.sh_degree
        core_mask = bounds.core_mask(model.positions, array_module=cupy_module)
        core_count = int(cupy_module.count_nonzero(core_mask).item())
        if core_count == 0:
            raise RuntimeError(f"Recovered cell_{index} has an empty unique core")
        frame_origin = scene.facade_frame.origin if scene.facade_frame is not None else None
        rotation_geo = scene.facade_frame.world_to_facade if scene.facade_frame is not None else None
        extent = compute_ortho_extent(
            model,
            pad=(1.0 / scene.colmap_to_meters if config.render_mode == "facade" else 2.0),
            R_geo=rotation_geo,
            frame_origin=frame_origin,
            quantile=0.001,
        )
        filtered.append(
            GaussianFilteredPartition(
                bounds=bounds,
                model_path=str(buffer_path),
                gaussian_count=model.num_gaussians,
                core_gaussian_count=core_count,
                render_extent=extent,
            )
        )
        training_partitions.append(
            GaussianPartitionModel(
                bounds=bounds,
                model_path=str(buffer_path),
                gaussian_count=model.num_gaussians,
                core_gaussian_count=core_count,
            )
        )
        training_sources.append(PartitionCorePly(bounds, training_path))
        extents.append(extent)
        _report(
            config.vol_id,
            "GAUSS",
            90 + int(4 * (index + 1) / len(scene.cells)),
            f"Verified filtered resident buffer {index + 1}/{len(scene.cells)}: "
            f"{core_count:,} unique core Gaussians",
            config.report_fn,
        )
        del model, core_mask
        gc.collect()
        cupy_module.get_default_memory_pool().free_all_blocks()

    output_gaussians = sum(partition.core_gaussian_count for partition in filtered)
    if expected_core_gaussians is not None and output_gaussians != expected_core_gaussians:
        raise RuntimeError(
            f"Recovered filtered core count drifted: {output_gaussians:,} "
            f"versus expected {expected_core_gaussians:,}"
        )
    input_gaussians = count_partition_core_vertices(training_sources)
    density = assess_gaussian_density(
        capacity_plan,
        actual_gaussian_count=output_gaussians,
    )
    local_gsd = config.resolution / scene.colmap_to_meters
    coordinate_scale = local_gsd / config.resolution
    geometry = GaussianRenderGeometry(
        geo_origin=np.zeros(3, dtype=np.float64),
        frame_origin=(scene.facade_frame.origin if scene.facade_frame is not None else None),
        rotation_geo=(scene.facade_frame.world_to_facade if scene.facade_frame is not None else None),
        sh_direction_rotation=None,
        facade_depth_bounds_model=None,
        render_extent=(
            min(part.bounds.core_x_min for part in filtered) * coordinate_scale,
            max(part.bounds.core_x_max for part in filtered) * coordinate_scale,
            min(part.bounds.core_y_min for part in filtered) * coordinate_scale,
            max(part.bounds.core_y_max for part in filtered) * coordinate_scale,
            min(extent[4] for extent in extents),
            max(extent[5] for extent in extents),
        ),
        local_gsd=local_gsd,
        resolution_units=("metres" if scene.scale_source != "model-units" else "model-units"),
        coverage_camera_positions=np.empty((0, 3), dtype=np.float64),
    )
    filtering_phase = GaussianFilteringPhaseState(
        render_state=None,
        input_gaussians=input_gaussians,
        output_gaussians=output_gaussians,
        density_assessment=density,
        partition_geometry=geometry,
        partition_models=tuple(filtered),
    )
    training_phase = GaussianTrainingPhaseState(
        scene_state=scene,
        training_state=GaussianTrainingState(
            merged_model=None,
            final_ply=None,
            facade_subset_result=None,
            partition_models=tuple(training_partitions),
        ),
        backend_name="filtered-partition-recovery",
        trainer_binary_sha256="recovered-from-validated-cell-products",
        capacity_plan=capacity_plan,
    )
    write_filtering_artifact(
        checkpoint_root,
        config,
        training_phase,
        filtering_phase,
        model_path=None,
    )
    _merge_unified_ply(
        filtering_phase,
        unified_ply_file,
        expected_core_gaussians=expected_core_gaussians,
        config=config,
    )
    return filtering_phase, _scene_summary(scene)
