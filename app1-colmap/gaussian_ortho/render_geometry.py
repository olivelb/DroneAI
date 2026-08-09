"""Shared geometry contract for Gaussian filtering and raster rendering."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class GaussianRenderGeometry:
    """Immutable geometry handed from filtering to rasterization."""

    geo_origin: np.ndarray
    frame_origin: np.ndarray | None
    rotation_geo: np.ndarray | None
    sh_direction_rotation: np.ndarray | None
    facade_depth_bounds_model: tuple[float, float] | None
    render_extent: tuple[float, float, float, float, float, float]
    local_gsd: float
    resolution_units: str
    coverage_camera_positions: np.ndarray
