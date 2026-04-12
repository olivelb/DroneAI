import os
import numpy as np
from PIL import Image as PILImage
from PIL.ExifTags import TAGS, GPSTAGS


def _get_gps_info(filepath):
    """Extract GPS IFD from a JPEG/TIFF using Pillow. Returns dict or None."""
    try:
        img = PILImage.open(filepath)
        exif = img._getexif()
        if exif is None:
            return None
        for tag_id, value in exif.items():
            if TAGS.get(tag_id) == "GPSInfo":
                gps = {}
                for gk, gv in value.items():
                    gps[GPSTAGS.get(gk, gk)] = gv
                return gps
    except Exception:
        pass
    return None


def _dms_to_deg(dms):
    """Convert (degrees, minutes, seconds) tuple to decimal degrees."""
    return float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0


def extract_exif_altitudes(images_dir):
    """
    Extract GPS altitude from all images in a directory.
    Returns a dict: {filename: altitude (float or None)}
    """
    altitudes = {}
    for fname in os.listdir(images_dir):
        if not fname.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        gps = _get_gps_info(os.path.join(images_dir, fname))
        if gps and "GPSAltitude" in gps:
            alt = float(gps["GPSAltitude"])
            ref = gps.get("GPSAltitudeRef", b"\x00")
            if isinstance(ref, bytes):
                ref = ref[0] if ref else 0
            if ref == 1:
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
    for fname in os.listdir(images_dir):
        if not fname.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        gps = _get_gps_info(os.path.join(images_dir, fname))
        if gps and "GPSLatitude" in gps and "GPSLongitude" in gps:
            lat = _dms_to_deg(gps["GPSLatitude"])
            if gps.get("GPSLatitudeRef") == "S":
                lat = -lat
            lon = _dms_to_deg(gps["GPSLongitude"])
            if gps.get("GPSLongitudeRef") == "W":
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
