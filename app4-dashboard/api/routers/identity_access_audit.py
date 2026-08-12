"""Tenant administrator access-audit queries."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from shared.database import AccessAuditEvent, get_session

from .. import security

router = APIRouter(
    prefix="/auth",
    tags=["access audit"],
    dependencies=[Depends(security.bind_tenant_context)],
)


@router.get("/access-audit-events")
def list_access_audit_events(
    principal: Annotated[security.Principal, Depends(security.require_admin)],
    limit: int = Query(default=100, ge=1, le=200),
    owner_subject: str | None = Query(default=None, max_length=256),
    resource_type: Literal["mission", "dataset"] | None = Query(default=None),
) -> list[dict[str, object]]:
    """Expose tenant-scoped delegation evidence to organization admins."""

    with get_session() as session:
        query = session.query(AccessAuditEvent).filter(
            AccessAuditEvent.organization_id == principal.organization_id
        )
        if owner_subject is not None:
            query = query.filter(AccessAuditEvent.target_owner_subject == owner_subject)
        if resource_type is not None:
            query = query.filter(AccessAuditEvent.resource_type == resource_type)
        events = (
            query.order_by(
                AccessAuditEvent.created_at.desc(),
                AccessAuditEvent.id.desc(),
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "event_id": event.event_id,
                "actor_subject": event.actor_subject,
                "actor_role": event.actor_role,
                "actor_realm": event.actor_realm,
                "actor_member_id": event.actor_member_id,
                "actor_credential_id": event.actor_credential_id,
                "action": event.action,
                "target_owner_subject": event.target_owner_subject,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "outcome": event.outcome,
                "created_at": event.created_at,
            }
            for event in events
        ]
