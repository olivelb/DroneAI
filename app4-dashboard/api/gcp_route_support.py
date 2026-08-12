"""Shared authorization and lookup dependencies for GCP HTTP routes."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Query

from shared.database import GcpSet

from .gcp_workspace import gcp_route_session
from .map_support import MissionRecord, RouteSession, get_mission
from .security import Principal, require_authenticated, require_operator

OperatorPrincipal = Annotated[Principal, Depends(require_operator)]
ViewerPrincipal = Annotated[Principal, Depends(require_authenticated)]
GcpSessionDependency = Annotated[RouteSession, Depends(gcp_route_session)]
OwnerSubjectQuery = Annotated[str | None, Query(max_length=256)]


def authorized_mission(
    session: RouteSession,
    vol_id: str,
    principal: Principal,
    owner_subject: str | None,
    action: str,
) -> MissionRecord:
    return get_mission(
        session,
        vol_id,
        principal,
        owner_subject=owner_subject,
        action=action,
    )


def require_gcp_set(
    session: RouteSession,
    mission_id: int,
    set_id: str,
) -> GcpSet:
    gcp_set = cast(
        GcpSet | None,
        session.query(GcpSet)
        .filter(
            GcpSet.mission_id == mission_id,
            GcpSet.set_id == set_id,
        )
        .first(),
    )
    if gcp_set is None:
        raise HTTPException(status_code=404, detail="GCP set not found")
    return gcp_set
