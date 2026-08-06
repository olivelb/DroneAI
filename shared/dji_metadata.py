"""Aerial RTK metadata parsing with a DJI-compatible facade.

DJI Enterprise ``*_Timestamp.MRK`` files contain one exposure record per
image, including ellipsoidal coordinates and estimated N/E/V standard
deviations. The parser is intentionally permissive because firmware versions
vary in spacing while retaining the comma-suffixed field names. Autel and DJI
also embed equivalent RTK covariance in an XMP APP1 segment.
"""

from __future__ import annotations

import os
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterable

SEQUENCE_PATTERNS = (
    re.compile(r"_(\d{4,6})_[A-Za-z0-9-]+\.[^.]+$"),
    re.compile(r"_(\d{4,6})\.[^.]+$"),
)
XMP_APP1_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
RTK_FLAG_STATES = {
    "0": "invalid",
    "16": "single",
    "34": "float",
    "50": "fixed",
    "false": "invalid",
    "invalid": "invalid",
    "none": "invalid",
    "no fix": "invalid",
    "nofix": "invalid",
    "single": "single",
    "float": "float",
    "rtk float": "float",
    "fix": "fixed",
    "fixed": "fixed",
    "rtk fix": "fixed",
    "rtk fixed": "fixed",
}


def classify_rtk_flag(value: Any) -> str:
    """Classify known DJI/Autel solution flags conservatively."""

    normalized = str(clean_metadata_text(value) or "").strip().lower()
    return RTK_FLAG_STATES.get(normalized, "unknown")


def image_sequence_number(path: str | Path) -> int | None:
    for pattern in SEQUENCE_PATTERNS:
        match = pattern.search(Path(path).name)
        if match:
            return int(match.group(1))
    return None


def clean_metadata_text(value: Any) -> str | None:
    """Normalize EXIF/XMP strings, including vendor NUL padding."""

    if value is None:
        return None
    cleaned = str(value).replace("\x00", "").strip()
    return cleaned or None


def _jpeg_xmp_packet(path: str | Path) -> bytes | None:
    """Read the standard XMP APP1 payload without decoding image pixels."""

    try:
        with Path(path).open("rb") as handle:
            if handle.read(2) != b"\xff\xd8":
                return None
            while True:
                prefix = handle.read(1)
                if not prefix:
                    return None
                if prefix != b"\xff":
                    continue
                marker = handle.read(1)
                while marker == b"\xff":
                    marker = handle.read(1)
                if not marker or marker in {b"\xd9", b"\xda"}:
                    return None
                if marker in {
                    b"\x01",
                    *[bytes([value]) for value in range(0xD0, 0xD8)],
                }:
                    continue
                raw_length = handle.read(2)
                if len(raw_length) != 2:
                    return None
                payload_length = int.from_bytes(raw_length, "big") - 2
                if payload_length < 0:
                    return None
                payload = handle.read(payload_length)
                if len(payload) != payload_length:
                    return None
                if marker == b"\xe1" and payload.startswith(XMP_APP1_HEADER):
                    return payload[len(XMP_APP1_HEADER) :]
    except OSError:
        return None


def _xmp_properties(path: str | Path) -> dict[str, str]:
    packet = _jpeg_xmp_packet(path)
    if not packet:
        return {}
    lowered = packet.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError(f"Unsafe XMP declaration in {Path(path).name}")
    try:
        root = ET.fromstring(packet)
    except ET.ParseError:
        return {}
    properties: dict[str, str] = {}
    for element in root.iter():
        for raw_name, raw_value in element.attrib.items():
            local_name = raw_name.rsplit("}", 1)[-1]
            value = clean_metadata_text(raw_value)
            if value is not None:
                properties[local_name] = value
    return properties


def _optional_float(properties: dict[str, str], *names: str) -> float | None:
    for name in names:
        value = properties.get(name)
        if value is None:
            continue
        try:
            return float(value)
        except ValueError:
            return None
    return None


