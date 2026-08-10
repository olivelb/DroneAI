"""Deterministic import of common ground-control point exchange formats."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer

from shared.gcp_control import normalize_gcp_role


@dataclass(frozen=True)
class ImportedGcpObservation:
    image_name: str
    pixel_x: float
    pixel_y: float


@dataclass(frozen=True)
class ImportedGcpPoint:
    external_id: str
    source_x: float
    source_y: float
    source_z: float
    longitude: float
    latitude: float
    altitude_m: float
    role: str
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    image_accuracy_px: float
    observations: tuple[ImportedGcpObservation, ...] = field(default_factory=tuple)
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImportedGcpSet:
    source_format: str
    source_crs: str
    points: tuple[ImportedGcpPoint, ...]


ID_ALIASES = ("point_id", "id", "label", "name", "marker", "gcp")
X_ALIASES = ("x", "easting", "east", "longitude", "lon", "lng")
Y_ALIASES = ("y", "northing", "north", "latitude", "lat")
Z_ALIASES = ("z", "altitude", "elevation", "height", "alt")
ROLE_ALIASES = ("role", "usage", "type")
H_ACCURACY_ALIASES = (
    "horizontal_accuracy_m",
    "horizontal_accuracy",
    "accuracy_xy",
    "sigma_xy",
)
V_ACCURACY_ALIASES = (
    "vertical_accuracy_m",
    "vertical_accuracy",
    "accuracy_z",
    "sigma_z",
)
IMAGE_ACCURACY_ALIASES = ("image_accuracy_px", "accuracy_px", "sigma_px")


def _positive(value: Any, fallback: float, label: str) -> float:
    if value in (None, ""):
        return fallback
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return parsed


def _first(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in normalized and normalized[alias] not in (None, ""):
            return normalized[alias]
    return None


def _transformer(source_crs: str) -> tuple[str, Transformer]:
    crs = CRS.from_user_input(source_crs)
    canonical = crs.to_string()
    return canonical, Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def _point(
    row: dict[str, Any],
    *,
    transformer: Transformer,
    default_role: str,
    horizontal_accuracy_m: float,
    vertical_accuracy_m: float,
    image_accuracy_px: float,
) -> ImportedGcpPoint:
    external_id = str(_first(row, ID_ALIASES) or "").strip()
    if not external_id:
        raise ValueError("GCP point identifier is missing")
    try:
        source_x = float(_first(row, X_ALIASES))
        source_y = float(_first(row, Y_ALIASES))
        raw_z = _first(row, Z_ALIASES)
        source_z = float(raw_z) if raw_z not in (None, "") else 0.0
    except (TypeError, ValueError) as error:
        raise ValueError(f"GCP {external_id}: invalid X/Y/Z coordinate") from error
    if not all(math.isfinite(value) for value in (source_x, source_y, source_z)):
        raise ValueError(f"GCP {external_id}: X/Y/Z coordinates must be finite")
    longitude, latitude = transformer.transform(source_x, source_y)
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError(f"GCP {external_id}: transformed coordinate is outside WGS84")
    role = normalize_gcp_role(str(_first(row, ROLE_ALIASES) or default_role))
    return ImportedGcpPoint(
        external_id=external_id,
        source_x=source_x,
        source_y=source_y,
        source_z=source_z,
        longitude=float(longitude),
        latitude=float(latitude),
        altitude_m=source_z,
        role=role,
        horizontal_accuracy_m=_positive(
            _first(row, H_ACCURACY_ALIASES),
            horizontal_accuracy_m,
            f"GCP {external_id} horizontal accuracy",
        ),
        vertical_accuracy_m=_positive(
            _first(row, V_ACCURACY_ALIASES),
            vertical_accuracy_m,
            f"GCP {external_id} vertical accuracy",
        ),
        image_accuracy_px=_positive(
            _first(row, IMAGE_ACCURACY_ALIASES),
            image_accuracy_px,
            f"GCP {external_id} image accuracy",
        ),
    )


def _parse_geojson(
    text: str,
    source_crs: str | None,
    defaults: dict[str, Any],
) -> ImportedGcpSet:
    payload = json.loads(text)
    if payload.get("type") != "FeatureCollection":
        raise ValueError("GCP GeoJSON must be a FeatureCollection")
    declared = (
        payload.get("crs", {}).get("properties", {}).get("name")
        if isinstance(payload.get("crs"), dict)
        else None
    )
    canonical, transformer = _transformer(source_crs or declared or "EPSG:4326")
    points: list[ImportedGcpPoint] = []
    for index, feature in enumerate(payload.get("features", []), start=1):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point" or len(geometry.get("coordinates", [])) < 2:
            raise ValueError(f"GeoJSON feature {index} must contain a Point")
        coordinates = geometry["coordinates"]
        properties = dict(feature.get("properties") or {})
        properties.setdefault("point_id", feature.get("id") or f"GCP-{index}")
        properties.setdefault("x", coordinates[0])
        properties.setdefault("y", coordinates[1])
        properties.setdefault("z", coordinates[2] if len(coordinates) > 2 else 0)
        point = _point(properties, transformer=transformer, **defaults)
        points.append(replace(point, properties=properties))
    return ImportedGcpSet("geojson", canonical, _unique_points(points))


def _parse_odm(
    lines: list[str],
    defaults: dict[str, Any],
) -> ImportedGcpSet:
    canonical, transformer = _transformer(lines[0])
    points: dict[str, ImportedGcpPoint] = {}
    observations: dict[str, list[ImportedGcpObservation]] = {}
    for line_number, line in enumerate(lines[1:], start=2):
        fields = line.split()
        if len(fields) < 7:
            raise ValueError(f"line {line_number}: expected ODM X Y Z pixelX pixelY image point")
        row = {
            "x": fields[0],
            "y": fields[1],
            "z": fields[2],
            "point_id": fields[6],
        }
        point = _point(row, transformer=transformer, **defaults)
        previous = points.get(point.external_id)
        if previous and (
            previous.source_x,
            previous.source_y,
            previous.source_z,
        ) != (point.source_x, point.source_y, point.source_z):
            raise ValueError(f"GCP {point.external_id}: inconsistent ODM coordinates")
        points.setdefault(point.external_id, point)
        observations.setdefault(point.external_id, []).append(
            ImportedGcpObservation(fields[5], float(fields[3]), float(fields[4]))
        )
    merged = [
        replace(point, observations=tuple(observations[point_id]))
        for point_id, point in points.items()
    ]
    return ImportedGcpSet("odm-gcp-list", canonical, _unique_points(merged))


def _unique_points(points: list[ImportedGcpPoint]) -> tuple[ImportedGcpPoint, ...]:
    result: dict[str, ImportedGcpPoint] = {}
    for point in points:
        if point.external_id in result:
            raise ValueError(f"duplicate GCP point identifier: {point.external_id}")
        result[point.external_id] = point
    if not result:
        raise ValueError("GCP file contains no points")
    return tuple(result.values())


def _parse_delimited(
    text: str,
    source_crs: str | None,
    defaults: dict[str, Any],
) -> ImportedGcpSet:
    if not source_crs:
        raise ValueError("source_crs is required for CSV/TSV and plain-text GCP files")
    canonical, transformer = _transformer(source_crs)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t ")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if row]
    if not rows:
        raise ValueError("empty GCP file")
    normalized_header = {cell.strip().lower() for cell in rows[0]}
    has_header = bool(normalized_header.intersection(ID_ALIASES + X_ALIASES + Y_ALIASES))
    dictionaries: list[dict[str, Any]] = []
    if has_header:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        dictionaries = [dict(row) for row in reader]
    else:
        for line_number, row in enumerate(rows, start=1):
            if len(row) < 4:
                raise ValueError(f"line {line_number}: expected id X Y Z or X Y Z id")
            if re.fullmatch(r"[-+0-9.eE]+", row[0].strip()):
                dictionaries.append(
                    {"x": row[0], "y": row[1], "z": row[2], "point_id": row[3]}
                )
            else:
                dictionaries.append(
                    {"point_id": row[0], "x": row[1], "y": row[2], "z": row[3]}
                )
    points = [
        _point(row, transformer=transformer, **defaults)
        for row in dictionaries
        if any(str(value).strip() for value in row.values())
    ]
    return ImportedGcpSet("delimited-text", canonical, _unique_points(points))


def import_gcp_bytes(
    payload: bytes,
    filename: str,
    *,
    source_crs: str | None = None,
    default_role: str = "adjustment",
    horizontal_accuracy_m: float = 0.02,
    vertical_accuracy_m: float = 0.03,
    image_accuracy_px: float = 1.0,
) -> ImportedGcpSet:
    """Parse a bounded upload into canonical WGS84 GCP records."""

    if not payload:
        raise ValueError("empty GCP upload")
    text = payload.decode("utf-8-sig")
    defaults = {
        "default_role": normalize_gcp_role(default_role),
        "horizontal_accuracy_m": _positive(horizontal_accuracy_m, 0.02, "horizontal accuracy"),
        "vertical_accuracy_m": _positive(vertical_accuracy_m, 0.03, "vertical accuracy"),
        "image_accuracy_px": _positive(image_accuracy_px, 1.0, "image accuracy"),
    }
    suffix = Path(filename).suffix.lower()
    stripped = text.lstrip()
    if suffix in {".geojson", ".json"} or stripped.startswith("{"):
        return _parse_geojson(text, source_crs, defaults)
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if lines:
        try:
            CRS.from_user_input(lines[0])
        except Exception:
            pass
        else:
            if len(lines) == 1 or len(lines[1].split()) >= 7:
                return _parse_odm(lines, defaults)
    return _parse_delimited(text, source_crs, defaults)
