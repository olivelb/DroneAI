"""Organization-scoped API credential lifecycle endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.database import ApiCredential, OrganizationMember, get_session
from shared.identity import (
    append_audit_event,
    issue_credential,
    mark_credential_revoked,
)

from .. import security
from ..identity_api import (
    SUBJECT_PATTERN,
    authorize_member_access,
    credential_response,
    credential_state,
    find_credential,
    find_member,
)

router = APIRouter(
    prefix="/auth/credentials",
    tags=["identity administration"],
    dependencies=[Depends(security.bind_tenant_context)],
)


class CredentialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    member_subject: str | None = Field(default=None, pattern=SUBJECT_PATTERN)
    expires_at: datetime | None = None


class CredentialRotateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)


def _locked_owned_credential(
    session: Session,
    principal: security.Principal,
    credential_id: str,
) -> tuple[ApiCredential, OrganizationMember]:
    credential = find_credential(
        session,
        principal,
        credential_id,
        for_update=True,
    )
    member = session.get(OrganizationMember, credential.member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    authorize_member_access(principal, member)
    return credential, member


@router.get("")
def list_credentials(
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
    member_subject: str | None = Query(default=None, pattern=SUBJECT_PATTERN),
) -> list[dict[str, object]]:
    target_subject = member_subject or principal.subject
    with get_session() as session:
        member = find_member(session, principal.organization_id, target_subject)
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")
        authorize_member_access(principal, member)
        credentials = (
            session.query(ApiCredential)
            .filter(
                ApiCredential.organization_id == principal.organization_id,
                ApiCredential.member_id == member.id,
            )
            .order_by(ApiCredential.created_at.desc())
            .all()
        )
        return [credential_response(item, member) for item in credentials]


@router.post("", status_code=201)
def create_credential(
    payload: CredentialCreateRequest,
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> dict[str, object]:
    target_subject = payload.member_subject or principal.subject
    with get_session() as session:
        member = find_member(session, principal.organization_id, target_subject)
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")
        authorize_member_access(principal, member)
        if member.status != "active":
            raise HTTPException(status_code=409, detail="Member is suspended")
        expires_at = payload.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                raise HTTPException(status_code=422, detail="expires_at must be in the future")
        issued = issue_credential(
            session,
            member=member,
            name=payload.name,
            actor_subject=principal.subject,
            expires_at=expires_at,
        )
        append_audit_event(
            session,
            organization_id=principal.organization_id,
            actor_subject=principal.subject,
            action="credential_created",
            target_type="credential",
            target_id=cast(str, issued.record.id),
            after_state=credential_state(issued.record),
        )
        result = credential_response(issued.record, member, token=issued.token)
    return result


@router.delete("/{credential_id}")
def revoke_credential(
    credential_id: str,
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
    reason: str | None = Query(default=None, max_length=500),
) -> dict[str, object]:
    with get_session() as session:
        credential, member = _locked_owned_credential(
            session, principal, credential_id
        )
        if credential.status == "active":
            before = credential_state(credential)
            mark_credential_revoked(
                credential,
                actor_subject=principal.subject,
                reason=reason,
            )
            append_audit_event(
                session,
                organization_id=principal.organization_id,
                actor_subject=principal.subject,
                action="credential_revoked",
                target_type="credential",
                target_id=cast(str, credential.id),
                before_state=before,
                after_state=credential_state(credential),
            )
            session.flush()
        result = credential_response(credential, member)
    return result


@router.post("/{credential_id}/rotate", status_code=201)
def rotate_credential(
    credential_id: str,
    payload: CredentialRotateRequest,
    principal: Annotated[
        security.Principal,
        Depends(security.require_authenticated),
    ],
) -> dict[str, object]:
    with get_session() as session:
        credential, member = _locked_owned_credential(
            session, principal, credential_id
        )
        if credential.status != "active":
            raise HTTPException(status_code=409, detail="Credential is not active")
        if member.status != "active":
            raise HTTPException(status_code=409, detail="Member is suspended")
        before = credential_state(credential)
        issued = issue_credential(
            session,
            member=member,
            name=payload.name or cast(str, credential.name),
            actor_subject=principal.subject,
            expires_at=cast(datetime | None, credential.expires_at),
            rotated_from_id=cast(str, credential.id),
        )
        mark_credential_revoked(
            credential,
            actor_subject=principal.subject,
            reason="rotated",
        )
        append_audit_event(
            session,
            organization_id=principal.organization_id,
            actor_subject=principal.subject,
            action="credential_rotated",
            target_type="credential",
            target_id=cast(str, credential.id),
            before_state=before,
            after_state={
                "revoked": credential_state(credential),
                "replacement": credential_state(issued.record),
            },
        )
        session.flush()
        result = credential_response(issued.record, member, token=issued.token)
    return result