def parse_aerial_xmp(path: str | Path) -> dict[str, Any]:
    """Return normalized Autel/DJI XMP calibration and RTK metadata."""

    properties = _xmp_properties(path)
    if not properties:
        return {}

    latitude = _optional_float(properties, "GpsLatitude", "GPSLatitude")
    longitude = _optional_float(
        properties,
        "GpsLongtitude",  # spelling used by Autel and DJI
        "GpsLongitude",
        "GPSLongitude",
    )
    altitude = _optional_float(properties, "AbsoluteAltitude")
    rtk_flag = clean_metadata_text(properties.get("RtkFlag"))
    rtk_status = classify_rtk_flag(rtk_flag)
    standard_deviations = {
        "north_m": _optional_float(properties, "RtkStdLat"),
        "east_m": _optional_float(properties, "RtkStdLon"),
        "vertical_m": _optional_float(properties, "RtkStdHgt"),
    }
    covariance_complete = all(value is not None and value > 0 for value in standard_deviations.values())
    rtk_valid = (
        rtk_status == "fixed"
        and covariance_complete
        and latitude is not None
        and longitude is not None
        and altitude is not None
    )

    metadata: dict[str, Any] = {
        "provider": "autel_dji_xmp",
        "camera_make": clean_metadata_text(properties.get("Make")),
        "camera_model": clean_metadata_text(properties.get("Model")),
        "captured_at": clean_metadata_text(properties.get("DateTimeOriginal") or properties.get("CreateDate")),
        "calibrated_focal_length_px": _optional_float(
            properties,
            "CalibratedFocalLength",
        ),
        "rtk_flag": rtk_flag,
        "rtk_status": rtk_status,
        "flight_attitude_deg": {
            "roll": _optional_float(properties, "FlightRollDegree"),
            "pitch": _optional_float(properties, "FlightPitchDegree"),
            "yaw": _optional_float(properties, "FlightYawDegree"),
        },
        "gimbal_attitude_deg": {
            "roll": _optional_float(properties, "GimbalRollDegree"),
            "pitch": _optional_float(properties, "GimbalPitchDegree"),
            "yaw": _optional_float(properties, "GimbalYawDegree"),
        },
    }
    if rtk_valid:
        metadata["gps"] = {
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude,
            "horizontal_error_m": max(
                cast(float, standard_deviations["north_m"]),
                cast(float, standard_deviations["east_m"]),
            ),
            "position_std_m": standard_deviations,
            # Vendor XMP does not carry an EPSG vertical CRS. Keep the
            # reference explicit instead of silently calling it orthometric.
            "vertical_reference": "vendor-ellipsoidal",
            "vertical_reference_source": "xmp_absolute_altitude",
            "source": "xmp_rtk",
            "rtk_flag": rtk_flag,
            "rtk_status": rtk_status,
            "provider": metadata["provider"],
        }
    return metadata


def parse_xmp_rtk(path: str | Path) -> dict[str, Any] | None:
    """Compatibility helper returning only a valid RTK position."""

    return parse_aerial_xmp(path).get("gps")


def _field_value(fields: Iterable[str], suffix: str) -> float | None:
    for field in fields:
        stripped = field.strip()
        if stripped.endswith(suffix):
            try:
                return float(stripped[: -len(suffix)].strip())
            except ValueError:
                return None
    return None


