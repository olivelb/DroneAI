"""One-time tenant invitations and self-issued recovery workflows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from shared.database import (
    IdentityCapability,
    Organization,
    OrganizationMember,
    get_session,
)
from shared.identity import append_audit_event, issue_credential
from shared.identity_capabilities import (
    authenticate_capability,
    capability_id_from_token,
    capability_member,
    issue_capability,
    redeem_capability,
    revoke_capability,
)

from .. import security
from ..identity_api import (
    SUBJECT_PATTERN,
    credential_response,
    credential_state,
    member_state,
)

router = APIRouter(prefix="/auth", tags=["identity capabilities"])


class InvitationCreateRequest(BaseModel):
    subject: str = Field(pattern=SUBJECT_PATTERN)
    role: Literal["viewer", "operator", "admin"]
    expires_in_hours: int = Field(default=168, ge=1, le=720)


class RecoveryCreateRequest(BaseModel):
    expires_in_hours: int = Field(default=720, ge=1, le=720)


class CapabilityRedeemRequest(BaseModel):
    token: str = Field(min_length=32, max_length=4096)
    credential_name: str = Field(min_length=1, max_length=160)


def _capability_response(
    record: IdentityCapability,
    *,
    token: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": record.id,
        "purpose": record.purpose,
        "subject": record.subject,
        "role": record.role,
        "status": record.status,
        "expires_at": record.expires_at,
        "redeemed_at": record.redeemed_at,
        "revoked_at": record.revoked_at,
        "revoked_by": record.revoked_by,
        "created_by": record.created_by,
        "created_at": record.created_at,
    }
    if token is not None:
        result["token"] = token
    return result


def _active_capability_exists(
    session: Any,
    *,
    organization_id: str,
    purpose: str,
    subject: str,
) -> bool:
    return bool(
        session.query(IdentityCapability).filter(
            IdentityCapability.organization_id == organization_id,
            IdentityCapability.purpose == purpose,
            IdentityCapability.subject == subject,
            IdentityCapability.status == "pending",
            IdentityCapability.expires_at > datetime.now(UTC),
        ).count()
    )


@router.get(
    "/invitations",
    dependencies=[Depends(security.bind_tenant_context)],
)
def list_invitations(
    principal: Annotated[security.Principal, Depends(security.require_admin)],
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, object]]:
    with get_session() as session:
        records = session.query(IdentityCapability).filter(
            IdentityCapability.organization_id == principal.organization_id,
            IdentityCapability.purpose == "invitation",
        ).order_by(IdentityCapability.created_at.desc()).limit(limit).all()
        return [_capability_response(item) for item in records]


@router.post(
    "/invitations",
    status_code=201,
    dependencies=[Depends(security.bind_tenant_context)],
)
def create_invitation(
    payload: InvitationCreateRequest,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    with get_session() as session:
        organization = session.query(Organization).filter(
            Organization.id == principal.organization_id,
        ).with_for_update().one_or_none()
        if organization is None or organization.status != "active":
            raise HTTPException(status_code=409, detail="Organization is not active")
        existing = session.query(OrganizationMember).filter(
            OrganizationMember.organization_id == principal.organization_id,
            OrganizationMember.subject == payload.subject,
        ).one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Member already exists")
        if _active_capability_exists(
            session,
            organization_id=principal.organization_id,
            purpose="invitation",
            subject=payload.subject,
        ):
            raise HTTPException(status_code=409, detail="Invitation already pending")
        issued = issue_capability(
            session,
            organization_id=principal.organization_id,
            purpose="invitation",
            subject=payload.subject,
            role=payload.role,
            actor_subject=principal.subject,
            expires_at=datetime.now(UTC)
            + timedelta(hours=payload.expires_in_hours),
        )
        append_audit_event(
            session,
            organization_id=principal.organization_id,
            actor_subject=principal.subject,
            action="invitation_created",
            target_type="identity_capability",
            target_id=cast(str, issued.record.id),
            after_state={
                "subject": payload.subject,
                "role": payload.role,
                "expires_at": cast(datetime, issued.record.expires_at).isoformat(),
            },
        )
        return _capability_response(issued.record, token=issued.token)


@router.delete(
    "/invitations/{capability_id}",
    dependencies=[Depends(security.bind_tenant_context)],
)
def revoke_invitation(
    capability_id: str,
    principal: Annotated[security.Principal, Depends(security.require_admin)],
) -> dict[str, object]:
    with get_session() as session:
        record = session.query(IdentityCapability).filter(
            IdentityCapability.id == capability_id,
            IdentityCapability.organization_id == principal.organization_id,
            IdentityCapability.purpose == "invitation",
        ).with_for_update().one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if record.status == "pending":
            revoke_capability(record, actor_subject=principal.subject)
            append_audit_event(
                session,
                organization_id=principal.organization_id,
                actor_subject=principal.subject,
                action="invitation_revoked",
                target_type="identity_capability",
                target_id=capability_id,
                before_state={"status": "pending"},
                after_state={"status": "revoked"},
            )
            session.flush()
        return _capability_response(record)


@router.get(
    "/recovery-tokens",
    dependencies=[Depends(security.bind_tenant_context)],
)
def list_recovery_tokens(
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> list[dict[str, object]]:
    if principal.member_id is None:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap a durable credential before creating recovery tokens",
        )
    with get_session() as session:
        records = session.query(IdentityCapability).filter(
            IdentityCapability.organization_id == principal.organization_id,
            IdentityCapability.member_id == principal.member_id,
            IdentityCapability.purpose == "recovery",
        ).order_by(IdentityCapability.created_at.desc()).all()
        return [_capability_response(item) for item in records]


@router.post(
    "/recovery-tokens",
    status_code=201,
    dependencies=[Depends(security.bind_tenant_context)],
)
def create_recovery_token(
    payload: RecoveryCreateRequest,
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> dict[str, object]:
    if principal.member_id is None:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap a durable credential before creating recovery tokens",
    )
    with get_session() as session:
        member = session.query(OrganizationMember).filter(
            OrganizationMember.id == principal.member_id,
        ).with_for_update().one_or_none()
        if member is None or member.status != "active":
            raise HTTPException(status_code=409, detail="Member is not active")
        if _active_capability_exists(
            session,
            organization_id=principal.organization_id,
            purpose="recovery",
            subject=principal.subject,
        ):
            raise HTTPException(status_code=409, detail="Recovery token already pending")
        issued = issue_capability(
            session,
            organization_id=principal.organization_id,
            purpose="recovery",
            subject=principal.subject,
            role=principal.role,
            member_id=principal.member_id,
            actor_subject=principal.subject,
            expires_at=datetime.now(UTC)
            + timedelta(hours=payload.expires_in_hours),
        )
        append_audit_event(
            session,
            organization_id=principal.organization_id,
            actor_subject=principal.subject,
            action="recovery_created",
            target_type="identity_capability",
            target_id=cast(str, issued.record.id),
            after_state={
                "member_id": principal.member_id,
                "expires_at": cast(datetime, issued.record.expires_at).isoformat(),
            },
        )
        return _capability_response(issued.record, token=issued.token)


@router.delete(
    "/recovery-tokens/{capability_id}",
    dependencies=[Depends(security.bind_tenant_context)],
)
def revoke_recovery_token(
    capability_id: str,
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> dict[str, object]:
    if principal.member_id is None:
        raise HTTPException(status_code=404, detail="Recovery token not found")
    with get_session() as session:
        record = session.query(IdentityCapability).filter(
            IdentityCapability.id == capability_id,
            IdentityCapability.organization_id == principal.organization_id,
            IdentityCapability.member_id == principal.member_id,
            IdentityCapability.purpose == "recovery",
        ).with_for_update().one_or_none()
        if record is None:
            raise HTTPException(status_code=404, detail="Recovery token not found")
        if record.status == "pending":
            revoke_capability(record, actor_subject=principal.subject)
            append_audit_event(
                session,
                organization_id=principal.organization_id,
                actor_subject=principal.subject,
                action="recovery_revoked",
                target_type="identity_capability",
                target_id=capability_id,
                before_state={"status": "pending"},
                after_state={"status": "revoked"},
            )
            session.flush()
        return _capability_response(record)


@router.post("/capabilities/redeem", status_code=201)
def redeem_identity_capability(
    payload: CapabilityRedeemRequest,
) -> dict[str, object]:
    capability_id = capability_id_from_token(payload.token)
    if capability_id is None:
        raise HTTPException(status_code=401, detail="Invalid identity capability")
    with get_session(identity_capability_id=capability_id) as session:
        record = authenticate_capability(session, payload.token, lock=True)
        if record is None:
            raise HTTPException(status_code=401, detail="Invalid identity capability")
        organization = session.get(Organization, record.organization_id)
        if organization is None or organization.status != "active":
            raise HTTPException(status_code=409, detail="Organization is not active")
        member = capability_member(session, record)
        if record.purpose == "invitation":
            if member is not None:
                raise HTTPException(status_code=409, detail="Member already exists")
            member = OrganizationMember(
                organization_id=record.organization_id,
                subject=record.subject,
                role=record.role,
                status="active",
                created_by=record.created_by,
                updated_by=record.created_by,
            )
            session.add(member)
            session.flush()
            append_audit_event(
                session,
                organization_id=cast(str, record.organization_id),
                actor_subject=cast(str, record.subject),
                action="member_created",
                target_type="member",
                target_id=cast(str, member.id),
                after_state=member_state(member),
            )
            capability_action = "invitation_accepted"
        else:
            if member is None or member.status != "active":
                raise HTTPException(status_code=409, detail="Member is not active")
            capability_action = "recovery_redeemed"
        issued = issue_credential(
            session,
            member=member,
            name=payload.credential_name,
            actor_subject=cast(str, record.subject),
        )
        append_audit_event(
            session,
            organization_id=cast(str, record.organization_id),
            actor_subject=cast(str, record.subject),
            action="credential_created",
            target_type="credential",
            target_id=cast(str, issued.record.id),
            after_state=credential_state(issued.record),
        )
        append_audit_event(
            session,
            organization_id=cast(str, record.organization_id),
            actor_subject=cast(str, record.subject),
            action=capability_action,
            target_type="identity_capability",
            target_id=cast(str, record.id),
            before_state={"status": "pending"},
            after_state={
                "status": "redeemed",
                "member_id": member.id,
                "credential_id": issued.record.id,
            },
        )
        session.flush()
        redeem_capability(record)
        session.flush()
        return {
            "purpose": record.purpose,
            "member": member_state(member),
            "credential": credential_response(
                issued.record,
                member,
                token=issued.token,
            ),
        }
