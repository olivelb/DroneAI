"""QGIS-compatible raster, vector and annotation downloads."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_
from starlette.background import BackgroundTask

from shared import storage
from shared.database import AIAnalysisRun, Detection, MapFeature, get_session
from shared.geospatial_assets import detections_feature_collection
from shared.qgis_crs import (
    ExportCrsError,
    reproject_features,
    resolve_export_crs,
)
from shared.qgis_exports import write_vector_export

from ..map_support import (
    get_mission,
    mission_key,
    require_object,
    stored_map_feature_geojson,
)

router = APIRouter()

VectorFormat = Literal["gpkg", "geojson"]
VectorScope = Literal["all", "manual", "ai", "legacy"]
RasterFormat = Literal["cog", "geotiff"]

VECTOR_MEDIA_TYPES = {
    "gpkg": "application/geopackage+sqlite3",
    "geojson": "application/geo+json",
}
VECTOR_EXTENSIONS = {"gpkg": "gpkg", "geojson": "geojson"}
SCOPE_SOURCES = {
    "all": {"legacy", "manual", "ai"},
    "manual": {"manual"},
    "ai": {"ai"},
    "legacy": {"legacy"},
}


def _stream_object(body, chunk_size: int = 1024 * 1024):
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


def _legacy_features(session, mission, vol_id: str) -> Iterator[dict[str, Any]]:
    metadata = mission.tiling_metadata or {}
    records = session.query(Detection).filter(Detection.vol_id == vol_id).order_by(Detection.id).yield_per(1_000)
    batch: list[Detection] = []
    for record in records:
        batch.append(record)
        if len(batch) < 1_000:
            continue
        yield from _legacy_batch(batch, metadata, vol_id)
        batch = []
    if batch:
        yield from _legacy_batch(batch, metadata, vol_id)


def _legacy_batch(records, metadata, vol_id):
    collection = detections_feature_collection(
        records,
        geotransform=metadata.get("transform"),
        source_crs=metadata.get("crs"),
        vol_id=vol_id,
    )
    for feature in collection["features"]:
        properties = feature.setdefault("properties", {})
        properties.update(
            {
                "source": "legacy",
                "name": properties.get("class_name"),
                "description": "Détection du pipeline initial",
                "color": "#f43f5e",
            }
        )
        yield feature


def _stored_features(
    session,
    vol_id: str,
    sources: set[str],
    run_ids: set[str],
) -> Iterator[dict[str, Any]]:
    query = (
        session.query(
            MapFeature,
            func.ST_AsGeoJSON(MapFeature.geometry),
            AIAnalysisRun.run_id,
        )
        .outerjoin(AIAnalysisRun, MapFeature.analysis_run_id == AIAnalysisRun.id)
        .filter(
            MapFeature.vol_id == vol_id,
            MapFeature.source.in_(sources),
        )
    )
    if run_ids:
        query = query.filter(
            or_(
                MapFeature.source == "manual",
                AIAnalysisRun.run_id.in_(run_ids),
            )
        )
    for feature, geometry_json, run_id in query.order_by(MapFeature.id).yield_per(1_000):
        yield stored_map_feature_geojson(feature, geometry_json, run_id)


def _export_features(
    session,
    mission,
    vol_id: str,
    scope: VectorScope,
    run_ids: set[str],
) -> Iterator[dict[str, Any]]:
    sources = SCOPE_SOURCES[scope]
    if "legacy" in sources:
        yield from _legacy_features(session, mission, vol_id)
    stored_sources = sources.intersection({"manual", "ai"})
    if stored_sources:
        yield from _stored_features(session, vol_id, stored_sources, run_ids)


@router.get("/{vol_id}/export/raster/{layer}")
def export_raster(
    vol_id: str,
    layer: str,
    output_format: RasterFormat = Query(default="cog", alias="format"),
):
    key, _ = mission_key(vol_id, layer)
    require_object(key)
    body, content_length, content_type = storage.get_object_stream(key)
    suffix = "cog.tif" if output_format == "cog" else "tif"
    filename = f"{vol_id}_{layer}.{suffix}"
    return StreamingResponse(
        _stream_object(body),
        media_type=content_type or "image/tiff",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(content_length),
            "X-DroneAI-Raster-Format": output_format,
        },
    )


@router.get("/{vol_id}/export/vectors")
def export_vectors(
    vol_id: str,
    output_format: VectorFormat = Query(default="gpkg", alias="format"),
    scope: VectorScope = Query(default="all"),
    run_ids: str | None = Query(default=None),
    requested_crs: str = Query(default="raster", alias="crs", max_length=32),
):
    mission_key(vol_id, "ortho")
    requested_runs = {value.strip() for value in (run_ids or "").split(",") if value.strip()}
    suffix = VECTOR_EXTENSIONS[output_format]
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"droneai-{vol_id}-",
        suffix=f".{suffix}",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with get_session() as session:
            mission = get_mission(session, vol_id)
            try:
                resolved_crs = resolve_export_crs(
                    (mission.tiling_metadata or {}).get("crs"),
                    output_format,
                    requested_crs,
                )
            except ExportCrsError as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(error),
                ) from error
            count = write_vector_export(
                temporary_path,
                reproject_features(
                    _export_features(
                        session,
                        mission,
                        vol_id,
                        scope,
                        requested_runs,
                    ),
                    resolved_crs.label,
                ),
                output_format=output_format,
                mission_id=vol_id,
                scope=scope,
                crs=resolved_crs.geopackage_crs,
            )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    label = "annotations" if scope == "manual" else f"vectors_{scope}"
    crs_slug = resolved_crs.label.lower().replace(":", "")
    filename = f"{vol_id}_{label}_{crs_slug}.{suffix}"
    headers = {
        "X-Feature-Count": str(count),
        "X-Coordinate-Reference-System": resolved_crs.label,
    }
    if resolved_crs.used_fallback:
        headers["X-Coordinate-Reference-System-Fallback"] = "raster-crs-unavailable"
    return FileResponse(
        temporary_path,
        media_type=VECTOR_MEDIA_TYPES[output_format],
        filename=filename,
        headers=headers,
        background=BackgroundTask(temporary_path.unlink, missing_ok=True),
    )
