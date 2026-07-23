"""Inspect an aerial image dataset without modifying the source files."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from PIL import ExifTags, Image


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
GPS_IFD_TAG = 0x8825
EXIF_MAKE_TAG = 0x010F
EXIF_MODEL_TAG = 0x0110
EXIF_DATETIME_ORIGINAL_TAG = 0x9003
EXIF_FOCAL_LENGTH_TAG = 0x920A


def _as_float(value: Any) -> float:
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[0]) / float(value[1])
    return float(value)


def dms_to_decimal(value: Iterable[Any], reference: str) -> float:
    degrees, minutes, seconds = [_as_float(item) for item in value]
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if reference.upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


def utm_epsg(latitude: float, longitude: float) -> str:
    zone = max(1, min(60, int((longitude + 180.0) / 6.0) + 1))
    return f"EPSG:{32700 + zone if latitude < 0 else 32600 + zone}"


def haversine_distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 6_371_008.8 * 2.0 * math.atan2(math.sqrt(value), math.sqrt(1.0 - value))


def discover_images(dataset: Path) -> list[Path]:
    return sorted(
        path
        for path in dataset.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _named_gps_data(exif: Any) -> dict[str, Any]:
    try:
        raw_gps = exif.get_ifd(GPS_IFD_TAG)
    except (AttributeError, KeyError, TypeError):
        raw_gps = exif.get(GPS_IFD_TAG, {})
    if not raw_gps:
        return {}
    return {ExifTags.GPSTAGS.get(key, str(key)): value for key, value in raw_gps.items()}


def _detailed_exif_data(exif: Any) -> dict[int, Any]:
    try:
        return dict(exif.get_ifd(ExifTags.IFD.Exif))
    except (AttributeError, KeyError, TypeError):
        return {}


def _altitude_reference_is_below_sea_level(value: Any) -> bool:
    if isinstance(value, bytes):
        return bool(value and value[0] == 1)
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def inspect_image(path: Path, dataset: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "file": path.relative_to(dataset).as_posix(),
        "size_bytes": path.stat().st_size,
        "readable": False,
        "gps": None,
        "error": None,
    }
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            detailed_exif = _detailed_exif_data(exif)
            captured_at = detailed_exif.get(
                EXIF_DATETIME_ORIGINAL_TAG,
                exif.get(EXIF_DATETIME_ORIGINAL_TAG),
            )
            focal_length = detailed_exif.get(
                EXIF_FOCAL_LENGTH_TAG,
                exif.get(EXIF_FOCAL_LENGTH_TAG),
            )
            record.update(
                {
                    "readable": True,
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                    "camera_make": str(exif.get(EXIF_MAKE_TAG) or "").strip() or None,
                    "camera_model": str(exif.get(EXIF_MODEL_TAG) or "").strip() or None,
                    "captured_at": str(captured_at or "").strip() or None,
                    "focal_length_mm": (
                        _as_float(focal_length)
                        if focal_length is not None
                        else None
                    ),
                }
            )
            gps = _named_gps_data(exif)
            latitude_dms = gps.get("GPSLatitude")
            longitude_dms = gps.get("GPSLongitude")
            if latitude_dms and longitude_dms:
                latitude = dms_to_decimal(latitude_dms, str(gps.get("GPSLatitudeRef", "N")))
                longitude = dms_to_decimal(longitude_dms, str(gps.get("GPSLongitudeRef", "E")))
                altitude_value = gps.get("GPSAltitude")
                altitude = _as_float(altitude_value) if altitude_value is not None else None
                if altitude is not None and _altitude_reference_is_below_sea_level(gps.get("GPSAltitudeRef")):
                    altitude = -altitude
                horizontal_error = gps.get("GPSHPositioningError")
                record["gps"] = {
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude_m": altitude,
                    "horizontal_error_m": (
                        _as_float(horizontal_error) if horizontal_error is not None else None
                    ),
                }
    except Exception as error:  # Pillow exposes several format-specific exceptions.
        record["error"] = f"{type(error).__name__}: {error}"
    return record


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def build_report(
    records: list[dict[str, Any]],
    *,
    dataset: Path,
    gps_quality: str = "unknown",
) -> dict[str, Any]:
    readable = [record for record in records if record["readable"]]
    positioned = [record for record in readable if record["gps"] is not None]
    coordinates = [
        (record["gps"]["latitude"], record["gps"]["longitude"])
        for record in positioned
    ]
    altitudes = [
        record["gps"]["altitude_m"]
        for record in positioned
        if record["gps"]["altitude_m"] is not None
    ]
    timestamps = sorted(
        record["captured_at"]
        for record in readable
        if record.get("captured_at")
    )
    path_length_m = sum(
        haversine_distance_m(first, second)
        for first, second in zip(coordinates, coordinates[1:])
    )

    warnings = []
    if len(positioned) != len(records):
        warnings.append(f"{len(records) - len(positioned)} image(s) have no usable GPS position.")
    if gps_quality != "rtk":
        warnings.append(
            "GPS positions are not treated as centimetric ground truth; use robust alignment tolerances."
        )
    if altitudes and max(altitudes) - min(altitudes) > 50:
        warnings.append("The EXIF altitude range exceeds 50 m; verify the vertical reference.")

    centroid = None
    bounds = None
    projected_crs = None
    if coordinates:
        centroid = {
            "latitude": mean(item[0] for item in coordinates),
            "longitude": mean(item[1] for item in coordinates),
        }
        bounds = {
            "south": min(item[0] for item in coordinates),
            "west": min(item[1] for item in coordinates),
            "north": max(item[0] for item in coordinates),
            "east": max(item[1] for item in coordinates),
        }
        projected_crs = utm_epsg(centroid["latitude"], centroid["longitude"])

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset.resolve()),
        "gps_quality_assumption": gps_quality,
        "summary": {
            "image_count": len(records),
            "readable_count": len(readable),
            "gps_count": len(positioned),
            "gps_coverage_percent": round(100.0 * len(positioned) / len(records), 2) if records else 0.0,
            "total_size_bytes": sum(record["size_bytes"] for record in records),
            "camera_makes": _counter_dict(record.get("camera_make") for record in readable),
            "camera_models": _counter_dict(record.get("camera_model") for record in readable),
            "dimensions": _counter_dict(
                f"{record['width']}x{record['height']}" for record in readable
            ),
            "capture_start": timestamps[0] if timestamps else None,
            "capture_end": timestamps[-1] if timestamps else None,
            "approximate_flight_path_m": round(path_length_m, 2),
            "altitude_m": (
                {
                    "minimum": min(altitudes),
                    "maximum": max(altitudes),
                    "mean": mean(altitudes),
                    "median": median(altitudes),
                }
                if altitudes
                else None
            ),
            "centroid": centroid,
            "bounds": bounds,
            "recommended_projected_crs": projected_crs,
        },
        "warnings": warnings,
        "images": records,
    }


def build_geojson(records: list[dict[str, Any]]) -> dict[str, Any]:
    positioned = [record for record in records if record["gps"] is not None]
    line_coordinates = [
        [
            record["gps"]["longitude"],
            record["gps"]["latitude"],
            record["gps"]["altitude_m"] or 0.0,
        ]
        for record in positioned
    ]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": line_coordinates},
            "properties": {"kind": "flight_path", "image_count": len(positioned)},
        }
    ]
    features.extend(
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coordinates},
            "properties": {"kind": "camera", "file": record["file"], "index": index},
        }
        for index, (record, coordinates) in enumerate(zip(positioned, line_coordinates))
    )
    return {"type": "FeatureCollection", "features": features}


def inspect_dataset(dataset: Path, gps_quality: str = "unknown") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dataset = dataset.resolve()
    if not dataset.is_dir():
        raise ValueError(f"Dataset directory does not exist: {dataset}")
    image_paths = discover_images(dataset)
    if not image_paths:
        raise ValueError(f"No supported images found in: {dataset}")
    records = [inspect_image(path, dataset) for path in image_paths]
    return records, build_report(records, dataset=dataset, gps_quality=gps_quality)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="JSON report destination")
    parser.add_argument("--geojson", type=Path, help="Optional GeoJSON camera trajectory destination")
    parser.add_argument(
        "--gps-quality",
        choices=["unknown", "standard", "rtk"],
        default="unknown",
        help="Positioning quality assumption used in the report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records, report = inspect_dataset(args.dataset, gps_quality=args.gps_quality)
    write_json(args.output, report)
    if args.geojson:
        write_json(args.geojson, build_geojson(records))
    summary = report["summary"]
    print(f"Images: {summary['image_count']} ({summary['gps_coverage_percent']:.2f}% with GPS)")
    print(f"Camera models: {summary['camera_models']}")
    print(f"Recommended CRS: {summary['recommended_projected_crs']}")
    print(f"Approximate flight path: {summary['approximate_flight_path_m']:.1f} m")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
