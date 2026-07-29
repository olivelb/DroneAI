"""Deterministic projected-CRS selection for metric aerial reconstruction."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable

EPSG_RE = re.compile(r"^EPSG:(\d{4,6})$", re.IGNORECASE)
PROJECTED_CRS_POLICIES = ("auto-local", "france-cc", "utm", "custom")

# Conservative outlines used only for automatic policy routing. Border or
# overseas missions can always select a known EPSG explicitly.
_FRANCE_MAINLAND = (
    (-5.2, 48.4),
    (-4.8, 47.8),
    (-2.2, 47.0),
    (-1.8, 43.3),
    (0.1, 42.5),
    (3.1, 42.3),
    (3.4, 43.1),
    (6.8, 43.2),
    (7.7, 44.0),
    (7.7, 48.0),
    (6.2, 49.2),
    (4.3, 49.9),
    (2.6, 51.2),
    (1.4, 50.9),
    (-1.7, 49.7),
    (-5.2, 48.4),
)
_CORSICA = (
    (8.5, 41.3),
    (9.6, 41.3),
    (9.6, 43.1),
    (8.5, 43.1),
    (8.5, 41.3),
)


@dataclass(frozen=True)
class ProjectedCrsChoice:
    crs: str
    policy: str
    source: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def normalize_epsg(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    match = EPSG_RE.fullmatch(normalized)
    if not match:
        raise ValueError("projected_crs must use the form EPSG:<code>")
    return f"EPSG:{int(match.group(1))}"


def utm_epsg(latitude: float, longitude: float) -> str:
    zone = max(1, min(60, int((longitude + 180.0) / 6.0) + 1))
    return f"EPSG:{32700 + zone if latitude < 0 else 32600 + zone}"


def france_cc_epsg(latitude: float) -> str:
    """Return the closest metropolitan RGF93 CC zone (CC42 through CC50)."""

    origin_latitude = max(42, min(50, math.floor(float(latitude) + 0.5)))
    return f"EPSG:{3900 + origin_latitude}"


def _point_in_polygon(longitude: float, latitude: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        if (current_y > latitude) != (previous_y > latitude):
            crossing_x = (
                (previous_x - current_x)
                * (latitude - current_y)
                / (previous_y - current_y)
                + current_x
            )
            if longitude < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def is_metropolitan_france(latitude: float, longitude: float) -> bool:
    return _point_in_polygon(longitude, latitude, _FRANCE_MAINLAND) or _point_in_polygon(
        longitude,
        latitude,
        _CORSICA,
    )


def _coordinates(values: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    coordinates = [(float(latitude), float(longitude)) for latitude, longitude in values]
    if not coordinates:
        raise ValueError("at least one WGS84 coordinate is required to select a projected CRS")
    for latitude, longitude in coordinates:
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("invalid WGS84 coordinate")
    return coordinates


def select_projected_crs(
    values: Iterable[tuple[float, float]],
    *,
    policy: str = "auto-local",
    custom_crs: str | None = None,
) -> ProjectedCrsChoice:
    """Choose one metric CRS for the complete mission footprint.

    The country registry is intentionally conservative: a national engineering
    CRS is selected only when the complete mission can be assigned safely.
    Otherwise the stable worldwide fallback is the centroid UTM zone.
    """

    coordinates = _coordinates(values)
    normalized_policy = str(policy or "auto-local").strip().lower()
    if normalized_policy not in PROJECTED_CRS_POLICIES:
        raise ValueError(
            f"projected_crs_mode must be one of: {', '.join(PROJECTED_CRS_POLICIES)}"
        )

    if normalized_policy == "custom":
        crs = normalize_epsg(custom_crs)
        return ProjectedCrsChoice(
            crs=crs,
            policy=normalized_policy,
            source="user",
            name="User-selected projected CRS",
        )

    centroid_latitude = mean(latitude for latitude, _ in coordinates)
    centroid_longitude = mean(longitude for _, longitude in coordinates)
    all_in_france = all(
        is_metropolitan_france(latitude, longitude)
        for latitude, longitude in coordinates
    )

    if normalized_policy == "france-cc" and not all_in_france:
        raise ValueError(
            "france-cc requires every camera position to be in metropolitan France; "
            "use custom for border or overseas missions"
        )

    if normalized_policy in {"auto-local", "france-cc"} and all_in_france:
        cc_origin = int(france_cc_epsg(centroid_latitude).split(":")[1]) - 3900
        if all(abs(latitude - cc_origin) <= 1.0 for latitude, _ in coordinates):
            return ProjectedCrsChoice(
                crs=f"EPSG:{3900 + cc_origin}",
                policy=normalized_policy,
                source="france-cc9",
                name=f"RGF93 / CC{cc_origin}",
            )
        return ProjectedCrsChoice(
            crs="EPSG:2154",
            policy=normalized_policy,
            source="france-national",
            name="RGF93 / Lambert-93",
        )

    crs = utm_epsg(centroid_latitude, centroid_longitude)
    return ProjectedCrsChoice(
        crs=crs,
        policy=normalized_policy,
        source="utm-fallback" if normalized_policy == "auto-local" else "utm",
        name=f"WGS 84 / UTM zone {crs[-2:]}{'S' if centroid_latitude < 0 else 'N'}",
    )
