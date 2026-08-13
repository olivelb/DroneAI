from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from PIL import Image as PILImage, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS


type ImageDirectory = str | os.PathLike[str]
type GpsInfo = dict[str | int, Any]


class _CameraPose(Protocol):
    image_name: str
    T: np.ndarray


def _sample_pair_indices(item_count: int, max_pairs: int) -> np.ndarray:
    """Return a deterministic bounded sample of index pairs."""

    pair_count = min(max_pairs, item_count * (item_count - 1) // 2)
    return cast(
        np.ndarray,
        np.random.default_rng(42).choice(
            item_count,
            size=(pair_count, 2),
            replace=True,
        ),
    )


def _get_gps_info(filepath: ImageDirectory) -> GpsInfo | None:
    """Extract GPS IFD from a JPEG/TIFF using Pillow. Returns dict or None."""
    try:
        with PILImage.open(filepath) as image:
            exif = image.getexif()
            for tag_id in exif:
                if TAGS.get(tag_id) != "GPSInfo":
                    continue
                value = exif.get_ifd(tag_id)
                gps_info: GpsInfo = {
                    GPSTAGS.get(gps_tag, gps_tag): gps_value
                    for gps_tag, gps_value in value.items()
                }
                return gps_info
    except (AttributeError, OSError, TypeError, UnidentifiedImageError, ValueError):
        return None
    return None


def _dms_to_deg(dms: Sequence[Any]) -> float:
    """Convert (degrees, minutes, seconds) tuple to decimal degrees."""
    return float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0


def _gps_reference(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="ignore").strip("\x00").upper()
    return str(value or "").upper()


def _image_paths(images_dir: ImageDirectory) -> list[tuple[str, Path]]:
    """Return recursive image paths keyed like COLMAP image names."""

    root = Path(images_dir)
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in {".jpg", ".jpeg", ".tif", ".tiff"}
    ]


def extract_exif_altitudes(
    images_dir: ImageDirectory,
) -> dict[str, float | None]:
    """
    Extract GPS altitude from all images in a directory.
    Returns a dict: {filename: altitude (float or None)}
    """
    altitudes: dict[str, float | None] = {}
    for image_name, path in _image_paths(images_dir):
        gps = _get_gps_info(str(path))
        if gps and "GPSAltitude" in gps:
            alt = float(gps["GPSAltitude"])
            ref = gps.get("GPSAltitudeRef", 0)
            if ref == 1 or _gps_reference(ref) in {"1", "\x01"}:
                alt = -alt
            altitudes[image_name] = alt
        else:
            altitudes[image_name] = None
    return altitudes


def extract_exif_gps(
    images_dir: ImageDirectory,
) -> dict[str, tuple[float, float] | None]:
    """
    Extract GPS lat/lon from all images in a directory.
    Returns a dict: {filename: (lat_deg, lon_deg) or None}
    """
    positions: dict[str, tuple[float, float] | None] = {}
    for image_name, path in _image_paths(images_dir):
        gps = _get_gps_info(str(path))
        if gps and "GPSLatitude" in gps and "GPSLongitude" in gps:
            lat = _dms_to_deg(gps["GPSLatitude"])
            if _gps_reference(gps.get("GPSLatitudeRef")) == "S":
                lat = -lat
            lon = _dms_to_deg(gps["GPSLongitude"])
            if _gps_reference(gps.get("GPSLongitudeRef")) == "W":
                lon = -lon
            positions[image_name] = (lat, lon)
        else:
            positions[image_name] = None
    return positions


