"""Lifecycle and vector publication routes for rerunnable AI campaigns."""

from __future__ import annotations

from typing import Annotated, TypedDict, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.analysis_stages import cancel_analysis_stages
from shared.database import AIAnalysisRun, MapFeature, get_session

from ..analysis_support import (
    latest_analysis_stage,
    queue_analysis_stage,
    ensure_mission_accepts_new_analysis,
    get_owned_run,
    owned_run_scope,
)
from ..map_schemas import AnalysisCreate
from ..map_support import (
    AnalysisRunRecord,
    JsonObject,
    RouteSession,
    apply_spatial_filter,
    feature_collection,
    get_mission,
    map_feature_geojson,
    analysis_artifact_features,
    parse_bbox,
    resolve_raster_product,
    serialize_run,
)
from ..security import Principal, require_authenticated, require_operator
from ..stage_orchestrator import stage_jobs_enabled

router = APIRouter()


class AnalysisListResponse(TypedDict):
    runs: list[JsonObject]


@router.get("/{vol_id}/analyses")
def list_analyses(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> AnalysisListResponse:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(typed_session, vol_id, principal, owner_subject=owner_subject, action="analysis_list")
        runs = cast(
            list[AnalysisRunRecord],
            typed_session.query(AIAnalysisRun)
            .filter(AIAnalysisRun.mission_id == mission.id)
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
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    if not stage_jobs_enabled():
        raise HTTPException(status_code=503, detail="Analyses require bounded Stage Jobs")
    run_id = str(uuid4())
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="analysis_create",
            for_update=True,
        )
        ensure_mission_accepts_new_analysis(mission)
        product = resolve_raster_product(
            typed_session,
            mission,
            vol_id,
            "ortho",
        )
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
            ortho_s3_key=product.key,
            created_by=principal.subject,
        )
        run = cast(AnalysisRunRecord, run_model)
        typed_session.add(run_model)
        if product.artifact_id is None:
            raise HTTPException(status_code=404, detail="Analysis requires a raster artifact")
        queue_analysis_stage(typed_session, mission, run, product.artifact_id)
        typed_session.flush()
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_analysis(
    vol_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with owned_run_scope(vol_id, run_id, principal, owner_subject, "analysis_retry") as (typed_session, run):
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="analysis_retry",
        )
        ensure_mission_accepts_new_analysis(mission)
        if not stage_jobs_enabled():
            raise HTTPException(status_code=503, detail="Analyses require bounded Stage Jobs")
        previous = latest_analysis_stage(typed_session, run)
        if previous.status != "failed":
            raise HTTPException(
                status_code=409,
                detail="Only a failed analysis can be retried",
            )
        if len(previous.upstream_artifact_ids) != 1:
            raise HTTPException(status_code=409, detail="Analysis raster binding is invalid")
        run.retry_count += 1
        queue_analysis_stage(typed_session, mission, run, previous.upstream_artifact_ids[0])
        return serialize_run(run)


@router.post(
    "/{vol_id}/analyses/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_analysis(
    principal: Annotated[Principal, Depends(require_operator)],
    vol_id: str,
    run_id: str,
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with owned_run_scope(vol_id, run_id, principal, owner_subject, "analysis_cancel") as (typed_session, run):
        latest_analysis_stage(typed_session, run)
        if not cancel_analysis_stages(typed_session, run):
            raise HTTPException(status_code=409, detail="A completed analysis cannot be cancelled")
        return serialize_run(run)


@router.get("/{vol_id}/analyses/{run_id}/vectors.geojson")
def analysis_vectors(
    vol_id: str,
    run_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    bbox: str | None = Query(default=None),
    limit: int = Query(default=10_000, ge=1, le=50_000),
) -> JsonObject:
    bounds = parse_bbox(bbox)
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        run = get_owned_run(
            typed_session,
            vol_id,
            run_id,
            principal,
            owner_subject,
            action="analysis_vectors",
        )
        if run.persist_results:
            query = typed_session.query(MapFeature).filter(
                MapFeature.analysis_run_id == run.id,
                MapFeature.deleted_at.is_(None),
            )
            query = apply_spatial_filter(query, MapFeature.geometry, bounds)
            records = cast(
                list[MapFeature],
                query.order_by(MapFeature.id).limit(limit + 1).all(),
            )
            features = [map_feature_geojson(typed_session, item) for item in records[:limit]]
            return feature_collection(
                features,
                run_id=run_id,
                truncated=len(records) > limit,
            )
        mission = get_mission(typed_session, vol_id, principal, owner_subject=owner_subject)
        features, truncated = analysis_artifact_features(
            typed_session, mission, run, bounds, limit,
        )
    return feature_collection(
        features,
        run_id=run_id,
        persisted=False,
        truncated=truncated,
    )
