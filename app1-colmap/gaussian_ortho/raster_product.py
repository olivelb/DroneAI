"""Shared qualification and GeoTIFF publication for Gaussian raster paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from .coverage_quality import (
    SpatialCoveragePolicy,
    SpatialCoverageReport,
    evaluate_spatial_coverage,
)
from .generate_gaussian_orthophoto import _report
from .geo_writer import write_geotiff
from .height_reference import georeference_height_map, georeference_raster_origin

if TYPE_CHECKING:
    from .generate_gaussian_orthophoto import (
        GaussianFilteringPhaseState,
        GaussianOrthoConfig,
        GaussianRasterizationPhaseState,
    )
    from .render_geometry import GaussianRenderGeometry


@dataclass(frozen=True)
class GaussianSceneSummary:
    """Portable scene facts needed to qualify and describe the final raster."""

    sim3_aligned: bool
    exif_altitude_available: bool
    colmap_to_meters: float
    scale_source: str
    facade_frame: dict[str, object] | None
    registered_camera_count: int
    texture_camera_count: int
    texture_filter_applied: bool
    minimum_sparse_observations: int
    seed_max_error: float
    seed_min_track: int
    gaussian_seed_point_count: int
    facade_subset_result: dict[str, object] | None


def _render_geometry(
    filtering_phase: GaussianFilteringPhaseState,
) -> GaussianRenderGeometry:
    render = filtering_phase.render_state or filtering_phase.partition_geometry
    if render is None:
        raise RuntimeError("Gaussian raster product has no render geometry")
    return render


def _write_json_report(report_path: str, report: object) -> None:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_coverage_report(
    config: GaussianOrthoConfig,
    filtering_phase: GaussianFilteringPhaseState,
    height: Any,
    extent: tuple[float, float, float, float],
) -> tuple[str | None, SpatialCoverageReport | None]:
    if config.render_mode != "map":
        return None, None
    policy = SpatialCoveragePolicy(
        grid_size=config.coverage_grid_size,
        minimum_valid_ratio=config.coverage_min_valid_ratio,
        cell_coverage_threshold=config.coverage_cell_threshold,
        minimum_covered_cells_ratio=config.coverage_min_covered_cells_ratio,
        minimum_worst_cell_ratio=config.coverage_min_worst_cell_ratio,
        minimum_camera_cell_ratio=config.coverage_min_camera_cell_ratio,
    )
    report = evaluate_spatial_coverage(
        height,
        extent=extent,
        camera_positions=_render_geometry(
            filtering_phase
        ).coverage_camera_positions,
        policy=policy,
        enforced=config.coverage_gate_enabled,
    )
    report_path = str(Path(config.ortho_file).with_name("gaussian_coverage_report.json"))
    _write_json_report(report_path, report)
    _report(
        config.vol_id,
        "GAUSS",
        97,
        "Gaussian spatial coverage: "
        f"valid={report['valid_pixel_ratio']:.1%}, "
        f"covered cells={report['covered_cells_ratio']:.1%}, "
        f"worst interior cell={report['worst_cell_ratio']:.1%} "
        f"({report['status']}).",
        config.report_fn,
    )
    if config.coverage_gate_enabled and not report["accepted"]:
        failed_checks = ", ".join(
            check["name"] for check in report["checks"] if not check["passed"]
        )
        raise RuntimeError(
            "Gaussian spatial coverage gate rejected the product: "
            f"{failed_checks}. Report: {report_path}"
        )
    return report_path, report


def _write_seam_report(
    config: GaussianOrthoConfig,
    filtering_phase: GaussianFilteringPhaseState,
    rgb: Any,
    height: Any,
    extent: tuple[float, float, float, float],
) -> tuple[str | None, dict[str, Any] | None]:
    if not getattr(filtering_phase, "partition_models", ()):
        return None, None
    from .seam_quality import evaluate_core_seams

    render = _render_geometry(filtering_phase)
    report = evaluate_core_seams(
        rgb,
        height,
        extent=extent,
        gsd=render.local_gsd,
        geo_origin=(
            render.geo_origin
            if config.render_mode == "map"
            else np.zeros(3, dtype=np.float64)
        ),
        partitions=filtering_phase.partition_models,
        coordinate_scale=(
            1.0
            if config.render_mode == "map"
            else render.local_gsd / config.resolution
        ),
    )
    report_path = str(
        Path(config.ortho_file).with_name("gaussian_seam_report.json")
    )
    _write_json_report(report_path, report)
    _report(
        config.vol_id,
        "GAUSS",
        97,
        f"Recorded evidence for {report['seam_count']} resident core seams.",
        config.report_fn,
    )
    return report_path, report


def _write_facade_report(
    config: GaussianOrthoConfig,
    summary: GaussianSceneSummary,
    filtering_phase: GaussianFilteringPhaseState,
    *,
    width: int,
    height: int,
    geo_x_min: float,
    geo_y_max: float,
) -> Path:
    if summary.facade_frame is None:
        raise RuntimeError("Facade frame is unavailable for reporting")
    render = _render_geometry(filtering_phase)
    depth_bounds = render.facade_depth_bounds_model
    resident_depth_partitions = [
        partition
        for partition in filtering_phase.partition_models
        if partition.facade_depth_bounds_model is not None
    ]
    resident_depth_windows = [
        {
            "row": partition.bounds.row,
            "column": partition.bounds.col,
            "bounds_model_units": list(
                cast(
                    tuple[float, float],
                    partition.facade_depth_bounds_model,
                )
            ),
            "bounds_metres": [
                value * summary.colmap_to_meters
                for value in cast(
                    tuple[float, float],
                    partition.facade_depth_bounds_model,
                )
            ],
        }
        for partition in resident_depth_partitions
    ]
    if resident_depth_partitions:
        depth_bounds = (
            min(
                cast(tuple[float, float], partition.facade_depth_bounds_model)[0]
                for partition in resident_depth_partitions
            ),
            max(
                cast(tuple[float, float], partition.facade_depth_bounds_model)[1]
                for partition in resident_depth_partitions
            ),
        )
    subset = summary.facade_subset_result
    if subset is not None and bool(subset.get("partitioned")):
        raw_cells = subset.get("cells", [])
        cells = [
            cast(dict[str, Any], cell)
            for cell in (raw_cells if isinstance(raw_cells, list) else [])
            if isinstance(cell, dict)
        ]
        training_workspace_points = sum(
            int(cell.get("exported_points", 0))
            for cell in cells
        )
        coverage_balanced = any(
            bool(cell.get("coverage_balanced"))
            for cell in cells
        )
        resident_cell_count = len(cells)
    else:
        training_workspace_points = (
            int(cast(Any, subset["exported_points"]))
            if subset is not None
            else summary.gaussian_seed_point_count
        )
        coverage_balanced = bool(
            subset and subset.get("coverage_balanced")
        )
        resident_cell_count = 1
    report_path = Path(
        config.facade_frame_report
        or str(Path(config.ortho_file).with_name("facade_frame.json"))
    )
    payload = {
        "schema_version": 1,
        "coordinate_system": "LOCAL_FACADE",
        "units": "metres" if summary.scale_source != "model-units" else "model-units",
        "axis_definition": {
            "x": "horizontal-right",
            "y": "vertical-up",
            "z": "outward-toward-cameras",
        },
        "scale": {
            "meters_per_model_unit": summary.colmap_to_meters,
            "source": summary.scale_source,
            "uses_absolute_position": False,
            "uses_rtk_adjustment": False,
        },
        "frame": summary.facade_frame,
        "texture_selection": {
            "registered_cameras": summary.registered_camera_count,
            "training_cameras": summary.texture_camera_count,
            "maximum_incidence_deg": config.facade_texture_max_incidence_deg,
            "minimum_sparse_observations": summary.minimum_sparse_observations,
            "filter_applied": summary.texture_filter_applied,
        },
        "gaussian_seed": {
            "maximum_reprojection_error_px": summary.seed_max_error,
            "minimum_track_length": summary.seed_min_track,
            "points_after_loader_filter": summary.gaussian_seed_point_count,
            "training_workspace_points": training_workspace_points,
            "coverage_balanced_cap_applied": coverage_balanced,
            "resident_partitioned": resident_cell_count > 1,
            "resident_cell_count": resident_cell_count,
        },
        "depth_filter": {
            "iqr_multiplier": config.facade_depth_iqr_multiplier,
            "scope": (
                "resident-cells"
                if filtering_phase.partition_models
                else "global-model"
            ),
            "bounds_model_units": list(depth_bounds) if depth_bounds is not None else None,
            "bounds_metres": (
                [
                    depth_bounds[0] * summary.colmap_to_meters,
                    depth_bounds[1] * summary.colmap_to_meters,
                ]
                if depth_bounds is not None
                else None
            ),
            "resident_cells": resident_depth_windows,
        },
        "raster": {
            "width": width,
            "height": height,
            "pixel_size": config.resolution,
            "pixel_size_units": render.resolution_units,
            "extent": [
                geo_x_min,
                geo_y_max - height * config.resolution,
                geo_x_min + width * config.resolution,
                geo_y_max,
            ],
            "crs": None,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, report_path)
    return report_path


def finalize_gaussian_raster_product(
    config: GaussianOrthoConfig,
    filtering_phase: GaussianFilteringPhaseState,
    rasterization_phase: GaussianRasterizationPhaseState,
    summary: GaussianSceneSummary,
    *,
    final_ply: str | None,
    cupy_version: str,
) -> dict[str, Any]:
    """Apply quality gates and publish RGB/height products identically."""
    result = rasterization_phase.result
    rgb = result["rgb"]
    height_map = result["height"]
    x_min, x_max, y_min, y_max = result["extent"]
    width = rasterization_phase.width
    height = rasterization_phase.height
    coverage_path, coverage = _write_coverage_report(
        config,
        filtering_phase,
        height_map,
        (x_min, x_max, y_min, y_max),
    )
    seam_path, seam_report = _write_seam_report(
        config,
        filtering_phase,
        rgb,
        height_map,
        (x_min, x_max, y_min, y_max),
    )
    render = _render_geometry(filtering_phase)
    geo_x_min, geo_y_max = georeference_raster_origin(
        x_min,
        y_max,
        geo_origin=render.geo_origin,
        colmap_to_meters=summary.colmap_to_meters,
        sim3_aligned=summary.sim3_aligned,
        facade=config.render_mode == "facade",
    )
    if config.render_mode == "facade":
        height_map = height_map * summary.colmap_to_meters
        z_offset = 0.0
        vertical_reference = "local-facade-depth"
    else:
        height_map, z_offset, vertical_reference = georeference_height_map(
            height_map,
            sim3_aligned=summary.sim3_aligned,
            geo_origin_z=float(render.geo_origin[2]),
            colmap_to_meters=summary.colmap_to_meters,
            exif_altitude_available=summary.exif_altitude_available,
        )
    if vertical_reference == "sim3":
        message = f"Applied Sim3 vertical translation ({z_offset:+.2f} m) to height map."
    elif vertical_reference == "exif":
        message = f"Applied GPS/EXIF vertical origin ({z_offset:+.2f} m) to height map."
    else:
        message = "No absolute altitude reference found; height map remains in local model Z."
    _report(config.vol_id, "GAUSS", 97, message, config.report_fn)
    _report(config.vol_id, "GAUSS", 98, "Writing GeoTIFF…", config.report_fn)
    height_file = str(Path(config.ortho_file).with_suffix(".height.tif"))
    write_geotiff(
        output_path=config.ortho_file,
        rgb=rgb,
        x_min=geo_x_min,
        y_max=geo_y_max,
        gsd=config.resolution,
        crs=None if config.render_mode == "facade" else config.utm_crs,
        height_map=height_map,
        height_output_path=height_file,
    )
    report_path = (
        _write_facade_report(
            config,
            summary,
            filtering_phase,
            width=width,
            height=height,
            geo_x_min=geo_x_min,
            geo_y_max=geo_y_max,
        )
        if config.render_mode == "facade"
        else None
    )
    _report(
        config.vol_id,
        "GAUSS",
        100,
        f"Done. Orthomosaic: {config.ortho_file}, Height: {height_file}",
        config.report_fn,
    )
    raster_extent = [
        geo_x_min,
        geo_y_max - height * config.resolution,
        geo_x_min + width * config.resolution,
        geo_y_max,
    ]
    return {
        "ortho_file": config.ortho_file,
        "height_file": height_file,
        "checkpoint_dir": config.checkpoint_dir,
        "final_ply": final_ply,
        "gaussian_partition_models": [
            {
                "path": partition.model_path,
                "row": partition.bounds.row,
                "column": partition.bounds.col,
                "gaussian_count": partition.gaussian_count,
                "core_gaussian_count": partition.core_gaussian_count,
            }
            for partition in filtering_phase.partition_models
        ],
        "width": width,
        "height": height,
        "gsd": config.resolution,
        "gsd_units": render.resolution_units,
        "raster_extent": raster_extent,
        "projected_extent": None if config.render_mode == "facade" else raster_extent,
        "vertical_reference": vertical_reference,
        "vertical_offset_m": z_offset,
        "render_mode": config.render_mode,
        "coordinate_system": (
            "LOCAL_FACADE" if config.render_mode == "facade" else config.utm_crs
        ),
        "facade_frame_report": str(report_path) if report_path else None,
        "gaussian_coverage_report": coverage_path,
        "gaussian_coverage": coverage,
        "gaussian_seam_report": seam_path,
        "gaussian_seams": seam_report,
        "scale_source": summary.scale_source,
        "meters_per_model_unit": summary.colmap_to_meters,
        "registered_cameras": summary.registered_camera_count,
        "texture_cameras": summary.texture_camera_count,
        "renderer_contract": "cupy-ortho-v3-surface-color",
        "cupy_version": cupy_version,
        "n_gaussians": filtering_phase.output_gaussians,
        "gaussian_density": (
            filtering_phase.density_assessment.as_dict()
            if filtering_phase.density_assessment is not None
            else None
        ),
        "ortho_mip_filter_variance": config.ortho_mip_filter_variance,
        "ortho_mip_filter_compensation": config.ortho_mip_filter_compensation,
    }
