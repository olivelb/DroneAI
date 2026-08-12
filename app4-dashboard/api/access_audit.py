"""API-level helpers for durable cross-member access auditing."""

from __future__ import annotations

from typing import Any

from shared.access_audit import append_access_audit_event
from shared.tenancy import LEGACY_ORGANIZATION_ID

from .security import Principal


def record_authorized_cross_member_access(
    session: Any,
    principal: Principal,
    *,
    action: str,
    target_owner_subject: str,
    resource_type: str,
    resource_id: str | None,
) -> None:
    """Persist and flush one authorized delegation before resource access."""

    append_access_audit_event(
        session,
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
        target_owner_subject=target_owner_subject,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    session.flush()
