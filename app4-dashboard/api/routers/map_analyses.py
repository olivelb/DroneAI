"""Lifecycle and vector publication routes for rerunnable AI campaigns."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, TypedDict, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.cancellation import mark_cancellation_requested
from shared.config import TOPIC_CONTROL, TOPIC_ORTHO
from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    MapFeature,
    get_session,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.inbox_outbox import enqueue_outbox

from ..map_schemas import AnalysisCreate
from ..map_support import (
    AnalysisRunRecord,
    AnalysisTileRecord,
    JsonObject,
    RouteSession,
    apply_spatial_filter,
    feature_collection,
    get_mission,
    map_feature_geojson,
    mission_key,
    object_store_analysis_features as _object_store_features,
    parse_bbox,
    require_object,
    serialize_run,
)
from ..security import Principal, require_operator

router = APIRouter()


class AnalysisListResponse(TypedDict):
    runs: list[JsonObject]


def _analysis_event(run: AnalysisRunRecord) -> JsonObject:
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


def _get_run(
    session: RouteSession,
    vol_id: str,
    run_id: str,
    *,
    lock: bool = False,
) -> AnalysisRunRecord:
    query = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.vol_id == vol_id,
        AIAnalysisRun.run_id == run_id,
    )
    if lock:
        query = query.with_for_update()
    run = cast(AnalysisRunRecord | None, query.first())
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return run


@router.get("/{vol_id}/analyses")
def list_analyses(vol_id: str) -> AnalysisListResponse:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        get_mission(typed_session, vol_id)
        runs = cast(
            list[AnalysisRunRecord],
            typed_session.query(AIAnalysisRun)
            .filter(AIAnalysisRun.vol_id == vol_id)
            .order_by(AIAnalysisRun.created_at.desc())
            .all(),
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
) -> JsonObject:
    key, _ = mission_key(vol_id, "ortho")
    require_object(key)
    run_id = str(uuid4())
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(typed_session, vol_id)
        run_model = AIAnalysisRun(
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
        run = cast(AnalysisRunRecord, run_model)
        typed_session.add(run_model)
        event = make_event(
            "orthomosaic",
            _analysis_event(run),
            event_id=deterministic_event_id(
                "orthomosaic", vol_id, run_id, 0
            ),
            correlation_id=run_id,
        )
        enqueue_outbox(typed_session, topic=TOPIC_ORTHO, event=event, key=vol_id)
        typed_session.flush()
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_analysis(
    vol_id: str,
    run_id: str,
    _principal: Annotated[Principal, Depends(require_operator)],
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        run = _get_run(typed_session, vol_id, run_id, lock=True)
        if run.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="Only a failed analysis can be retried",
            )
        run.retry_count += 1
        run.status = "queued"
        run.phase = "recovery_queued"
        run.error_message = None
        run.heartbeat_at = datetime.now(UTC)
        (
            typed_session.query(AIAnalysisTile)
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
        enqueue_outbox(typed_session, topic=TOPIC_ORTHO, event=event, key=vol_id)
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_analysis(
    vol_id: str,
    run_id: str,
    _principal: Annotated[Principal, Depends(require_operator)],
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        run = _get_run(typed_session, vol_id, run_id, lock=True)
        if run.status == "completed":
            raise HTTPException(
                status_code=409,
                detail="A completed analysis cannot be cancelled",
            )
        if not mark_cancellation_requested(
            typed_session,
            vol_id=vol_id,
            run_id=run_id,
            attempt=run.retry_count,
        ):
            raise HTTPException(
                status_code=409,
                detail="Analysis generation changed before cancellation",
            )
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
        enqueue_outbox(typed_session, topic=TOPIC_CONTROL, event=event, key=vol_id)
        return serialize_run(run)


@router.get("/{vol_id}/analyses/{run_id}/vectors.geojson")
def analysis_vectors(
    vol_id: str,
    run_id: str,
    bbox: str | None = Query(default=None),
    limit: int = Query(default=10_000, ge=1, le=50_000),
) -> JsonObject:
    bounds = parse_bbox(bbox)
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        run = _get_run(typed_session, vol_id, run_id)
        if run.persist_results:
            query = typed_session.query(MapFeature).filter(
                MapFeature.analysis_run_id == run.id
            )
            query = apply_spatial_filter(
                query, MapFeature.geometry, bounds
            )
            records = cast(
                list[MapFeature],
                query.order_by(MapFeature.id).limit(limit + 1).all(),
            )
            features = [
                map_feature_geojson(typed_session, item)
                for item in records[:limit]
            ]
            return feature_collection(
                features,
                run_id=run_id,
                truncated=len(records) > limit,
            )
        tiles = cast(
            list[AnalysisTileRecord],
            typed_session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.status == "completed",
                AIAnalysisTile.result_s3_key.isnot(None),
            )
            .order_by(AIAnalysisTile.tile_index)
            .all(),
        )
        features, truncated = _object_store_features(
            tiles,
            bounds,
            limit,
            vol_id=run.vol_id,
            tiling_metadata=run.tiling_metadata or {},
        )
    return feature_collection(
        features,
        run_id=run_id,
        persisted=False,
        truncated=truncated,
    )