def compute_colmap_scale(
    cameras: Sequence[_CameraPose],
    images_dir: ImageDirectory,
    utm_crs: object,
) -> float:
    """
    Compute the scale factor: how many real-world metres per COLMAP unit.

    Compares pairwise distances between cameras in COLMAP local coords
    vs their GPS positions converted to UTM.  Returns the median ratio
    (metres / COLMAP-unit), or 1.0 if GPS data is insufficient.
    """
    from pyproj import Transformer

    gps = extract_exif_gps(images_dir)
    transformer = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)

    # Match cameras to GPS by image_name
    matched: list[tuple[np.ndarray, np.ndarray]] = []
    for cam in cameras:
        g = gps.get(cam.image_name)
        if g is not None:
            e, n = transformer.transform(g[1], g[0])  # lon, lat → easting, northing
            matched.append((cam.T.astype(np.float64), np.array([e, n])))

    if len(matched) < 10:
        return 1.0  # not enough data

    # Sample random pairs and compute distance ratios
    indices = _sample_pair_indices(len(matched), 1000)
    ratios: list[float] = []
    for i, j in indices:
        if i == j:
            continue
        colmap_d = float(np.linalg.norm(matched[i][0] - matched[j][0]))
        gps_d = float(np.linalg.norm(matched[i][1] - matched[j][1]))  # 2D horizontal
        if colmap_d > 0.01:
            ratios.append(gps_d / colmap_d)

    if not ratios:
        return 1.0

    scale = float(np.median(ratios))
    return scale


def compute_projected_geo_origin(
    cameras: Sequence[_CameraPose],
    images_dir: ImageDirectory,
    projected_crs: object,
    aligned_camera_positions: object,
    colmap_to_meters: float,
    mean_altitude: float | None,
) -> np.ndarray | None:
    """Align a rotated local camera centroid with its projected GPS centroid."""

    from pyproj import Transformer

    gps = extract_exif_gps(images_dir)
    transformer = Transformer.from_crs(
        "EPSG:4326",
        projected_crs,
        always_xy=True,
    )
    projected: list[list[float]] = []
    for camera in cameras:
        position = gps.get(camera.image_name)
        if position is None:
            continue
        easting, northing = transformer.transform(position[1], position[0])
        projected.append([easting, northing, mean_altitude or 0.0])
    if not projected:
        return None
    gps_centroid: np.ndarray = np.mean(projected, axis=0).astype(np.float64)
    model_centroid: np.ndarray = (
        np.asarray(aligned_camera_positions, dtype=np.float64).mean(axis=0)
        * float(colmap_to_meters)
    )
    origin: np.ndarray = gps_centroid - model_centroid
    return origin


def compute_colmap_scale_geodesic(
    cameras: Sequence[_CameraPose],
    images_dir: ImageDirectory,
) -> tuple[float, str]:
    """Estimate metres per COLMAP unit without selecting an output CRS.

    GPS is used only for relative 3D camera-baseline lengths (including EXIF
    altitude when available). Absolute position, RTK covariance and map
    projection never enter the facade orientation or origin.
    """

    gps = extract_exif_gps(images_dir)
    altitudes = extract_exif_altitudes(images_dir)
    matched: list[tuple[np.ndarray, tuple[float, float], float | None]] = []
    for camera in cameras:
        position = gps.get(camera.image_name)
        if position is None:
            continue
        matched.append(
            (
                camera.T.astype(np.float64),
                position,
                altitudes.get(camera.image_name),
            )
        )
    if len(matched) < 10:
        return 1.0, "model-units"

    radius_m = 6_371_008.8
    indices = _sample_pair_indices(len(matched), 2000)
    ratios: list[float] = []
    for i, j in indices:
        if i == j:
            continue
        model_distance = float(np.linalg.norm(matched[i][0] - matched[j][0]))
        lat_a, lon_a = matched[i][1]
        lat_b, lon_b = matched[j][1]
        phi_a, phi_b = np.radians([lat_a, lat_b])
        delta_phi = phi_b - phi_a
        delta_lambda = np.radians(lon_b - lon_a)
        haversine = (
            np.sin(delta_phi / 2.0) ** 2
            + np.cos(phi_a) * np.cos(phi_b) * np.sin(delta_lambda / 2.0) ** 2
        )
        horizontal_distance = 2.0 * radius_m * np.arcsin(
            min(1.0, np.sqrt(haversine))
        )
        altitude_a = matched[i][2]
        altitude_b = matched[j][2]
        altitude_delta = (
            float(altitude_b) - float(altitude_a)
            if altitude_a is not None and altitude_b is not None
            else 0.0
        )
        metric_distance = float(np.hypot(horizontal_distance, altitude_delta))
        if model_distance > 0.01 and metric_distance > 0.05:
            ratios.append(float(metric_distance / model_distance))
    if len(ratios) < 10:
        return 1.0, "model-units"
    return float(np.median(ratios)), "relative-gps-baselines"
