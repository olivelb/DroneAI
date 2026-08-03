import os
import numpy as np
from PIL import Image as PILImage, UnidentifiedImageError
from PIL.ExifTags import TAGS, GPSTAGS


def _get_gps_info(filepath):
    """Extract GPS IFD from a JPEG/TIFF using Pillow. Returns dict or None."""
    try:
        with PILImage.open(filepath) as image:
            exif = image.getexif()
            for tag_id in exif:
                if TAGS.get(tag_id) != "GPSInfo":
                    continue
                value = exif.get_ifd(tag_id)
                return {
                    GPSTAGS.get(gps_tag, gps_tag): gps_value
                    for gps_tag, gps_value in value.items()
                }
    except (AttributeError, OSError, TypeError, UnidentifiedImageError, ValueError):
        return None
    return None


def _dms_to_deg(dms):
    """Convert (degrees, minutes, seconds) tuple to decimal degrees."""
    return float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0


def _gps_reference(value):
    if isinstance(value, bytes):
        return value.decode("ascii", errors="ignore").strip("\x00").upper()
    return str(value or "").upper()


def extract_exif_altitudes(images_dir):
    """
    Extract GPS altitude from all images in a directory.
    Returns a dict: {filename: altitude (float or None)}
    """
    altitudes = {}
    for fname in sorted(os.listdir(images_dir)):
        if not fname.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        gps = _get_gps_info(os.path.join(images_dir, fname))
        if gps and "GPSAltitude" in gps:
            alt = float(gps["GPSAltitude"])
            ref = gps.get("GPSAltitudeRef", 0)
            if ref == 1 or _gps_reference(ref) in {"1", "\x01"}:
                alt = -alt
            altitudes[fname] = alt
        else:
            altitudes[fname] = None
    return altitudes


def extract_exif_gps(images_dir):
    """
    Extract GPS lat/lon from all images in a directory.
    Returns a dict: {filename: (lat_deg, lon_deg) or None}
    """
    positions = {}
    for fname in sorted(os.listdir(images_dir)):
        if not fname.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        gps = _get_gps_info(os.path.join(images_dir, fname))
        if gps and "GPSLatitude" in gps and "GPSLongitude" in gps:
            lat = _dms_to_deg(gps["GPSLatitude"])
            if _gps_reference(gps.get("GPSLatitudeRef")) == "S":
                lat = -lat
            lon = _dms_to_deg(gps["GPSLongitude"])
            if _gps_reference(gps.get("GPSLongitudeRef")) == "W":
                lon = -lon
            positions[fname] = (lat, lon)
        else:
            positions[fname] = None
    return positions


def compute_colmap_scale(cameras, images_dir, utm_crs):
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
    matched = []
    for cam in cameras:
        g = gps.get(cam.image_name)
        if g is not None:
            e, n = transformer.transform(g[1], g[0])  # lon, lat → easting, northing
            matched.append((cam.T.astype(np.float64), np.array([e, n])))

    if len(matched) < 10:
        return 1.0  # not enough data

    # Sample random pairs and compute distance ratios
    rng = np.random.default_rng(42)
    n_pairs = min(1000, len(matched) * (len(matched) - 1) // 2)
    indices = rng.choice(len(matched), size=(n_pairs, 2), replace=True)
    ratios = []
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
    cameras,
    images_dir,
    projected_crs,
    aligned_camera_positions,
    colmap_to_meters,
    mean_altitude,
):
    """Align a rotated local camera centroid with its projected GPS centroid."""

    from pyproj import Transformer

    gps = extract_exif_gps(images_dir)
    transformer = Transformer.from_crs(
        "EPSG:4326",
        projected_crs,
        always_xy=True,
    )
    projected = []
    for camera in cameras:
        position = gps.get(camera.image_name)
        if position is None:
            continue
        easting, northing = transformer.transform(position[1], position[0])
        projected.append([easting, northing, mean_altitude or 0.0])
    if not projected:
        return None
    gps_centroid = np.mean(projected, axis=0).astype(np.float64)
    model_centroid = (
        np.asarray(aligned_camera_positions, dtype=np.float64).mean(axis=0)
        * float(colmap_to_meters)
    )
    return gps_centroid - model_centroid


def compute_colmap_scale_geodesic(cameras, images_dir):
    """Estimate metres per COLMAP unit without selecting an output CRS.

    GPS is used only for relative 3D camera-baseline lengths (including EXIF
    altitude when available). Absolute position, RTK covariance and map
    projection never enter the facade orientation or origin.
    """

    gps = extract_exif_gps(images_dir)
    altitudes = extract_exif_altitudes(images_dir)
    matched = [
        (
            cam.T.astype(np.float64),
            gps.get(cam.image_name),
            altitudes.get(cam.image_name),
        )
        for cam in cameras
        if gps.get(cam.image_name) is not None
    ]
    if len(matched) < 10:
        return 1.0, "model-units"

    radius_m = 6_371_008.8
    rng = np.random.default_rng(42)
    n_pairs = min(2000, len(matched) * (len(matched) - 1) // 2)
    indices = rng.choice(len(matched), size=(n_pairs, 2), replace=True)
    ratios = []
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
