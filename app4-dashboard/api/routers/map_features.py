"""Database-backed geospatial feature search."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Text, func, or_

from shared.database import AIAnalysisRun, Detection, MapFeature, get_session
from shared.geospatial_assets import detections_feature_collection
from shared.geospatial_workspace import geometry_bounds

from ..map_support import (
    Bounds,
    JsonObject,
    MissionRecord,
    RouteSession,
    apply_detection_spatial_filter,
    apply_spatial_filter,
    feature_collection,
    get_mission,
    map_feature_geojson,
    parse_bbox,
    pipeline_detection_features,
)
from ..security import Principal, require_authenticated

router = APIRouter()


def _search_map_records(
    session: RouteSession,
    *,
    vol_id: str,
    text: str,
    source: str | None,
    run_id: str | None,
    class_name: str | None,
    min_confidence: float | None,
    bounds: Bounds | None,
    reviewed: bool | None,
    deleted: bool,
    limit: int,
) -> tuple[list[MapFeature], bool]:
    if source == "legacy":
        return [], False
    query = session.query(MapFeature).filter(MapFeature.vol_id == vol_id)
    query = query.filter(
        MapFeature.deleted_at.is_not(None)
        if deleted
        else MapFeature.deleted_at.is_(None)
    )
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
        query = query.join(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id)
    if class_name:
        query = query.filter(MapFeature.class_name == class_name)
    if min_confidence is not None:
        query = query.filter(MapFeature.confidence >= min_confidence)
    if reviewed is not None:
        query = query.filter(
            MapFeature.reviewed_at.is_not(None)
            if reviewed
            else MapFeature.reviewed_at.is_(None)
        )
    query = apply_spatial_filter(query, MapFeature.geometry, bounds)
    records = cast(
        list[MapFeature],
        query.order_by(MapFeature.updated_at.desc()).limit(limit + 1).all(),
    )
    return records[:limit], len(records) > limit


def _search_legacy_records(
    session: RouteSession,
    *,
    vol_id: str,
    text: str,
    class_name: str | None,
    min_confidence: float | None,
    bounds: Bounds | None,
    limit: int,
) -> tuple[list[Detection], bool]:
    query = session.query(Detection).filter(Detection.vol_id == vol_id)
    if text:
        query = query.filter(Detection.class_name.ilike(f"%{text}%"))
    if class_name:
        query = query.filter(Detection.class_name == class_name)
    if min_confidence is not None:
        query = query.filter(Detection.confidence >= min_confidence)
    query = apply_detection_spatial_filter(query, bounds)
    records = cast(
        list[Detection],
        query.order_by(Detection.id.desc()).limit(limit + 1).all(),
    )
    return records[:limit], len(records) > limit


def _legacy_geojson(
    records: list[Detection],
    mission: MissionRecord,
    vol_id: str,
) -> list[JsonObject]:
    metadata = mission.tiling_metadata or {}
    collection = detections_feature_collection(
        records,
        geotransform=metadata.get("transform"),
        source_crs=metadata.get("crs"),
        vol_id=vol_id,
    )
    features = cast(list[JsonObject], collection["features"])
    for feature in features:
        properties = cast(JsonObject, feature["properties"])
        properties.update(
            {
                "source": "legacy",
                "name": properties.get("class_name"),
                "description": "Initial pipeline detection",
                "color": "#f43f5e",
            }
        )
    return features


def _search_pipeline_artifact(
    session: RouteSession,
    mission: MissionRecord,
    *,
    vol_id: str,
    text: str,
    class_name: str | None,
    min_confidence: float | None,
    bounds: Bounds | None,
    reviewed: bool | None,
    deleted: bool,
    limit: int,
) -> tuple[list[JsonObject], bool] | None:
    immutable = pipeline_detection_features(
        session,
        mission,
        vol_id,
        bounds,
        50_000,
    )
    if immutable is None:
        return None
    if deleted or reviewed is True:
        return [], False
    needle = text.casefold()
    filtered: list[JsonObject] = []
    for feature in immutable[0]:
        properties = cast(JsonObject, feature.get("properties") or {})
        feature_class = str(properties.get("class_name") or "")
        if class_name and feature_class != class_name:
            continue
        confidence = properties.get("confidence")
        if min_confidence is not None and (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or float(confidence) < min_confidence
        ):
            continue
        if needle:
            raw_tags = properties.get("tags")
            tags = raw_tags if isinstance(raw_tags, list) else []
            searchable = " ".join(
                [
                    feature_class,
                    str(properties.get("name") or ""),
                    str(properties.get("description") or ""),
                    " ".join(str(tag) for tag in tags),
                ]
            ).casefold()
            if needle not in searchable:
                continue
        filtered.append(feature)
        if len(filtered) > limit:
            break
    return filtered[:limit], len(filtered) > limit or immutable[1]


def _aggregate_bounds(features: list[JsonObject]) -> list[float] | None:
    if not features:
        return None
    bounds = [
        geometry_bounds(cast(JsonObject, feature["geometry"]))
        for feature in features
    ]
    return [
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    ]


@router.get("/{vol_id}/search")
def search_map_features(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    q: Annotated[str, Query(max_length=160)] = "",
    source: Annotated[str | None, Query()] = None,
    run_id: Annotated[str | None, Query()] = None,
    class_name: Annotated[str | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
    reviewed: Annotated[bool | None, Query()] = None,
    deleted: Annotated[bool, Query()] = False,
    bbox: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> JsonObject:
    bounds = parse_bbox(bbox)
    text = q.strip()
    features: list[JsonObject] = []
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session, vol_id, principal,
            owner_subject=owner_subject, action="feature_search",
        )
        records, truncated = _search_map_records(
            typed_session,
            vol_id=vol_id,
            text=text,
            source=source,
            run_id=run_id,
            class_name=class_name,
            min_confidence=min_confidence,
            bounds=bounds,
            reviewed=reviewed,
            deleted=deleted,
            limit=limit,
        )
        features.extend(map_feature_geojson(typed_session, item) for item in records)
        remaining = max(0, limit - len(features))
        if remaining and source in {None, "", "legacy"} and not run_id:
            immutable = _search_pipeline_artifact(
                typed_session,
                mission,
                vol_id=vol_id,
                text=text,
                class_name=class_name,
                min_confidence=min_confidence,
                bounds=bounds,
                reviewed=reviewed,
                deleted=deleted,
                limit=remaining,
            )
            if immutable is not None:
                features.extend(immutable[0])
                truncated = truncated or immutable[1]
            else:
                legacy, legacy_truncated = _search_legacy_records(
                    typed_session,
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
        **feature_collection(features, vol_id=vol_id, truncated=truncated),
        "bounds": _aggregate_bounds(features),
    }
