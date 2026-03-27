import json
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess

from exif import Image as ExifImage

from shared.pipeline_params import (
    merge_mission_pipeline_params,
    normalize_feature_type,
    normalize_matcher_type,
)


logger = logging.getLogger("app1-colmap.support")

COPY_MANIFEST_FILENAME = ".copy_manifest.json"


def normalize_ai_backend(value):
    normalized = str(value or "yolo").strip().lower().replace("_", "-").replace(" ", "-")
    if normalized in {"sam", "sam3", "sam-3", "meta-sam3", "meta-sam-3", "segment-anything-3"}:
        return "sam3"
    return "yolo"


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


def extract_gps_data(image_dir, output_file, vol_id, report_fn):
    import pyproj

    report_fn(vol_id, "GPS_EXTRACTION", 10, log="Extracting EXIF GPS data and converting to UTM...")
    images = [f for f in os.listdir(image_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    count = 0
    utm_crs = None
    transformer = None
    with open(output_file, "w", encoding="utf-8") as handle:
        for img_name in images:
            img_path = os.path.join(image_dir, img_name)
            with open(img_path, "rb") as src:
                try:
                    exif_img = ExifImage(src)
                    if hasattr(exif_img, "gps_latitude") and hasattr(exif_img, "gps_longitude"):
                        lat = exif_img.gps_latitude[0] + exif_img.gps_latitude[1] / 60 + exif_img.gps_latitude[2] / 3600
                        if getattr(exif_img, "gps_latitude_ref", "N") == "S":
                            lat = -lat
                        lon = exif_img.gps_longitude[0] + exif_img.gps_longitude[1] / 60 + exif_img.gps_longitude[2] / 3600
                        if getattr(exif_img, "gps_longitude_ref", "E") == "W":
                            lon = -lon
                        alt = getattr(exif_img, "gps_altitude", 0)

                        if transformer is None:
                            zone_number = int((lon + 180) / 6) + 1
                            is_south = lat < 0
                            utm_crs = f"EPSG:32{'7' if is_south else '6'}{zone_number:02d}"
                            transformer = pyproj.Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)

                        x, y = transformer.transform(lon, lat)
                        handle.write(f"{img_name} {x} {y} {alt}\n")
                        count += 1
                except Exception as error:
                    logger.debug("Skipping GPS extraction for %s: %s", img_name, error)
                    continue
    report_fn(vol_id, "GPS_EXTRACTION", 12, log=f"Extracted GPS from {count}/{len(images)} images using EXIF. Using CRS {utm_crs}")
    return utm_crs


def read_saved_utm_crs(geo_data_file):
    crs_file = f"{geo_data_file}.crs"
    if not os.path.exists(crs_file):
        return None
    try:
        with open(crs_file, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        return value or None
    except OSError:
        return None


def save_utm_crs(geo_data_file, utm_crs):
    if not utm_crs:
        return
    crs_file = f"{geo_data_file}.crs"
    try:
        with open(crs_file, "w", encoding="utf-8") as handle:
            handle.write(f"{utm_crs}\n")
    except OSError as error:
        logger.warning("Failed to save UTM CRS for %s: %s", geo_data_file, error)


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