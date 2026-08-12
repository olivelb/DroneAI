"""Durable cross-member access audit primitives."""

from __future__ import annotations

from typing import Any

from shared.database import AccessAuditEvent


def append_access_audit_event(
    session: Any,
    *,
    organization_id: str,
    actor_subject: str,
    actor_role: str,
    actor_realm: str,
    actor_member_id: str | None,
    actor_credential_id: str | None,
    action: str,
    target_owner_subject: str,
    resource_type: str,
    resource_id: str | None,
) -> AccessAuditEvent:
    """Append one authorized delegation without recording credential secrets."""

    event = AccessAuditEvent(
        organization_id=organization_id,
        actor_subject=actor_subject,
        actor_role=actor_role,
        actor_realm=actor_realm,
        actor_member_id=actor_member_id,
        actor_credential_id=actor_credential_id,
        action=action,
        target_owner_subject=target_owner_subject,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="authorized",
    )
    session.add(event)
    return event
