import json
import hashlib
import logging
import os
import shutil
import sqlite3
import statistics
import subprocess
from pathlib import Path

from PIL import Image as PILImage
from PIL.ExifTags import GPSTAGS

from shared.pipeline_params import (
    merge_mission_pipeline_params,
    normalize_ai_backend,
    normalize_feature_type,
    normalize_matcher_type,
)
from shared.dji_metadata import load_dji_mrk_overrides
from shared.projected_crs import select_projected_crs


logger = logging.getLogger("app1-colmap.support")

COPY_MANIFEST_FILENAME = ".copy_manifest.json"


def is_aliked_feature_type(feature_type):
    return normalize_feature_type(feature_type).startswith("ALIKED")


def resolve_feature_family(feature_type):
    return "ALIKED" if is_aliked_feature_type(feature_type) else "SIFT"


def resolve_feature_matching_type(feature_type, matcher_type):
    normalized_matcher = normalize_matcher_type(matcher_type)
    if is_aliked_feature_type(feature_type):
        return "ALIKED_LIGHTGLUE" if normalized_matcher == "LIGHTGLUE" else "ALIKED_BRUTEFORCE"
    return "SIFT_LIGHTGLUE" if normalized_matcher == "LIGHTGLUE" else "SIFT_BRUTEFORCE"


def merge_pipeline_params(pipeline_mode, mission_params):
    return merge_mission_pipeline_params(pipeline_mode, mission_params)


def copy_manifest_path(image_dir):
    return os.path.join(image_dir, COPY_MANIFEST_FILENAME)


def load_copy_manifest(image_dir):
    manifest_path = copy_manifest_path(image_dir)
    if not os.path.exists(manifest_path):
        return {}

    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(manifest, dict):
        return manifest
    return {}


def save_copy_manifest(image_dir, manifest):
    manifest_path = copy_manifest_path(image_dir)
    temp_path = f"{manifest_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, manifest_path)


def compute_file_sha256(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def describe_source_file(path, sha256=None):
    stat_result = os.stat(path)
    return {
        "size": stat_result.st_size,
        "mtime_ns": getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)),
        "sha256": sha256,
    }


def plan_clean_image_copy(src_path, dst_path, manifest_entry):
    if not os.path.exists(dst_path):
        return True, None

    source_descriptor = describe_source_file(src_path)
    if (
        manifest_entry
        and manifest_entry.get("size") == source_descriptor["size"]
        and manifest_entry.get("mtime_ns") == source_descriptor["mtime_ns"]
    ):
        return False, source_descriptor

    source_sha256 = compute_file_sha256(src_path)
    source_descriptor["sha256"] = source_sha256

    if manifest_entry and manifest_entry.get("sha256") == source_sha256:
        return False, source_descriptor

    if manifest_entry is None and os.path.getsize(dst_path) == source_descriptor["size"]:
        return False, source_descriptor

    return True, source_descriptor


