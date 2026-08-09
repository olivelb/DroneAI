"""Owner-scoped mission catalogue and durable detail projection."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict, cast

from fastapi import APIRouter, Depends, Query

from shared.database import AIAnalysisRun, Mission, MissionLog, get_session

from ..mission_access import get_owned_mission, mission_query
from ..mission_state import serialize_mission
from ..security import Principal, require_authenticated

router = APIRouter()


class MissionCatalogResponse(TypedDict):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


def _catalog_item(mission: Mission) -> dict[str, Any]:
    serialized = serialize_mission(cast(Any, mission))
    parameters = cast(dict[str, Any], mission.params or {})
    return {
        "vol_id": mission.vol_id,
        "owner_subject": mission.owner_subject,
        "status": mission.status,
        "current_step": mission.current_step,
        "progress": mission.progress,
        "pipeline": mission.pipeline,
        "quality_profile": parameters.get("quality_profile"),
        "attempt_count": int(mission.retry_count or 0) + 1,
        "created_at": mission.created_at.isoformat() if mission.created_at else None,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
        "overall_status": serialized["overall_status"],
        "is_stale": serialized["is_stale"],
        "last_event_age_seconds": serialized["last_event_age_seconds"],
    }


def _mission_detail(session: Any, mission: Mission) -> dict[str, Any]:
    logs = (
        session.query(MissionLog)
        .filter(MissionLog.mission_id == mission.id)
        .order_by(MissionLog.created_at.desc())
        .limit(200)
        .all()
    )
    analyses = (
        session.query(AIAnalysisRun)
        .filter(AIAnalysisRun.mission_id == mission.id)
        .order_by(AIAnalysisRun.created_at.desc())
        .all()
    )
    products: list[dict[str, Any]] = []
    if mission.ortho_s3_key:
        products.append({"kind": "orthomosaic", "s3_key": mission.ortho_s3_key})
    products.extend(
        {
            "kind": "analysis",
            "run_id": analysis.run_id,
            "name": analysis.name,
            "status": analysis.status,
            "s3_key": analysis.result_s3_key,
        }
        for analysis in analyses
    )
    snapshot = serialize_mission(cast(Any, mission))
    return {
        **_catalog_item(mission),
        "parameters": mission.params or {},
        "attempts": [
            {
                "attempt": int(mission.retry_count or 0),
                "status": mission.status,
                "started_at": mission.created_at.isoformat() if mission.created_at else None,
                "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
            }
        ],
        "phases": snapshot["services"],
        "heartbeat": {
            "updated_at": snapshot["workspace_state"]["updated_at"],
            "age_seconds": snapshot["last_event_age_seconds"],
            "delayed": snapshot["is_stale"],
        },
        "logs": [
            {
                "service": entry.service,
                "step": entry.step,
                "status": entry.status,
                "progress": entry.progress,
                "message": entry.message,
                "details": entry.details,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in reversed(logs)
        ],
        "products": products,
    }


@router.get("/missions")
def mission_catalog(
    principal: Annotated[Principal, Depends(require_authenticated)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> MissionCatalogResponse:
    with get_session() as session:
        query = cast(
            Any,
            mission_query(
                session,
                principal,
                requested_owner=owner_subject,
                action="catalog",
            ),
        )
        total = int(query.count())
        missions = cast(
            list[Mission],
            query.order_by(Mission.updated_at.desc())
            .offset(offset)
            .limit(limit)
            .all(),
        )
        return {
            "items": [_catalog_item(mission) for mission in missions],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/missions/{vol_id}")
def mission_detail(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> dict[str, Any]:
    with get_session() as session:
        mission = get_owned_mission(
            session,
            vol_id,
            principal,
            requested_owner=owner_subject,
            action="detail",
        )
        return _mission_detail(session, mission)
