"""Owner-scoped mission lookup and explicit administrator delegation."""

from __future__ import annotations

import logging
from typing import Any, Protocol, Self, cast

from fastapi import HTTPException, status

from shared.access_audit import append_access_audit_event
from shared.database import Mission
from shared.tenancy import LEGACY_ORGANIZATION_ID

from .security import Principal

audit_logger = logging.getLogger("droneai.audit.mission_access")


class MissionQuery(Protocol):
    def filter(self, *criteria: Any) -> Self: ...

    def first(self) -> Any: ...

    def with_for_update(self) -> Self: ...


class MissionSession(Protocol):
    def query(self, *entities: Any) -> MissionQuery: ...


def resolve_owner_subject(
    principal: Principal,
    requested_owner: str | None,
    *,
    action: str,
    vol_id: str | None = None,
    audit_session: Any | None = None,
) -> str:
    """Resolve mission ownership inside one tenant with explicit delegation."""

    owner = (requested_owner or principal.subject).strip()
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner subject cannot be empty",
        )
    if owner != principal.subject:
        if principal.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mission not found",
            )
        if audit_session is None:
            raise RuntimeError(
                "Cross-member mission access requires a durable audit session"
            )
        append_access_audit_event(
            audit_session,
            organization_id=getattr(
                principal,
                "organization_id",
                LEGACY_ORGANIZATION_ID,
            ),
            actor_subject=principal.subject,
            actor_role=principal.role,
            actor_realm=getattr(principal, "realm", "tenant"),
            actor_member_id=getattr(principal, "member_id", None),
            actor_credential_id=getattr(principal, "credential_id", None),
            action=action,
            target_owner_subject=owner,
            resource_type="mission",
            resource_id=vol_id,
        )
        audit_session.flush()
        audit_logger.warning(
            "admin_cross_member_mission_access principal=%s owner=%s action=%s vol_id=%s",
            principal.subject,
            owner,
            action,
            vol_id or "-",
        )
    return owner


def mission_query(
    session: MissionSession,
    principal: Principal,
    *,
    requested_owner: str | None = None,
    action: str,
    vol_id: str | None = None,
) -> MissionQuery:
    owner = resolve_owner_subject(
        principal,
        requested_owner,
        action=action,
        vol_id=vol_id,
        audit_session=session,
    )
    organization_id = getattr(
        principal,
        "organization_id",
        LEGACY_ORGANIZATION_ID,
    )
    return session.query(Mission).filter(
        Mission.organization_id == organization_id,
        Mission.owner_subject == owner,
    )


def get_owned_mission(
    session: MissionSession,
    vol_id: str,
    principal: Principal,
    *,
    requested_owner: str | None = None,
    action: str = "read",
    for_update: bool = False,
) -> Mission:
    query = mission_query(
        session,
        principal,
        requested_owner=requested_owner,
        action=action,
        vol_id=vol_id,
    ).filter(Mission.vol_id == vol_id)
    if for_update:
        query = query.with_for_update()
    mission = cast(Mission | None, query.first())
    if mission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mission not found",
        )
    return mission