def extract_gps_data(
    image_dir,
    output_file,
    vol_id,
    report_fn,
    *,
    projected_crs_mode="auto-local",
    projected_crs=None,
):
    import pyproj

    report_fn(
        vol_id,
        "GPS_EXTRACTION",
        10,
        log="Extracting DJI MRK/EXIF positions and selecting a metric projected CRS...",
    )
    images = sorted(
        f
        for f in os.listdir(image_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )
    image_paths = [Path(image_dir) / image_name for image_name in images]
    mrk_overrides = load_dji_mrk_overrides(image_dir, image_paths)
    positions = []
    mrk_count = 0
    mrk_vertical_errors = []
    for img_name in images:
        img_path = os.path.join(image_dir, img_name)
        try:
            mrk_gps = mrk_overrides.get(img_name)
            if mrk_gps:
                lat = float(mrk_gps["latitude"])
                lon = float(mrk_gps["longitude"])
                alt = float(mrk_gps.get("altitude_m") or 0.0)
                mrk_count += 1
                position_std = mrk_gps.get("position_std_m") or {}
                if position_std.get("vertical_m") is not None:
                    mrk_vertical_errors.append(
                        float(position_std["vertical_m"])
                    )
            else:
                with PILImage.open(img_path) as pil_img:
                    exif_data = pil_img._getexif()
                    if not exif_data:
                        continue
                    gps_ifd = exif_data.get(0x8825)
                    if not gps_ifd:
                        continue
                    gps_info = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                    gps_lat = gps_info.get("GPSLatitude")
                    gps_lon = gps_info.get("GPSLongitude")
                    if not gps_lat or not gps_lon:
                        continue
                    lat = float(gps_lat[0]) + float(gps_lat[1]) / 60 + float(gps_lat[2]) / 3600
                    if gps_info.get("GPSLatitudeRef", "N") == "S":
                        lat = -lat
                    lon = float(gps_lon[0]) + float(gps_lon[1]) / 60 + float(gps_lon[2]) / 3600
                    if gps_info.get("GPSLongitudeRef", "E") == "W":
                        lon = -lon
                    gps_alt = gps_info.get("GPSAltitude", 0)
                    alt = float(gps_alt) if gps_alt else 0
            positions.append((img_name, lat, lon, alt))
        except Exception as error:
            logger.debug("Skipping GPS extraction for %s: %s", img_name, error)

    choice = None
    if positions:
        choice = select_projected_crs(
            ((latitude, longitude) for _, latitude, longitude, _ in positions),
            policy=projected_crs_mode,
            custom_crs=projected_crs,
        )
        transformer = pyproj.Transformer.from_crs(
            "EPSG:4326",
            choice.crs,
            always_xy=True,
        )

    count = 0
    with open(output_file, "w", encoding="utf-8") as handle:
        for img_name, lat, lon, alt in positions:
            try:
                x, y = transformer.transform(lon, lat)
                handle.write(f"{img_name} {x} {y} {alt}\n")
                count += 1
            except Exception as error:
                logger.debug("Skipping GPS extraction for %s: %s", img_name, error)
    report_fn(
        vol_id,
        "GPS_EXTRACTION",
        12,
        log=(
            f"Extracted positions from {count}/{len(images)} images "
            f"({mrk_count} DJI MRK, {count - mrk_count} EXIF). "
            f"Using CRS {choice.crs if choice else None} "
            f"({choice.source if choice else 'unavailable'})"
        ),
    )
    vertical_reference = (
        "ellipsoidal"
        if count > 0 and mrk_count == count
        else ("mixed-or-unknown" if mrk_count else "unknown")
    )
    save_projected_crs(
        output_file,
        choice.crs if choice else None,
        policy=projected_crs_mode,
        requested_crs=projected_crs,
        vertical_reference=vertical_reference,
        vertical_source=(
            "dji_mrk_ellh"
            if vertical_reference == "ellipsoidal"
            else "exif_gps_altitude_or_mixed"
        ),
        vertical_uncertainty_m=(
            {
                "minimum": min(mrk_vertical_errors),
                "maximum": max(mrk_vertical_errors),
                "mean": sum(mrk_vertical_errors)
                / len(mrk_vertical_errors),
            }
            if mrk_vertical_errors
            else None
        ),
    )
    return choice.crs if choice else None


def read_saved_projected_crs(geo_data_file):
    crs_file = f"{geo_data_file}.crs"
    if not os.path.exists(crs_file):
        return None
    try:
        with open(crs_file, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        return value or None
    except OSError:
        return None


def read_saved_projected_crs_policy(geo_data_file):
    metadata_file = f"{geo_data_file}.crs.json"
    try:
        with open(metadata_file, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_projected_crs(
    geo_data_file,
    projected_crs,
    *,
    policy=None,
    requested_crs=None,
    vertical_reference=None,
    vertical_source=None,
    vertical_uncertainty_m=None,
):
    if not projected_crs:
        return
    crs_file = f"{geo_data_file}.crs"
    try:
        with open(crs_file, "w", encoding="utf-8") as handle:
            handle.write(f"{projected_crs}\n")
        if policy:
            metadata_file = f"{geo_data_file}.crs.json"
            temporary_file = f"{metadata_file}.tmp"
            metadata = {}
            try:
                with open(metadata_file, encoding="utf-8") as handle:
                    existing = json.load(handle)
                if isinstance(existing, dict):
                    metadata.update(existing)
            except (OSError, json.JSONDecodeError):
                pass
            metadata.update(
                {
                    "schema_version": 2,
                    "projected_crs": projected_crs,
                    "policy": str(policy),
                    "requested_crs": str(requested_crs or ""),
                }
            )
            if vertical_reference is not None:
                metadata["vertical"] = {
                    "reference": str(vertical_reference),
                    "source": str(vertical_source or "unspecified"),
                    "uncertainty_m": vertical_uncertainty_m,
                    "orthometric_conversion_applied": False,
                }
            with open(temporary_file, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary_file, metadata_file)
    except OSError as error:
        logger.warning("Failed to save projected CRS for %s: %s", geo_data_file, error)


def read_saved_utm_crs(geo_data_file):
    """Backward-compatible alias for older worker imports."""

    return read_saved_projected_crs(geo_data_file)


def save_utm_crs(geo_data_file, utm_crs):
    """Backward-compatible alias for older worker imports."""

    save_projected_crs(geo_data_file, utm_crs)


def sanitize_exif_for_colmap(image_dir, vol_id, report_fn):
    marker_path = os.path.join(image_dir, ".colmap_exif_sanitized")
    if os.path.exists(marker_path):
        return

    exiftool_path = shutil.which("exiftool")
    if not exiftool_path:
        report_fn(
            vol_id,
            "PREPARING",
            13,
            log="ExifTool not found in worker image; skipping EXIF sanitization for COLMAP compatibility.",
        )
        return

    command = [
        exiftool_path,
        "-overwrite_original",
        "-q",
        "-m",
        "-r",
        "-ext",
        "jpg",
        "-ext",
        "jpeg",
        "-EXIF:ComponentsConfiguration=",
        "-EXIF:FileSource=",
        "-EXIF:SceneType=",
        image_dir,
    ]

    report_fn(
        vol_id,
        "PREPARING",
        13,
        log="Sanitizing copied JPEG EXIF tags that trigger OpenImageIO/COLMAP parser warnings...",
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        report_fn(
            vol_id,
            "PREPARING",
            13,
            log=f"EXIF sanitization skipped after ExifTool error: {detail}",
        )
        return

    try:
        with open(marker_path, "w", encoding="utf-8") as handle:
            handle.write("sanitized\n")
    except OSError as error:
        logger.warning("Failed to write EXIF sanitization marker %s: %s", marker_path, error)


def detect_existing_pipeline(db_path):
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT rows, cols, length(data) FROM descriptors LIMIT 1")
            row = cursor.fetchone()

            if row is not None:
                rows, cols, blob_size = row
                if rows > 0:
                    bytes_per_feature = blob_size / rows
                    if bytes_per_feature == 128:
                        return "SIFT"
                    if bytes_per_feature == 512:
                        return "ALIKED"
        finally:
            conn.close()
    except Exception as error:
        logger.warning("Failed to inspect existing database %s: %s", db_path, error)
    return None


def inspect_sparse_reconstruction(model_path):
    if not os.path.isdir(model_path):
        return 0, 0
    try:
        import pycolmap

        reconstruction = pycolmap.Reconstruction(model_path)
        registered_images = len(reconstruction.reg_image_ids())
        points3d = len(reconstruction.points3D)
        return registered_images, points3d
    except Exception as error:
        logger.warning("Failed to inspect sparse reconstruction %s: %s", model_path, error)
        return 0, 0


def inspect_sparse_quality(model_path):
    """Return registration, reprojection and track-health metrics."""

    empty = {
        "registered_images": 0,
        "points3D": 0,
        "mean_reprojection_error_px": None,
        "median_reprojection_error_px": None,
        "median_track_length": None,
    }
    if not os.path.isdir(model_path):
        return empty
    try:
        import math
        import pycolmap

        reconstruction = pycolmap.Reconstruction(model_path)
        errors = []
        track_lengths = []
        for point in reconstruction.points3D.values():
            try:
                error = float(point.error)
                if math.isfinite(error):
                    errors.append(error)
            except (TypeError, ValueError):
                pass
            try:
                track_lengths.append(int(point.track.length()))
            except (AttributeError, TypeError):
                try:
                    track_lengths.append(len(point.track.elements))
                except (AttributeError, TypeError):
                    pass
        return {
            "registered_images": len(reconstruction.reg_image_ids()),
            "points3D": len(reconstruction.points3D),
            "mean_reprojection_error_px": (
                statistics.fmean(errors) if errors else None
            ),
            "median_reprojection_error_px": (
                statistics.median(errors) if errors else None
            ),
            "median_track_length": (
                statistics.median(track_lengths)
                if track_lengths
                else None
            ),
        }
    except Exception as error:
        logger.warning(
            "Failed to inspect sparse quality %s: %s",
            model_path,
            error,
        )
        return empty
