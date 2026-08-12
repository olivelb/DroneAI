"""Ground-control read, bundle-publication and audit routes."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query

from shared.database import GcpAuditEvent, GcpPoint, GcpSet

from ..gcp_audit import audit_event_json, record_gcp_audit
from ..gcp_route_support import (
    GcpSessionDependency,
    OperatorPrincipal,
    OwnerSubjectQuery,
    ViewerPrincipal,
    authorized_mission,
    require_gcp_set,
)
from ..gcp_workspace import materialize_gcp_bundle, point_json, set_json
from ..map_support import JsonObject

router = APIRouter()


@router.get("/{vol_id}/gcps")
def list_ground_control(
    vol_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = authorized_mission(session, vol_id, principal, owner_subject, "gcp_list")
    sets = cast(
        list[GcpSet],
        session.query(GcpSet)
        .filter(GcpSet.mission_id == mission.id)
        .order_by(GcpSet.created_at.desc())
        .all(),
    )
    features = [
        point_json(session, point)
        for gcp_set in sets
        for point in cast(list[GcpPoint], gcp_set.points)
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "gcp_sets": [set_json(session, item, include_points=False) for item in sets],
    }


@router.get("/{vol_id}/gcps/{set_id}")
def ground_control_detail(
    vol_id: str,
    set_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = authorized_mission(session, vol_id, principal, owner_subject, "gcp_detail")
    gcp_set = require_gcp_set(session, mission.id, set_id)
    return set_json(session, gcp_set, include_points=True)


@router.post("/{vol_id}/gcps/{set_id}/bundle")
def prepare_ground_control_bundle(
    vol_id: str,
    set_id: str,
    principal: OperatorPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = authorized_mission(
        session,
        vol_id,
        principal,
        owner_subject,
        "gcp_bundle_create",
    )
    gcp_set = require_gcp_set(session, mission.id, set_id)
    try:
        bundle = materialize_gcp_bundle(
            gcp_set,
            mission.organization_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to publish immutable GCP bundle: {error}",
        ) from error
    record_gcp_audit(
        session,
        gcp_set,
        actor_subject=principal.subject,
        action="bundle_materialized",
        before_state=None,
        after_state=bundle,
    )
    session.flush()
    return bundle


@router.get("/{vol_id}/gcps/{set_id}/audit")
def ground_control_audit(
    vol_id: str,
    set_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JsonObject:
    mission = authorized_mission(session, vol_id, principal, owner_subject, "gcp_audit")
    gcp_set = require_gcp_set(session, mission.id, set_id)
    events = cast(
        list[GcpAuditEvent],
        session.query(GcpAuditEvent)
        .filter(GcpAuditEvent.gcp_set_id == gcp_set.id)
        .order_by(GcpAuditEvent.created_at.desc(), GcpAuditEvent.id.desc())
        .limit(limit)
        .all(),
    )
    return {
        "set_id": set_id,
        "events": [audit_event_json(event) for event in events],
    }
