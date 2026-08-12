"""HTTP serialization and scoped lookups for identity administration."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from shared.database import ApiCredential, Organization, OrganizationMember

from . import security

SUBJECT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,255}$"


def member_state(member: OrganizationMember) -> dict[str, object]:
    return {
        "id": member.id,
        "subject": member.subject,
        "role": member.role,
        "status": member.status,
        "auth_version": member.auth_version,
    }


def credential_state(credential: ApiCredential) -> dict[str, object]:
    def timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": credential.id,
        "member_id": credential.member_id,
        "name": credential.name,
        "status": credential.status,
        "expires_at": timestamp(cast(datetime | None, credential.expires_at)),
        "last_used_at": timestamp(cast(datetime | None, credential.last_used_at)),
        "revoked_at": timestamp(cast(datetime | None, credential.revoked_at)),
        "revoked_by": credential.revoked_by,
        "revocation_reason": credential.revocation_reason,
        "rotated_from_id": credential.rotated_from_id,
    }


def organization_response(organization: Organization) -> dict[str, object]:
    return {
        "id": organization.id,
        "display_name": organization.display_name,
        "status": organization.status,
        "created_at": organization.created_at,
        "updated_at": organization.updated_at,
    }


def member_response(member: OrganizationMember) -> dict[str, object]:
    return {
        **member_state(member),
        "created_at": member.created_at,
        "updated_at": member.updated_at,
    }


def credential_response(
    credential: ApiCredential,
    member: OrganizationMember,
    *,
    token: str | None = None,
) -> dict[str, object]:
    result = {
        **credential_state(credential),
        "member_subject": member.subject,
        "created_at": credential.created_at,
        "updated_at": credential.updated_at,
    }
    if token is not None:
        result["token"] = token
    return result


def find_member(
    session: Session,
    organization_id: str,
    subject: str,
    *,
    for_update: bool = False,
) -> OrganizationMember | None:
    query = session.query(OrganizationMember).filter(
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.subject == subject,
    )
    if for_update:
        query = query.with_for_update()
    return query.one_or_none()


def find_credential(
    session: Session,
    principal: security.Principal,
    credential_id: str,
    *,
    for_update: bool = False,
) -> ApiCredential:
    query = session.query(ApiCredential).filter(
        ApiCredential.id == credential_id,
        ApiCredential.organization_id == principal.organization_id,
    )
    if for_update:
        query = query.with_for_update()
    credential = query.one_or_none()
    if credential is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return credential


def authorize_member_access(
    principal: security.Principal,
    member: OrganizationMember,
) -> None:
    if principal.role != "admin" and member.subject != principal.subject:
        raise HTTPException(status_code=403, detail="Cannot manage another member")
