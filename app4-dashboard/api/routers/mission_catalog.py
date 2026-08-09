"""Owner-scoped mission catalogue and durable detail projection."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict, cast

from fastapi import APIRouter, Depends, Query

from shared.database import Mission, get_session

from ..mission_access import get_owned_mission, mission_query
from ..mission_detail import mission_detail_projection
from ..mission_state import serialize_mission
from ..stage_projection import project_stage_mission
from ..security import Principal, require_authenticated

router = APIRouter()


class MissionCatalogResponse(TypedDict):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


def _catalog_item(mission: Mission) -> dict[str, Any]:
    serialized = serialize_mission(cast(Any, mission))
    stage_projection = project_stage_mission(mission, mission.stage_runs)
    parameters = cast(dict[str, Any], mission.params or {})
    updated_at = (
        stage_projection["updated_at"]
        if stage_projection is not None
        else mission.updated_at
    )
    return {
        "vol_id": mission.vol_id,
        "owner_subject": mission.owner_subject,
        "status": (
            stage_projection["status"] if stage_projection else mission.status
        ),
        "current_step": (
            stage_projection["current_step"]
            if stage_projection
            else mission.current_step
        ),
        "progress": (
            stage_projection["progress"] if stage_projection else mission.progress
        ),
        "pipeline": mission.pipeline,
        "quality_profile": parameters.get("quality_profile"),
        "attempt_count": int(mission.retry_count or 0) + 1,
        "created_at": mission.created_at.isoformat() if mission.created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "overall_status": (
            stage_projection["overall_status"]
            if stage_projection
            else serialized["overall_status"]
        ),
        "is_stale": (
            stage_projection["is_stale"]
            if stage_projection
            else serialized["is_stale"]
        ),
        "last_event_age_seconds": (
            stage_projection["last_event_age_seconds"]
            if stage_projection
            else serialized["last_event_age_seconds"]
        ),
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
        return mission_detail_projection(
            session,
            mission,
            _catalog_item(mission),
        )
