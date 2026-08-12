"""Shared qualification and GeoTIFF publication for Gaussian raster paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        camera_positions=(
            filtering_phase.render_state.coverage_camera_positions
        ),
        policy=policy,
        enforced=config.coverage_gate_enabled,
    )
    report_path = str(Path(config.ortho_file).with_name("gaussian_coverage_report.json"))
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    _report(
        config.vol_id,
        "GAUSS",
        97,
        "Gaussian spatial coverage: "
        f"valid={report['valid_pixel_ratio']:.1%}, "
        f"covered cells={report['covered_cells_ratio']:.1%}, "
        f"worst cell={report['worst_cell_ratio']:.1%} "
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
    depth_bounds = filtering_phase.render_state.facade_depth_bounds_model
    subset = summary.facade_subset_result
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
            "training_workspace_points": (
                subset["exported_points"]
                if subset is not None
                else summary.gaussian_seed_point_count
            ),
            "coverage_balanced_cap_applied": bool(
                subset and subset["coverage_balanced"]
            ),
        },
        "depth_filter": {
            "iqr_multiplier": config.facade_depth_iqr_multiplier,
            "bounds_model_units": list(depth_bounds) if depth_bounds is not None else None,
            "bounds_metres": (
                [
                    depth_bounds[0] * summary.colmap_to_meters,
                    depth_bounds[1] * summary.colmap_to_meters,
                ]
                if depth_bounds is not None
                else None
            ),
        },
        "raster": {
            "width": width,
            "height": height,
            "pixel_size": config.resolution,
            "pixel_size_units": filtering_phase.render_state.resolution_units,
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
    final_ply: str,
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
    render = filtering_phase.render_state
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
        "scale_source": summary.scale_source,
        "meters_per_model_unit": summary.colmap_to_meters,
        "registered_cameras": summary.registered_camera_count,
        "texture_cameras": summary.texture_camera_count,
        "renderer_contract": "cupy-ortho-v3-surface-color",
        "cupy_version": cupy_version,
        "n_gaussians": filtering_phase.output_gaussians,
        "ortho_mip_filter_variance": config.ortho_mip_filter_variance,
        "ortho_mip_filter_compensation": config.ortho_mip_filter_compensation,
    }
