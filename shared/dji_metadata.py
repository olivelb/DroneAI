"""DJI timestamp/position sidecar parsing.

DJI Enterprise ``*_Timestamp.MRK`` files contain one exposure record per
image, including ellipsoidal coordinates and estimated N/E/V standard
deviations. The parser is intentionally permissive because firmware versions
vary in spacing while retaining the comma-suffixed field names.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable


SEQUENCE_PATTERN = re.compile(r"_(\d{4,6})_[A-Za-z0-9-]+\.[^.]+$")


def image_sequence_number(path: str | Path) -> int | None:
    match = SEQUENCE_PATTERN.search(Path(path).name)
    return int(match.group(1)) if match else None


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
            latitude = _field_value(fields, ",Lat")
            longitude = _field_value(fields, ",Lon")
            ellipsoid_height = _field_value(fields, ",Ellh")
            if latitude is None or longitude is None:
                continue

            standard_deviations = None
            ellipsoid_index = next(
                (
                    index
                    for index, field in enumerate(fields)
                    if field.strip().endswith(",Ellh")
                ),
                None,
            )
            if ellipsoid_index is not None and ellipsoid_index + 1 < len(fields):
                candidates = [
                    item.strip()
                    for item in fields[ellipsoid_index + 1].split(",")
                    if item.strip()
                ]
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
            }
    return marks


def load_dji_mrk_overrides(
    dataset: str | Path,
    image_paths: Iterable[Path],
) -> dict[str, dict[str, Any]]:
    root = Path(dataset).resolve()
    images_by_parent_and_sequence: dict[tuple[Path, int], Path] = {}
    for image_path in image_paths:
        image_path = Path(image_path)
        sequence = image_sequence_number(image_path)
        if sequence is not None:
            images_by_parent_and_sequence[(image_path.parent, sequence)] = image_path

    overrides: dict[str, dict[str, Any]] = {}
    sidecars = sorted(
        Path(directory) / filename
        for directory, _, filenames in os.walk(root)
        for filename in filenames
        if Path(filename).suffix.lower() == ".mrk"
    )
    for sidecar in sidecars:
        for sequence, gps in parse_dji_mrk_file(sidecar).items():
            image_path = images_by_parent_and_sequence.get(
                (sidecar.parent, sequence)
            )
            if image_path is None:
                continue
            overrides[image_path.relative_to(root).as_posix()] = gps
    return overrides
