"""CRS resolution and streaming geometry reprojection for QGIS exports."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from rasterio.crs import CRS
from rasterio.errors import CRSError
from rasterio.warp import transform_geom

from .qgis_exports import GeoPackageCrs, WGS84_CRS

EPSG_PATTERN = re.compile(r"^EPSG:(\d{4,6})$", re.IGNORECASE)


class ExportCrsError(ValueError):
    """Raised when an export CRS request cannot be honored safely."""


@dataclass(frozen=True)
class ResolvedExportCrs:
    geopackage_crs: GeoPackageCrs
    label: str
    used_fallback: bool = False


def _crs_from_epsg(value: str) -> ResolvedExportCrs:
    match = EPSG_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ExportCrsError("crs must use the form EPSG:<code> or the value raster")
    epsg = int(match.group(1))
    try:
        raster_crs = CRS.from_epsg(epsg)
    except CRSError as error:
        raise ExportCrsError(f"Unknown coordinate reference system EPSG:{epsg}") from error
    label = f"EPSG:{epsg}"
    return ResolvedExportCrs(
        geopackage_crs=GeoPackageCrs(
            srs_id=epsg,
            name=label,
            definition=raster_crs.to_wkt(),
        ),
        label=label,
    )


def resolve_export_crs(
    raster_crs_value: str | None,
    output_format: str,
    requested_crs: str,
) -> ResolvedExportCrs:
    """Resolve a standards-safe vector output CRS.

    RFC 7946 GeoJSON is always WGS84. GeoPackage accepts either the raster
    authority, WGS84, or an explicit EPSG authority code.
    """

    normalized = requested_crs.strip()
    if output_format == "geojson":
        if normalized.lower() not in {"raster", "epsg:4326"}:
            raise ExportCrsError("RFC 7946 GeoJSON exports use EPSG:4326; choose GeoPackage for another CRS")
        return ResolvedExportCrs(WGS84_CRS, "EPSG:4326")

    if normalized.lower() != "raster":
        return _crs_from_epsg(normalized)

    source_crs = str(raster_crs_value or "").strip()
    if not source_crs or source_crs.lower() == "unknown":
        return ResolvedExportCrs(
            WGS84_CRS,
            "EPSG:4326",
            used_fallback=True,
        )
    try:
        epsg = CRS.from_user_input(source_crs).to_epsg()
    except CRSError:
        epsg = None
    if epsg is None:
        return ResolvedExportCrs(
            WGS84_CRS,
            "EPSG:4326",
            used_fallback=True,
        )
    return _crs_from_epsg(f"EPSG:{epsg}")


def reproject_features(
    features: Iterable[dict[str, Any]],
    target_crs: str,
) -> Iterator[dict[str, Any]]:
    """Lazily reproject WGS84 GeoJSON features without materializing a layer."""

    if target_crs == "EPSG:4326":
        yield from features
        return
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry:
            yield feature
            continue
        yield {
            **feature,
            "geometry": transform_geom(
                "EPSG:4326",
                target_crs,
                geometry,
                precision=-1,
            ),
        }
