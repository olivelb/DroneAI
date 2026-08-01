from __future__ import annotations

import math
import sqlite3
import struct
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from shared.dji_metadata import load_position_overrides, parse_aerial_xmp


def load_rtk_records(image_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(image_dir)
    image_paths = sorted(
        path
        for path in root.rglob("*")
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    overrides = load_position_overrides(root, image_paths)
    records = []
    for path in image_paths:
        relative = path.relative_to(root).as_posix()
        gps = overrides.get(relative) or overrides.get(path.name)
        xmp = parse_aerial_xmp(path)
        if gps is not None or xmp.get("gimbal_attitude_deg"):
            records.append(
                {
                    "file": relative,
                    "gps": gps,
                    "gimbal_attitude_deg": xmp.get("gimbal_attitude_deg"),
                    "camera_make": xmp.get("camera_make"),
                    "camera_model": xmp.get("camera_model"),
                }
            )
    return records


def gimbal_attitude_to_gravity_sensor(
    attitude: dict[str, Any],
) -> tuple[float, float, float]:
    """Convert aerial yaw/pitch/roll metadata to gravity in camera axes.

    Camera axes follow COLMAP (X right, Y down, Z forward). Vendor pitch is
    elevation (nadir is -90 degrees) and roll is about the optical axis. Yaw
    cancels because gravity is expressed in the camera frame.
    """

    pitch = math.radians(float(attitude["pitch"]))
    roll = math.radians(float(attitude["roll"]))
    gravity = (
        math.sin(roll) * math.cos(pitch),
        math.cos(roll) * math.cos(pitch),
        -math.sin(pitch),
    )
    norm = math.sqrt(sum(value * value for value in gravity))
    if not math.isfinite(norm) or norm < 1.0e-9:
        raise ValueError("invalid gimbal attitude")
    return tuple(value / norm for value in gravity)


def inject_database_gravity_priors(
    database_path: str | Path,
    records: Iterable[dict[str, Any]],
    *,
    minimum_coverage: float = 0.95,
) -> dict[str, Any]:
    """Write validated gimbal-derived gravity vectors into COLMAP priors."""

    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be in (0, 1]")
    candidates: dict[str, tuple[float, float, float]] = {}
    basename_candidates: dict[
        str, list[tuple[str, tuple[float, float, float]]]
    ] = defaultdict(list)
    camera_pairs: set[str] = set()
    for record in records:
        attitude = record.get("gimbal_attitude_deg") or {}
        if not all(attitude.get(axis) is not None for axis in ("roll", "pitch")):
            continue
        try:
            gravity = gimbal_attitude_to_gravity_sensor(attitude)
        except (TypeError, ValueError):
            continue
        filename = str(record.get("file") or "").replace("\\", "/")
        if not filename:
            continue
        candidates[filename] = gravity
        basename_candidates[Path(filename).name].append((filename, gravity))
        camera_pairs.add(
            f"{record.get('camera_make') or 'unknown'} / "
            f"{record.get('camera_model') or 'unknown'}"
        )

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT images.name, pose_priors.pose_prior_id
            FROM images
            JOIN frame_data
              ON frame_data.data_id = images.image_id
             AND frame_data.sensor_type = 0
            JOIN pose_priors
              ON pose_priors.corr_data_id = frame_data.frame_id
             AND pose_priors.corr_sensor_type = 0
            """
        ).fetchall()

        def gravity_for_name(name: str) -> tuple[float, float, float] | None:
            exact = candidates.get(name)
            if exact is not None:
                return exact
            basename_matches = basename_candidates.get(Path(name).name, [])
            if len(basename_matches) == 1:
                return basename_matches[0][1]
            return None

        matched_rows = [
            (name, prior_id, gravity_for_name(name))
            for name, prior_id in rows
        ]
        matched_rows = [row for row in matched_rows if row[2] is not None]
        coverage = len(matched_rows) / len(rows) if rows else 0.0
        enabled = len(matched_rows) >= 3 and coverage >= minimum_coverage
        if enabled:
            with connection:
                for _, pose_prior_id, gravity in matched_rows:
                    connection.execute(
                        "UPDATE pose_priors SET gravity = ? WHERE pose_prior_id = ?",
                        (struct.pack("<3d", *gravity), pose_prior_id),
                    )
    finally:
        connection.close()

    return {
        "schema_version": 1,
        "status": "enabled" if enabled else "insufficient-coverage",
        "database_pose_priors": len(rows),
        "attitude_pose_priors": len(matched_rows),
        "coverage": coverage,
        "minimum_coverage": minimum_coverage,
        "camera_pairs": sorted(camera_pairs),
        "ambiguous_basenames": sorted(
            name for name, matches in basename_candidates.items() if len(matches) > 1
        ),
        "convention": "COLMAP camera XYZ: right, down, forward",
        "use_in_global_rotation_averaging": enabled,
    }


def inject_database_pose_priors(
    database_path: str | Path,
    records: Iterable[dict[str, Any]],
    *,
    minimum_coverage: float = 0.95,
) -> dict[str, Any]:
    """Replace EXIF priors with DJI MRK positions and Cartesian ENU covariance."""
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be in (0, 1]")
    precise_records: dict[str, dict[str, Any]] = {}
    for record in records:
        gps = record.get("gps")
        position_std = gps.get("position_std_m") if gps else None
        if (
            not gps
            or gps.get("source") not in {"dji_mrk", "xmp_rtk"}
            or gps.get("altitude_m") is None
            or not position_std
        ):
            continue
        values = (
            position_std.get("east_m"),
            position_std.get("north_m"),
            position_std.get("vertical_m"),
        )
        if not all(value is not None and float(value) > 0 for value in values):
            continue
        precise_records[record["file"]] = gps

    if len(precise_records) < 3:
        raise RuntimeError(
            "RTK refinement requires at least three MRK/XMP RTK records with "
            "positive east, north, and vertical standard deviations"
        )

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT images.name, pose_priors.pose_prior_id
            FROM images
            JOIN frame_data
              ON frame_data.data_id = images.image_id
             AND frame_data.sensor_type = 0
            JOIN pose_priors
              ON pose_priors.corr_data_id = frame_data.frame_id
             AND pose_priors.corr_sensor_type = 0
            """
        ).fetchall()
        matched_names = sum(
            1 for image_name, _ in rows if image_name in precise_records
        )
        minimum_matched = max(3, math.ceil(len(rows) * minimum_coverage))
        if matched_names < minimum_matched:
            raise RuntimeError(
                f"only {matched_names}/{len(rows)} database pose priors have usable "
                f"RTK records; required {minimum_matched}"
            )
        matched = 0
        horizontal_errors = []
        vertical_errors = []
        with connection:
            for image_name, pose_prior_id in rows:
                gps = precise_records.get(image_name)
                if gps is None:
                    continue
                position_std = gps["position_std_m"]
                east = float(position_std["east_m"])
                north = float(position_std["north_m"])
                vertical = float(position_std["vertical_m"])
                position = struct.pack(
                    "<3d",
                    float(gps["latitude"]),
                    float(gps["longitude"]),
                    float(gps["altitude_m"]),
                )
                covariance = struct.pack(
                    "<9d",
                    east * east,
                    0.0,
                    0.0,
                    0.0,
                    north * north,
                    0.0,
                    0.0,
                    0.0,
                    vertical * vertical,
                )
                connection.execute(
                    """
                    UPDATE pose_priors
                    SET position = ?, position_covariance = ?, coordinate_system = 0
                    WHERE pose_prior_id = ?
                    """,
                    (position, covariance, pose_prior_id),
                )
                matched += 1
                horizontal_errors.append(math.hypot(east, north))
                vertical_errors.append(vertical)
    finally:
        connection.close()

    return {
        "schema_version": 1,
        "sources": sorted(
            {
                str(gps.get("source"))
                for gps in precise_records.values()
            }
        ),
        "coordinate_system": "WGS84",
        "covariance_coordinate_system": "local_cartesian_enu",
        "available_records": len(precise_records),
        "database_pose_priors": len(rows),
        "updated_pose_priors": matched,
        "horizontal_std_m": {
            "minimum": min(horizontal_errors),
            "maximum": max(horizontal_errors),
            "mean": mean(horizontal_errors),
            "median": median(horizontal_errors),
        },
        "vertical_std_m": {
            "minimum": min(vertical_errors),
            "maximum": max(vertical_errors),
            "mean": mean(vertical_errors),
            "median": median(vertical_errors),
        },
    }
