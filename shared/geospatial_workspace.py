"""Validation helpers for editable GeoJSON and AI analysis presentation."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
ALLOWED_GEOMETRIES = {
    "Point",
    "LineString",
    "Polygon",
    "MultiPoint",
    "MultiLineString",
    "MultiPolygon",
}
MAX_COORDINATES = 50_000


def normalize_color(value: str) -> str:
    color = str(value or "").strip()
    if not COLOR_PATTERN.fullmatch(color):
        raise ValueError("color must be #RRGGBB or #RRGGBBAA")
    return color.lower()


def normalize_tags(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if value and value not in result:
            result.append(value[:64])
        if len(result) >= 30:
            break
    return result


def _coordinate_pairs(value: Any) -> list[list[float]]:
    pairs: list[list[float]] = []

    def visit(node: Any) -> None:
        if len(pairs) > MAX_COORDINATES:
            raise ValueError("geometry has too many coordinates")
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and all(isinstance(item, (int, float)) for item in node[:2])
        ):
            longitude, latitude = float(node[0]), float(node[1])
            if (
                not math.isfinite(longitude)
                or not math.isfinite(latitude)
                or longitude < -180
                or longitude > 180
                or latitude < -90
                or latitude > 90
            ):
                raise ValueError("geometry coordinates must be valid WGS84")
            pairs.append([longitude, latitude])
            return
        if not isinstance(node, (list, tuple)):
            raise ValueError("invalid GeoJSON coordinate structure")
        for child in node:
            visit(child)

    visit(value)
    return pairs


def validate_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(geometry, dict):
        raise ValueError("geometry must be a GeoJSON object")
    geometry_type = geometry.get("type")
    if geometry_type not in ALLOWED_GEOMETRIES:
        raise ValueError(f"unsupported geometry type: {geometry_type}")
    coordinates = geometry.get("coordinates")
    pairs = _coordinate_pairs(coordinates)
    minimum = 1 if geometry_type in {"Point", "MultiPoint"} else 2
    if "Polygon" in geometry_type:
        minimum = 4
    if len(pairs) < minimum:
        raise ValueError(f"{geometry_type} does not have enough coordinates")
    return {"type": geometry_type, "coordinates": coordinates}


def geometry_bounds(geometry: dict[str, Any]) -> list[float]:
    pairs = _coordinate_pairs(geometry.get("coordinates"))
    return [
        min(pair[0] for pair in pairs),
        min(pair[1] for pair in pairs),
        max(pair[0] for pair in pairs),
        max(pair[1] for pair in pairs),
    ]


def bounds_intersect(first: list[float], second: list[float]) -> bool:
    return not (first[2] < second[0] or first[0] > second[2] or first[3] < second[1] or first[1] > second[3])
