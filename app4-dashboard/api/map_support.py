"""Shared query and serialization helpers for map routers."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select

from shared import storage
from shared.database import AIAnalysisRun, Detection, MapFeature, Mission

VOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
RASTER_LAYERS = {
    "ortho": ("orthomosaic.tif", ""),
    "depth": ("orthomosaic.height.tif", "depth"),
}
MAX_VECTOR_OBJECT_BYTES = 20_000_000


def mission_key(vol_id: str, layer: str) -> tuple[str, str]:
    if not VOL_ID_PATTERN.fullmatch(vol_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid mission identifier",
        )
    try:
        suffix, colormap = RASTER_LAYERS[layer]
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown raster layer",
        ) from error
    return f"missions/{vol_id}/{suffix}", colormap


def require_object(key: str) -> None:
    if not storage.file_exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Map product is not available",
        )


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    try:
        bounds = tuple(float(item) for item in value.split(","))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bbox must contain west,south,east,north",
        ) from error
    if (
        len(bounds) != 4
        or bounds[0] >= bounds[2]
        or bounds[1] >= bounds[3]
        or bounds[0] < -180
        or bounds[2] > 180
        or bounds[1] < -90
        or bounds[3] > 90
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid WGS84 bbox",
        )
    return bounds


def get_mission(session, vol_id: str) -> Mission:
    mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
    if mission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mission not found",
        )
    return mission


def serialize_run(run: AIAnalysisRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "vol_id": run.vol_id,
        "name": run.name,
        "description": run.description or "",
        "color": run.color,
        "tags": run.tags or [],
        "backend": run.backend,
        "model_variant": run.model_variant,
        "prompt": run.prompt,
        "classes": run.classes or [],
        "confidence": run.confidence,
        "tile_size": run.tile_size,
        "persist_results": bool(run.persist_results),
        "status": run.status,
        "phase": run.phase,
        "progress": run.progress,
        "total_tiles": run.total_tiles,
        "tiles_completed": run.tiles_completed,
        "detection_count": run.detection_count,
        "retry_count": run.retry_count,
        "error_message": run.error_message,
        "result_s3_key": run.result_s3_key,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def stored_map_feature_geojson(
    feature: MapFeature,
    geometry_json: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one stored feature consistently across API and exports."""

    return {
        "type": "Feature",
        "id": feature.feature_id,
        "geometry": json.loads(geometry_json),
        "properties": {
            **(feature.properties or {}),
            "feature_id": feature.feature_id,
            "source": feature.source,
            "run_id": run_id,
            "name": feature.name,
            "description": feature.description or "",
            "color": feature.color,
            "tags": feature.tags or [],
            "class_name": feature.class_name,
            "confidence": feature.confidence,
            "version": feature.version,
            "created_by": feature.created_by,
            "updated_at": (
                feature.updated_at.isoformat() if feature.updated_at else None
            ),
        },
    }


def map_feature_geojson(session, feature: MapFeature) -> dict[str, Any]:
    geometry_json = session.scalar(
        select(func.ST_AsGeoJSON(MapFeature.geometry)).where(
            MapFeature.id == feature.id
        )
    )
    run_id = (
        feature.analysis_run.run_id
        if feature.analysis_run is not None
        else None
    )
    return stored_map_feature_geojson(feature, geometry_json, run_id)


def feature_collection(features: list[dict[str, Any]], **properties):
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "feature_count": len(features),
            **properties,
        },
    }


def apply_spatial_filter(query, geometry_column, bounds):
    if not bounds:
        return query
    west, south, east, north = bounds
    return query.filter(
        func.ST_Intersects(
            geometry_column,
            func.ST_MakeEnvelope(west, south, east, north, 4326),
        )
    )


def apply_detection_spatial_filter(query, bounds):
    """Filter legacy detections stored as geometry or fallback GPS columns."""

    if not bounds:
        return query
    west, south, east, north = bounds
    envelope = func.ST_MakeEnvelope(west, south, east, north, 4326)
    return query.filter(
        or_(
            func.ST_Intersects(Detection.geometry, envelope),
            (
                (Detection.geo_lon >= west)
                & (Detection.geo_lon <= east)
                & (Detection.geo_lat >= south)
                & (Detection.geo_lat <= north)
            ),
        )
    )


def load_json_object(key: str) -> dict[str, Any]:
    stream, content_length, _ = storage.get_object_stream(key)
    if content_length > MAX_VECTOR_OBJECT_BYTES:
        stream.close()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Vector tile is too large",
        )
    try:
        return json.loads(stream.read())
    finally:
        stream.close()
