"""Manual feature CRUD and database-backed geospatial search."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Text, func, or_

from shared.database import (
    AIAnalysisRun,
    Detection,
    MapFeature,
    get_session,
)
from shared.geospatial_assets import detections_feature_collection
from shared.geospatial_workspace import geometry_bounds

from ..map_schemas import MapFeatureCreate, MapFeatureUpdate
from ..map_support import (
    apply_detection_spatial_filter,
    apply_spatial_filter,
    feature_collection,
    get_mission,
    map_feature_geojson,
    parse_bbox,
)
from ..security import Principal, require_operator

router = APIRouter()


@router.post("/{vol_id}/features", status_code=status.HTTP_201_CREATED)
def create_map_feature(
    vol_id: str,
    request: MapFeatureCreate,
    principal: Annotated[Principal, Depends(require_operator)],
):
    with get_session() as session:
        mission = get_mission(session, vol_id)
        feature = MapFeature(
            mission_id=mission.id,
            vol_id=vol_id,
            source="manual",
            geometry=func.ST_SetSRID(
                func.ST_GeomFromGeoJSON(json.dumps(request.geometry)),
                4326,
            ),
            name=request.name.strip(),
            description=request.description.strip(),
            color=request.color,
            tags=request.tags,
            properties=request.properties,
            created_by=principal.subject,
        )
        session.add(feature)
        session.flush()
        return map_feature_geojson(session, feature)


@router.patch("/{vol_id}/features/{feature_id}")
def update_map_feature(
    vol_id: str,
    feature_id: str,
    request: MapFeatureUpdate,
    _principal: Annotated[Principal, Depends(require_operator)],
):
    with get_session() as session:
        feature = (
            session.query(MapFeature)
            .filter(
                MapFeature.vol_id == vol_id,
                MapFeature.feature_id == feature_id,
                MapFeature.source == "manual",
            )
            .with_for_update()
            .first()
        )
        if feature is None:
            raise HTTPException(status_code=404, detail="Feature not found")
        if feature.version != request.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Feature was changed by another user",
                    "current_version": feature.version,
                },
            )
        changes = request.model_dump(exclude_unset=True)
        changes.pop("version", None)
        if "geometry" in changes:
            feature.geometry = func.ST_SetSRID(
                func.ST_GeomFromGeoJSON(json.dumps(changes.pop("geometry"))),
                4326,
            )
        for field, value in changes.items():
            setattr(
                feature,
                field,
                value.strip() if isinstance(value, str) else value,
            )
        feature.version += 1
        feature.updated_at = datetime.now(timezone.utc)
        session.flush()
        return map_feature_geojson(session, feature)


@router.delete(
    "/{vol_id}/features/{feature_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_map_feature(
    vol_id: str,
    feature_id: str,
    _principal: Annotated[Principal, Depends(require_operator)],
):
    with get_session() as session:
        feature = (
            session.query(MapFeature)
            .filter(
                MapFeature.vol_id == vol_id,
                MapFeature.feature_id == feature_id,
                MapFeature.source == "manual",
            )
            .first()
        )
        if feature is None:
            raise HTTPException(status_code=404, detail="Feature not found")
        session.delete(feature)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _search_map_records(
    session,
    *,
    vol_id,
    text,
    source,
    run_id,
    class_name,
    min_confidence,
    bounds,
    limit,
):
    if source == "legacy":
        return [], False
    query = session.query(MapFeature).filter(MapFeature.vol_id == vol_id)
    if text:
        pattern = f"%{text}%"
        query = query.filter(
            or_(
                MapFeature.name.ilike(pattern),
                MapFeature.description.ilike(pattern),
                MapFeature.class_name.ilike(pattern),
                func.cast(MapFeature.tags, Text).ilike(pattern),
            )
        )
    if source:
        query = query.filter(MapFeature.source == source)
    if run_id:
        query = query.join(AIAnalysisRun).filter(
            AIAnalysisRun.run_id == run_id
        )
    if class_name:
        query = query.filter(MapFeature.class_name == class_name)
    if min_confidence is not None:
        query = query.filter(MapFeature.confidence >= min_confidence)
    query = apply_spatial_filter(query, MapFeature.geometry, bounds)
    records = query.order_by(MapFeature.updated_at.desc()).limit(limit + 1).all()
    return records[:limit], len(records) > limit


def _search_legacy_records(
    session,
    *,
    vol_id,
    text,
    class_name,
    min_confidence,
    bounds,
    limit,
):
    query = session.query(Detection).filter(Detection.vol_id == vol_id)
    if text:
        query = query.filter(Detection.class_name.ilike(f"%{text}%"))
    if class_name:
        query = query.filter(Detection.class_name == class_name)
    if min_confidence is not None:
        query = query.filter(Detection.confidence >= min_confidence)
    query = apply_detection_spatial_filter(query, bounds)
    records = query.order_by(Detection.id.desc()).limit(limit + 1).all()
    return records[:limit], len(records) > limit


def _legacy_geojson(records, mission, vol_id):
    metadata = mission.tiling_metadata or {}
    collection = detections_feature_collection(
        records,
        geotransform=metadata.get("transform"),
        source_crs=metadata.get("crs"),
        vol_id=vol_id,
    )
    for feature in collection["features"]:
        properties = feature["properties"]
        properties.update(
            {
                "source": "legacy",
                "name": properties.get("class_name"),
                "description": "Détection du pipeline initial",
                "color": "#f43f5e",
            }
        )
    return collection["features"]


def _aggregate_bounds(features: list[dict[str, Any]]):
    if not features:
        return None
    bounds = [geometry_bounds(feature["geometry"]) for feature in features]
    return [
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    ]


@router.get("/{vol_id}/search")
def search_map_features(
    vol_id: str,
    q: str = Query(default="", max_length=160),
    source: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    class_name: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    bbox: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    bounds = parse_bbox(bbox)
    text = q.strip()
    features: list[dict[str, Any]] = []
    with get_session() as session:
        mission = get_mission(session, vol_id)
        records, truncated = _search_map_records(
            session,
            vol_id=vol_id,
            text=text,
            source=source,
            run_id=run_id,
            class_name=class_name,
            min_confidence=min_confidence,
            bounds=bounds,
            limit=limit,
        )
        features.extend(map_feature_geojson(session, item) for item in records)
        remaining = max(0, limit - len(features))
        if remaining and source in {None, "", "legacy"} and not run_id:
            legacy, legacy_truncated = _search_legacy_records(
                session,
                vol_id=vol_id,
                text=text,
                class_name=class_name,
                min_confidence=min_confidence,
                bounds=bounds,
                limit=remaining,
            )
            features.extend(_legacy_geojson(legacy, mission, vol_id))
            truncated = truncated or legacy_truncated
    return {
        **feature_collection(
            features,
            vol_id=vol_id,
            truncated=truncated,
        ),
        "bounds": _aggregate_bounds(features),
    }