def parse_dji_mrk_file(path: str | Path) -> dict[int, dict[str, Any]]:
    marks: dict[int, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            fields = [field.strip() for field in raw_line.split("\t") if field.strip()]
            if len(fields) < 9:
                continue
            try:
                sequence = int(fields[0])
            except ValueError:
                continue
            try:
                gps_seconds_of_week = float(fields[1])
            except ValueError:
                gps_seconds_of_week = None
            try:
                gps_week = int(fields[2].strip().strip("[]"))
            except ValueError:
                gps_week = None
            latitude = _field_value(fields, ",Lat")
            longitude = _field_value(fields, ",Lon")
            ellipsoid_height = _field_value(fields, ",Ellh")
            if latitude is None or longitude is None:
                continue

            standard_deviations = None
            ellipsoid_index = next(
                (index for index, field in enumerate(fields) if field.strip().endswith(",Ellh")),
                None,
            )
            if ellipsoid_index is not None and ellipsoid_index + 1 < len(fields):
                candidates = [item.strip() for item in fields[ellipsoid_index + 1].split(",") if item.strip()]
                if len(candidates) >= 3:
                    try:
                        standard_deviations = {
                            "north_m": float(candidates[0]),
                            "east_m": float(candidates[1]),
                            "vertical_m": float(candidates[2]),
                        }
                    except ValueError:
                        standard_deviations = None

            marks[sequence] = {
                "latitude": latitude,
                "longitude": longitude,
                "altitude_m": ellipsoid_height,
                "horizontal_error_m": (
                    max(
                        standard_deviations["north_m"],
                        standard_deviations["east_m"],
                    )
                    if standard_deviations
                    else None
                ),
                "position_std_m": standard_deviations,
                "vertical_reference": "ellipsoidal",
                "vertical_reference_source": "dji_mrk_ellh",
                "source": "dji_mrk",
                "sidecar": Path(path).name,
                "gps_week": gps_week,
                "gps_seconds_of_week": gps_seconds_of_week,
                "rtk_flag": clean_metadata_text(
                    next(
                        (field[: -len(",Q")].strip() for field in fields if field.strip().endswith(",Q")),
                        None,
                    )
                ),
            }
            marks[sequence]["rtk_status"] = classify_rtk_flag(marks[sequence]["rtk_flag"])
            marks[sequence]["rtk_valid"] = (
                marks[sequence]["rtk_status"] == "fixed"
                and ellipsoid_height is not None
                and standard_deviations is not None
                and all(value > 0 for value in standard_deviations.values())
            )
    return marks


def _horizontal_distance_m(first: dict[str, Any], second: dict[str, Any]) -> float:
    radius_m = 6_371_008.8
    latitude_a = math.radians(float(first["latitude"]))
    latitude_b = math.radians(float(second["latitude"]))
    delta_latitude = latitude_b - latitude_a
    delta_longitude = math.radians(float(second["longitude"]) - float(first["longitude"]))
    haversine = (
        math.sin(delta_latitude / 2.0) ** 2
        + math.cos(latitude_a) * math.cos(latitude_b) * math.sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(min(1.0, math.sqrt(haversine)))


def _validate_mrk_xmp_position(
    image_path: Path,
    mrk: dict[str, Any],
    xmp: dict[str, Any],
) -> dict[str, Any]:
    horizontal_delta = _horizontal_distance_m(mrk, xmp)
    mrk_std = mrk.get("position_std_m") or {}
    xmp_std = xmp.get("position_std_m") or {}
    horizontal_sigma = math.hypot(
        max(float(mrk_std.get("north_m") or 0), float(mrk_std.get("east_m") or 0)),
        max(float(xmp_std.get("north_m") or 0), float(xmp_std.get("east_m") or 0)),
    )
    horizontal_limit = max(0.25, 6.0 * horizontal_sigma)
    vertical_delta = abs(float(mrk["altitude_m"]) - float(xmp["altitude_m"]))
    vertical_sigma = math.hypot(
        float(mrk_std.get("vertical_m") or 0),
        float(xmp_std.get("vertical_m") or 0),
    )
    vertical_limit = max(0.50, 6.0 * vertical_sigma)
    if horizontal_delta > horizontal_limit or vertical_delta > vertical_limit:
        raise ValueError(
            f"MRK/XMP position mismatch for {image_path.name}: "
            f"horizontal={horizontal_delta:.3f} m (limit {horizontal_limit:.3f}), "
            f"vertical={vertical_delta:.3f} m (limit {vertical_limit:.3f})"
        )
    validation = {
        "method": "sequence-and-position",
        "xmp_horizontal_delta_m": horizontal_delta,
        "xmp_vertical_delta_m": vertical_delta,
        "horizontal_limit_m": horizontal_limit,
        "vertical_limit_m": vertical_limit,
    }
    capture_time = _read_exif_capture_datetime(image_path)
    if capture_time is not None and mrk.get("gps_week") is not None and mrk.get("gps_seconds_of_week") is not None:
        # GPS time is ahead of UTC by 18 seconds for contemporary DJI/Autel
        # datasets (2017 onward). EXIF commonly stores local wall time without
        # an offset, so evaluate legal whole-hour UTC offsets and retain the
        # unique closest interpretation.
        gps_utc = datetime(1980, 1, 6, tzinfo=UTC) + timedelta(
            weeks=int(mrk["gps_week"]),
            seconds=float(mrk["gps_seconds_of_week"]) - 18.0,
        )
        candidates = [
            (
                abs((capture_time.replace(tzinfo=UTC) - timedelta(hours=offset) - gps_utc).total_seconds()),
                offset,
            )
            for offset in range(-14, 15)
        ]
        timestamp_delta, utc_offset = min(candidates)
        timestamp_limit = 5.0
        if timestamp_delta > timestamp_limit:
            raise ValueError(
                f"MRK/EXIF timestamp mismatch for {image_path.name}: minimum delta={timestamp_delta:.3f} s"
            )
        validation.update(
            {
                "method": "sequence-position-and-timestamp",
                "exif_to_utc_offset_hours": utc_offset,
                "timestamp_delta_seconds": timestamp_delta,
                "timestamp_limit_seconds": timestamp_limit,
            }
        )
    return validation


def _read_exif_capture_datetime(path: str | Path) -> datetime | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            raw = clean_metadata_text(exif.get(36867) or exif.get(306))
        if raw is None:
            return None
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except (ImportError, OSError, TypeError, ValueError):
        return None


def load_dji_mrk_overrides(
    dataset: str | Path,
    image_paths: Iterable[Path],
) -> dict[str, dict[str, Any]]:
    root = Path(dataset).resolve()
    images_by_parent_and_sequence: dict[tuple[Path, int], list[Path]] = {}
    images_by_sequence: dict[int, list[Path]] = {}
    for image_path in image_paths:
        image_path = Path(image_path).resolve()
        sequence = image_sequence_number(image_path)
        if sequence is not None:
            images_by_parent_and_sequence.setdefault(
                (image_path.parent, sequence),
                [],
            ).append(image_path)
            images_by_sequence.setdefault(sequence, []).append(image_path)

    overrides: dict[str, dict[str, Any]] = {}
    assigned_sidecars: dict[str, Path] = {}
    sidecars = sorted(
        Path(directory) / filename
        for directory, _, filenames in os.walk(root)
        for filename in filenames
        if Path(filename).suffix.lower() == ".mrk"
    )
    for sidecar in sidecars:
        for sequence, gps in parse_dji_mrk_file(sidecar).items():
            if not gps.get("rtk_valid"):
                continue
            local_candidates = images_by_parent_and_sequence.get(
                (sidecar.parent.resolve(), sequence),
                [],
            )
            candidates = local_candidates if local_candidates else images_by_sequence.get(sequence, [])
            if not candidates:
                continue
            if len(candidates) != 1:
                names = ", ".join(path.relative_to(root).as_posix() for path in candidates)
                raise ValueError(
                    f"Ambiguous MRK sequence {sequence} from {sidecar.relative_to(root).as_posix()}: {names}"
                )
            image_path = candidates[0]
            xmp_gps = parse_xmp_rtk(image_path)
            if xmp_gps is not None:
                gps["association_validation"] = _validate_mrk_xmp_position(image_path, gps, xmp_gps)
            else:
                gps["association_validation"] = {
                    "method": "sequence-only",
                    "reason": "no independently valid fixed-RTK XMP position",
                }
            relative = image_path.relative_to(root).as_posix()
            previous_sidecar = assigned_sidecars.get(relative)
            if previous_sidecar is not None and previous_sidecar != sidecar:
                raise ValueError(
                    f"Multiple MRK sidecars match {relative}: "
                    f"{previous_sidecar.relative_to(root).as_posix()}, "
                    f"{sidecar.relative_to(root).as_posix()}"
                )
            overrides[relative] = gps
            assigned_sidecars[relative] = sidecar
    return overrides


def load_position_overrides(
    dataset: str | Path,
    image_paths: Iterable[Path],
) -> dict[str, dict[str, Any]]:
    """Resolve positions by priority: MRK, valid XMP RTK, then caller EXIF."""

    root = Path(dataset).resolve()
    paths = [Path(path).resolve() for path in image_paths]
    overrides = {path.relative_to(root).as_posix(): gps for path in paths if (gps := parse_xmp_rtk(path)) is not None}
    overrides.update(load_dji_mrk_overrides(root, paths))
    return overrides
