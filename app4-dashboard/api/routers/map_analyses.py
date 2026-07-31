"""Lifecycle and vector publication routes for rerunnable AI campaigns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.config import TOPIC_CONTROL, TOPIC_ORTHO
from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    MapFeature,
    get_session,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.geospatial_workspace import bounds_intersect, geometry_bounds
from shared.inbox_outbox import enqueue_outbox

from ..map_schemas import AnalysisCreate
from ..map_support import (
    apply_spatial_filter,
    feature_collection,
    get_mission,
    load_json_object,
    map_feature_geojson,
    mission_key,
    parse_bbox,
    require_object,
    serialize_run,
)
from ..security import Principal, require_operator

router = APIRouter()


def _analysis_event(run: AIAnalysisRun):
    return {
        "vol_id": run.vol_id,
        "ortho_s3_key": run.ortho_s3_key,
        "analysis_run_id": run.run_id,
        "classes": run.classes or [],
        "ai_confidence": run.confidence,
        "ai_backend": run.backend,
        "ai_model_variant": run.model_variant,
        "sam_prompt": run.prompt,
        "tile_size": run.tile_size,
    }


def _get_run(session, vol_id: str, run_id: str, *, lock=False):
    query = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.vol_id == vol_id,
        AIAnalysisRun.run_id == run_id,
    )
    if lock:
        query = query.with_for_update()
    run = query.first()
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return run


@router.get("/{vol_id}/analyses")
def list_analyses(vol_id: str):
    with get_session() as session:
        get_mission(session, vol_id)
        runs = (
            session.query(AIAnalysisRun)
            .filter(AIAnalysisRun.vol_id == vol_id)
            .order_by(AIAnalysisRun.created_at.desc())
            .all()
        )
        return {"runs": [serialize_run(run) for run in runs]}


@router.post(
    "/{vol_id}/analyses",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_analysis(
    vol_id: str,
    request: AnalysisCreate,
    principal: Annotated[Principal, Depends(require_operator)],
):
    key, _ = mission_key(vol_id, "ortho")
    require_object(key)
    run_id = str(uuid4())
    with get_session() as session:
        mission = get_mission(session, vol_id)
        run = AIAnalysisRun(
            run_id=run_id,
            mission_id=mission.id,
            vol_id=vol_id,
            name=request.name.strip(),
            description=request.description.strip(),
            color=request.color,
            tags=request.tags,
            backend=request.backend,
            model_variant=request.model_variant,
            prompt=request.prompt,
            classes=request.classes,
            confidence=request.confidence,
            tile_size=request.tile_size,
            persist_results=request.persist_results,
            ortho_s3_key=key,
            created_by=principal.subject,
        )
        session.add(run)
        event = make_event(
            "orthomosaic",
            _analysis_event(run),
            event_id=deterministic_event_id(
                "orthomosaic", vol_id, run_id, 0
            ),
            correlation_id=run_id,
        )
        enqueue_outbox(session, topic=TOPIC_ORTHO, event=event, key=vol_id)
        session.flush()
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_analysis(
    vol_id: str,
    run_id: str,
    _principal: Annotated[Principal, Depends(require_operator)],
):
    with get_session() as session:
        run = _get_run(session, vol_id, run_id, lock=True)
        if run.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="Only a failed analysis can be retried",
            )
        run.retry_count += 1
        run.status = "queued"
        run.phase = "recovery_queued"
        run.error_message = None
        run.heartbeat_at = datetime.now(timezone.utc)
        (
            session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.status != "completed",
            )
            .update(
                {
                    AIAnalysisTile.status: "queued",
                    AIAnalysisTile.attempts: 0,
                    AIAnalysisTile.last_error: None,
                },
                synchronize_session=False,
            )
        )
        event = make_event(
            "orthomosaic",
            _analysis_event(run),
            event_id=deterministic_event_id(
                "orthomosaic", vol_id, run_id, run.retry_count
            ),
            correlation_id=run_id,
            attempt=run.retry_count,
        )
        enqueue_outbox(session, topic=TOPIC_ORTHO, event=event, key=vol_id)
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_analysis(
    vol_id: str,
    run_id: str,
    _principal: Annotated[Principal, Depends(require_operator)],
):
    with get_session() as session:
        run = _get_run(session, vol_id, run_id, lock=True)
        if run.status == "completed":
            raise HTTPException(
                status_code=409,
                detail="A completed analysis cannot be cancelled",
            )
        run.status = "cancelled"
        run.phase = "cancelled"
        run.heartbeat_at = datetime.now(timezone.utc)
        event = make_event(
            "control",
            {
                "vol_id": vol_id,
                "command": "cancel",
                "analysis_run_id": run_id,
            },
            event_id=deterministic_event_id(
                "control", vol_id, "cancel", run_id, run.retry_count
            ),
            correlation_id=run_id,
            attempt=run.retry_count,
        )
        enqueue_outbox(session, topic=TOPIC_CONTROL, event=event, key=vol_id)
        return serialize_run(run)


def _object_store_features(tiles, bounds, limit):
    features = []
    truncated = False
    for tile in tiles:
        if (
            bounds
            and tile.bounds_wgs84
            and not bounds_intersect(list(bounds), tile.bounds_wgs84)
        ):
            continue
        payload = load_json_object(tile.result_s3_key)
        for feature in payload.get("features", []):
            if bounds and not bounds_intersect(
                list(bounds), geometry_bounds(feature["geometry"])
            ):
                continue
            features.append(feature)
            if len(features) >= limit:
                truncated = True
                break
        if truncated:
            break
    return features, truncated


@router.get("/{vol_id}/analyses/{run_id}/vectors.geojson")
def analysis_vectors(
    vol_id: str,
    run_id: str,
    bbox: str | None = Query(default=None),
    limit: int = Query(default=10_000, ge=1, le=50_000),
):
    bounds = parse_bbox(bbox)
    with get_session() as session:
        run = _get_run(session, vol_id, run_id)
        if run.persist_results:
            query = session.query(MapFeature).filter(
                MapFeature.analysis_run_id == run.id
            )
            query = apply_spatial_filter(
                query, MapFeature.geometry, bounds
            )
            records = query.order_by(MapFeature.id).limit(limit + 1).all()
            features = [
                map_feature_geojson(session, item)
                for item in records[:limit]
            ]
            return feature_collection(
                features,
                run_id=run_id,
                truncated=len(records) > limit,
            )
        tiles = (
            session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.status == "completed",
                AIAnalysisTile.result_s3_key.isnot(None),
            )
            .order_by(AIAnalysisTile.tile_index)
            .all()
        )
        features, truncated = _object_store_features(
            tiles, bounds, limit
        )
    return feature_collection(
        features,
        run_id=run_id,
        persisted=False,
        truncated=truncated,
    )
