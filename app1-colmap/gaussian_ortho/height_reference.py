"""Vertical referencing helpers for Gaussian height rasters."""

from __future__ import annotations

import math

import numpy as np


def georeference_raster_origin(
    x_min: float,
    y_max: float,
    *,
    geo_origin: np.ndarray,
    colmap_to_meters: float,
    sim3_aligned: bool,
    facade: bool = False,
) -> tuple[float, float]:
    """Translate a rendered local extent into its output raster origin."""
    scale = float(colmap_to_meters)
    origin = np.asarray(geo_origin, dtype=np.float64)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("colmap_to_meters must be a positive finite value")
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError("geo_origin must contain three finite coordinates")

    local_x = np.float64(x_min)
    local_y = np.float64(y_max)
    if facade:
        return float(local_x * scale), float(local_y * scale)
    if sim3_aligned:
        return float(local_x + origin[0]), float(local_y + origin[1])
    return (
        float(local_x * scale + origin[0]),
        float(local_y * scale + origin[1]),
    )


def depth_buffer_to_height(depth: np.ndarray, camera_z: float) -> np.ndarray:
    """Convert normalized positive camera depth to world Z.

    A zero depth denotes a pixel with no Gaussian coverage and becomes NaN so
    it cannot be mistaken for the ortho-camera elevation in the DSM.
    """

    z_camera = float(camera_z)
    if not math.isfinite(z_camera):
        raise ValueError("camera_z must be finite")
    depth_array = np.asarray(depth)
    valid = np.isfinite(depth_array) & (depth_array > 0.0)
    height = np.full(depth_array.shape, np.nan, dtype=np.float32)
    height[valid] = z_camera - depth_array[valid]
    return height


def georeference_height_map(
    height: np.ndarray,
    *,
    sim3_aligned: bool,
    geo_origin_z: float,
    colmap_to_meters: float,
    exif_altitude_available: bool,
) -> tuple[np.ndarray, float, str]:
    """Convert rendered model Z values to the available vertical reference.

    A Sim3-aligned model is already metric after its scale and rotation have
    been applied; its withheld float64 translation must still be added to Z,
    just as it is added to the GeoTIFF X/Y origin.  On the PCA path, model Z
    is first converted to metres and the GPS/EXIF-derived origin is applied
    only when EXIF altitude was actually available.

    Returns ``(height, applied_offset_m, reference_source)``.  A source of
    ``"local"`` explicitly means that no absolute vertical datum is known.
    """

    scale = float(colmap_to_meters)
    origin_z = float(geo_origin_z)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("colmap_to_meters must be a positive finite value")
    if not math.isfinite(origin_z):
        raise ValueError("geo_origin_z must be finite")

    referenced = np.asarray(height)
    if not sim3_aligned and scale != 1.0:
        referenced = referenced * scale

    if sim3_aligned:
        offset = origin_z
        source = "sim3"
    elif exif_altitude_available:
        offset = origin_z
        source = "exif"
    else:
        offset = 0.0
        source = "local"

    if offset != 0.0:
        referenced = referenced + offset
    return referenced, offset, source
