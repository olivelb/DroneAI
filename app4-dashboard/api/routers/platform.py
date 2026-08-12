"""Global support endpoints with no tenant data-plane privileges."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.database import (
    Organization,
    PlatformAuditEvent,
    PlatformCredential,
    PlatformMember,
    get_session,
)
from shared.platform_identity import (
    append_platform_audit_event,
    issue_platform_credential,
    revoke_platform_credential,
)

from .. import security

router = APIRouter(
    prefix="/platform",
    tags=["platform support"],
    dependencies=[Depends(security.require_platform_support)],
)


class OrganizationStatusUpdate(BaseModel):
    status: Literal["active", "suspended"]


class PlatformCredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expires_at: datetime | None = None


class PlatformCredentialRotate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)


def _credential_state(credential: PlatformCredential) -> dict[str, object]:
    def timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": credential.id,
        "name": credential.name,
        "status": credential.status,
        "expires_at": timestamp(
            cast(datetime | None, credential.expires_at),
        ),
        "last_used_at": timestamp(
            cast(datetime | None, credential.last_used_at),
        ),
        "revoked_at": timestamp(
            cast(datetime | None, credential.revoked_at),
        ),
        "revoked_by": credential.revoked_by,
        "revocation_reason": credential.revocation_reason,
        "rotated_from_id": credential.rotated_from_id,
        "created_at": timestamp(cast(datetime, credential.created_at)),
        "updated_at": timestamp(cast(datetime, credential.updated_at)),
    }


def _platform_credential_id(principal: security.Principal) -> str:
    if principal.credential_id is None:
        raise HTTPException(
            status_code=403,
            detail="Durable platform credential required",
        )
    return principal.credential_id


def _platform_member(
    session: Session,
    principal: security.Principal,
) -> PlatformMember:
    if principal.member_id is None:
        raise HTTPException(status_code=403, detail="Platform member required")
    member = session.get(PlatformMember, principal.member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Platform member not found")
    return member


@router.get("/me")
def read_platform_session(
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
) -> dict[str, object]:
    return {
        "subject": principal.subject,
        "role": principal.role,
        "realm": principal.realm,
    }


@router.get("/organizations")
def list_organizations(
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
    after: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict[str, object]]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        query = session.query(Organization)
        if after is not None:
            query = query.filter(Organization.id > after)
        organizations = query.order_by(Organization.id).limit(limit).all()
        return [
            {
                "id": item.id,
                "display_name": item.display_name,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in organizations
        ]


@router.patch("/organizations/{organization_id}/status")
def update_organization_status(
    organization_id: str,
    payload: OrganizationStatusUpdate,
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
) -> dict[str, object]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        organization = session.query(Organization).filter(
            Organization.id == organization_id,
        ).with_for_update().one_or_none()
        if organization is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        before: dict[str, object] = {
            "status": cast(str, organization.status),
        }
        if organization.status != payload.status:
            mutable_organization = cast(Any, organization)
            mutable_organization.status = payload.status
            mutable_organization.updated_by = principal.subject
            append_platform_audit_event(
                session,
                actor_subject=principal.subject,
                action="organization_status_updated",
                target_type="organization",
                target_id=organization_id,
                before_state=before,
                after_state={"status": payload.status},
            )
            session.flush()
        return {
            "id": organization.id,
            "display_name": organization.display_name,
            "status": organization.status,
            "updated_at": organization.updated_at,
        }


@router.get("/audit-events")
def list_platform_audit_events(
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, object]]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        events = session.query(PlatformAuditEvent).order_by(
            PlatformAuditEvent.created_at.desc(),
            PlatformAuditEvent.id.desc(),
        ).limit(limit).all()
        return [
            {
                "event_id": item.event_id,
                "actor_subject": item.actor_subject,
                "action": item.action,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "before_state": item.before_state,
                "after_state": item.after_state,
                "created_at": item.created_at,
            }
            for item in events
        ]


@router.get("/credentials")
def list_platform_credentials(
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
) -> list[dict[str, object]]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        member = _platform_member(session, principal)
        records = session.query(PlatformCredential).filter(
            PlatformCredential.member_id == member.id,
        ).order_by(PlatformCredential.created_at.desc()).all()
        return [_credential_state(item) for item in records]


@router.post("/credentials", status_code=201)
def create_platform_credential(
    payload: PlatformCredentialCreate,
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
) -> dict[str, object]:
    expires_at = payload.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise HTTPException(
                status_code=422,
                detail="expires_at must be in the future",
            )
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        member = _platform_member(session, principal)
        issued = issue_platform_credential(
            session,
            member=member,
            name=payload.name,
            actor_subject=principal.subject,
            expires_at=expires_at,
        )
        append_platform_audit_event(
            session,
            actor_subject=principal.subject,
            action="platform_credential_created",
            target_type="platform_credential",
            target_id=cast(str, issued.record.id),
            after_state=_credential_state(issued.record),
        )
        return {
            **_credential_state(issued.record),
            "token": issued.token,
        }


def _owned_platform_credential(
    session: Session,
    principal: security.Principal,
    credential_id: str,
) -> tuple[PlatformCredential, PlatformMember]:
    member = _platform_member(session, principal)
    credential = session.query(PlatformCredential).filter(
        PlatformCredential.id == credential_id,
        PlatformCredential.member_id == member.id,
    ).with_for_update().one_or_none()
    if credential is None:
        raise HTTPException(status_code=404, detail="Platform credential not found")
    return credential, member


@router.delete("/credentials/{credential_id}")
def revoke_owned_platform_credential(
    credential_id: str,
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
    reason: str | None = Query(default=None, max_length=500),
) -> dict[str, object]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        credential, _member = _owned_platform_credential(
            session,
            principal,
            credential_id,
        )
        if credential.status == "active":
            before = _credential_state(credential)
            revoked_at = datetime.now(UTC)
            after = {
                **before,
                "status": "revoked",
                "revoked_at": revoked_at.isoformat(),
                "revoked_by": principal.subject,
                "revocation_reason": reason,
            }
            append_platform_audit_event(
                session,
                actor_subject=principal.subject,
                action="platform_credential_revoked",
                target_type="platform_credential",
                target_id=credential_id,
                before_state=before,
                after_state=after,
            )
            session.flush()
            revoke_platform_credential(
                credential,
                actor_subject=principal.subject,
                reason=reason,
                revoked_at=revoked_at,
            )
            session.flush()
        return _credential_state(credential)


@router.post("/credentials/{credential_id}/rotate", status_code=201)
def rotate_owned_platform_credential(
    credential_id: str,
    payload: PlatformCredentialRotate,
    principal: Annotated[
        security.Principal,
        Depends(security.require_platform_support),
    ],
) -> dict[str, object]:
    with get_session(
        platform_credential_id=_platform_credential_id(principal),
    ) as session:
        credential, member = _owned_platform_credential(
            session,
            principal,
            credential_id,
        )
        if credential.status != "active":
            raise HTTPException(
                status_code=409,
                detail="Platform credential is not active",
            )
        before = _credential_state(credential)
        issued = issue_platform_credential(
            session,
            member=member,
            name=payload.name or cast(str, credential.name),
            actor_subject=principal.subject,
            expires_at=cast(datetime | None, credential.expires_at),
            rotated_from_id=credential_id,
        )
        revoked_at = datetime.now(UTC)
        revoked_state = {
            **before,
            "status": "revoked",
            "revoked_at": revoked_at.isoformat(),
            "revoked_by": principal.subject,
            "revocation_reason": "rotated",
        }
        append_platform_audit_event(
            session,
            actor_subject=principal.subject,
            action="platform_credential_rotated",
            target_type="platform_credential",
            target_id=credential_id,
            before_state=before,
            after_state={
                "revoked": revoked_state,
                "replacement": _credential_state(issued.record),
            },
        )
        session.flush()
        revoke_platform_credential(
            credential,
            actor_subject=principal.subject,
            reason="rotated",
            revoked_at=revoked_at,
        )
        session.flush()
        return {
            **_credential_state(issued.record),
            "token": issued.token,
        }
