"""Organization and member lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.database import (
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
    get_session,
)
from shared.identity import (
    append_audit_event,
    update_member as apply_member_update,
    update_organization as apply_organization_update,
)

from .. import security
from ..identity_api import (
    SUBJECT_PATTERN,
    find_member,
    member_response,
    member_state,
    organization_response,
)

router = APIRouter(prefix="/auth", tags=["identity administration"])


class BootstrapRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)


class OrganizationUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)


class MemberCreateRequest(BaseModel):
    subject: str = Field(pattern=SUBJECT_PATTERN)
    role: Literal["viewer", "operator", "admin"]


class MemberUpdateRequest(BaseModel):
    role: Literal["viewer", "operator", "admin"] | None = None
    status: Literal["active", "suspended"] | None = None


def _create_member(
    session: Session,
    *,
    organization_id: str,
    subject: str,
    role: str,
    actor_subject: str,
) -> OrganizationMember:
    member = OrganizationMember(
        organization_id=organization_id,
        subject=subject,
        role=role,
        status="active",
        created_by=actor_subject,
        updated_by=actor_subject,
    )
    session.add(member)
    session.flush()
    append_audit_event(
        session,
        organization_id=organization_id,
        actor_subject=actor_subject,
        action="member_created",
        target_type="member",
        target_id=cast(str, member.id),
        after_state=member_state(member),
    )
    return member


@router.post("/bootstrap", status_code=201)
def bootstrap_organization(
    payload: BootstrapRequest,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    """Adopt one static admin into the durable identity control plane."""

    with get_session() as session:
        organization = session.get(Organization, principal.organization_id)
        organization_created = organization is None
        if organization is None:
            organization = Organization(
                id=principal.organization_id,
                display_name=payload.display_name,
                status="active",
                created_by=principal.subject,
                updated_by=principal.subject,
            )
            session.add(organization)
            session.flush()
        member = find_member(
            session,
            principal.organization_id,
            principal.subject,
        )
        member_created = member is None
        if member is None:
            member = _create_member(
                session,
                organization_id=principal.organization_id,
                subject=principal.subject,
                role="admin",
                actor_subject=principal.subject,
            )
        if organization_created or member_created:
            append_audit_event(
                session,
                organization_id=principal.organization_id,
                actor_subject=principal.subject,
                action="organization_bootstrapped",
                target_type="organization",
                target_id=cast(str, organization.id),
                after_state={
                    "id": organization.id,
                    "display_name": organization.display_name,
                    "status": organization.status,
                    "member_id": member.id,
                },
            )
        result = {
            "created": organization_created or member_created,
            "organization": organization_response(organization),
            "member": member_response(member),
        }
    return result


@router.get("/organization")
def read_organization(
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> dict[str, object]:
    with get_session() as session:
        organization = session.get(Organization, principal.organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail="Organization not bootstrapped")
        result = organization_response(organization)
    return result


@router.patch("/organization")
def update_organization(
    payload: OrganizationUpdateRequest,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    with get_session() as session:
        organization = session.get(Organization, principal.organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail="Organization not bootstrapped")
        before: dict[str, object] = {
            "display_name": cast(str, organization.display_name)
        }
        apply_organization_update(
            organization,
            display_name=payload.display_name,
            actor_subject=principal.subject,
        )
        append_audit_event(
            session,
            organization_id=principal.organization_id,
            actor_subject=principal.subject,
            action="organization_updated",
            target_type="organization",
            target_id=cast(str, organization.id),
            before_state=before,
            after_state={"display_name": organization.display_name},
        )
        session.flush()
        result = organization_response(organization)
    return result


@router.get("/members")
def list_members(
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> list[dict[str, object]]:
    with get_session() as session:
        members = (
            session.query(OrganizationMember)
            .filter(OrganizationMember.organization_id == principal.organization_id)
            .order_by(OrganizationMember.subject)
            .all()
        )
        return [member_response(member) for member in members]


@router.post("/members", status_code=201)
def create_member(
    payload: MemberCreateRequest,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    with get_session() as session:
        organization = session.get(Organization, principal.organization_id)
        if organization is None:
            raise HTTPException(status_code=409, detail="Bootstrap the organization first")
        if organization.status != "active":
            raise HTTPException(status_code=409, detail="Organization is suspended")
        if find_member(session, principal.organization_id, payload.subject) is not None:
            raise HTTPException(status_code=409, detail="Member already exists")
        member = _create_member(
            session,
            organization_id=principal.organization_id,
            subject=payload.subject,
            role=payload.role,
            actor_subject=principal.subject,
        )
        result = member_response(member)
    return result


@router.patch("/members/{subject}")
def update_member(
    subject: str,
    payload: MemberUpdateRequest,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    with get_session() as session:
        organization = (
            session.query(Organization)
            .filter(Organization.id == principal.organization_id)
            .with_for_update()
            .one_or_none()
        )
        if organization is None:
            raise HTTPException(status_code=404, detail="Organization not bootstrapped")
        member = find_member(
            session,
            principal.organization_id,
            subject,
            for_update=True,
        )
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")
        current_role = cast(str, member.role)
        current_status = cast(str, member.status)
        next_role = payload.role or current_role
        next_status = payload.status or current_status
        removes_active_admin = (
            current_role == "admin"
            and current_status == "active"
            and (next_role != "admin" or next_status != "active")
        )
        if removes_active_admin:
            active_admins = (
                session.query(OrganizationMember)
                .filter(
                    OrganizationMember.organization_id == principal.organization_id,
                    OrganizationMember.role == "admin",
                    OrganizationMember.status == "active",
                )
                .with_for_update()
                .all()
            )
            if len(active_admins) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot remove the last active organization admin",
                )
        before = member_state(member)
        changed = next_role != current_role or next_status != current_status
        if changed:
            apply_member_update(
                member,
                role=next_role,
                status=next_status,
                actor_subject=principal.subject,
            )
            append_audit_event(
                session,
                organization_id=principal.organization_id,
                actor_subject=principal.subject,
                action="member_updated",
                target_type="member",
                target_id=cast(str, member.id),
                before_state=before,
                after_state=member_state(member),
            )
            session.flush()
        result = member_response(member)
    return result


@router.get("/audit-events")
def list_identity_audit_events(
    principal: Annotated[security.Principal, Depends(security.require_admin)],
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, object]]:
    with get_session() as session:
        events = (
            session.query(IdentityAuditEvent)
            .filter(IdentityAuditEvent.organization_id == principal.organization_id)
            .order_by(IdentityAuditEvent.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "event_id": event.event_id,
                "actor_subject": event.actor_subject,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "before_state": event.before_state,
                "after_state": event.after_state,
                "created_at": event.created_at,
            }
            for event in events
        ]
