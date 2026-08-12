"""Durable organization membership and API credential primitives."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from shared.database import (
    ApiCredential,
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
)

CREDENTIAL_TOKEN_PREFIX = "dai"


@dataclass(frozen=True)
class AuthenticatedIdentity:
    subject: str
    role: str
    organization_id: str
    member_id: str
    credential_id: str
    auth_version: int


@dataclass(frozen=True)
class IssuedCredential:
    record: ApiCredential
    token: str


def database_authentication_enabled(*, production: bool = False) -> bool:
    raw = os.getenv("DRONEAI_DATABASE_AUTH_ENABLED")
    if raw is None:
        return production
    return raw.strip().lower() in {"1", "true", "yes"}


def credential_pepper() -> bytes:
    value = os.getenv("DRONEAI_CREDENTIAL_PEPPER", "")
    if len(value) < 32:
        raise RuntimeError(
            "DRONEAI_CREDENTIAL_PEPPER must contain at least 32 characters"
        )
    return value.encode("utf-8")


def _credential_hash(token: str) -> str:
    return hmac_new(credential_pepper(), token.encode("utf-8"), sha256).hexdigest()


def credential_id_from_token(token: str) -> str | None:
    """Return the public credential UUID without authenticating its secret."""

    prefix, separator, remainder = token.partition(".")
    credential_id, second_separator, secret = remainder.partition(".")
    if prefix != CREDENTIAL_TOKEN_PREFIX or not separator or not second_separator:
        return None
    if len(secret) < 32:
        return None
    try:
        return str(UUID(credential_id))
    except ValueError:
        return None


def _is_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= now


def _should_update_last_used(value: datetime | None, now: datetime) -> bool:
    raw_interval = os.getenv(
        "DRONEAI_CREDENTIAL_LAST_USED_WRITE_INTERVAL_SECONDS",
        "300",
    )
    try:
        interval_seconds = int(raw_interval)
    except ValueError as error:
        raise RuntimeError(
            "DRONEAI_CREDENTIAL_LAST_USED_WRITE_INTERVAL_SECONDS must be an integer"
        ) from error
    if interval_seconds < 0:
        raise RuntimeError(
            "DRONEAI_CREDENTIAL_LAST_USED_WRITE_INTERVAL_SECONDS cannot be negative"
        )
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= now - timedelta(seconds=interval_seconds)


def issue_credential(
    session: Session,
    *,
    member: OrganizationMember,
    name: str,
    actor_subject: str,
    expires_at: datetime | None = None,
    rotated_from_id: str | None = None,
) -> IssuedCredential:
    credential_id = str(uuid4())
    token = f"{CREDENTIAL_TOKEN_PREFIX}.{credential_id}.{secrets.token_urlsafe(32)}"
    record = ApiCredential(
        id=credential_id,
        organization_id=member.organization_id,
        member_id=member.id,
        name=name,
        secret_hash=_credential_hash(token),
        status="active",
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
        created_by=actor_subject,
    )
    session.add(record)
    session.flush()
    return IssuedCredential(record=record, token=token)


def update_organization(
    organization: Organization,
    *,
    display_name: str,
    actor_subject: str,
) -> None:
    record = cast(Any, organization)
    record.display_name = display_name
    record.updated_by = actor_subject


def update_member(
    member: OrganizationMember,
    *,
    role: str,
    status: str,
    actor_subject: str,
) -> None:
    record = cast(Any, member)
    record.role = role
    record.status = status
    record.auth_version += 1
    record.updated_by = actor_subject


def mark_credential_revoked(
    credential: ApiCredential,
    *,
    actor_subject: str,
    reason: str | None,
) -> None:
    record = cast(Any, credential)
    record.status = "revoked"
    record.revoked_at = datetime.now(UTC)
    record.revoked_by = actor_subject
    record.revocation_reason = reason


def authenticate_credential(
    session: Session,
    token: str,
    *,
    update_last_used: bool = True,
) -> AuthenticatedIdentity | None:
    credential_id = credential_id_from_token(token)
    if credential_id is None:
        return None
    credential = session.get(ApiCredential, credential_id)
    if credential is None or credential.status != "active":
        return None
    if not secrets.compare_digest(credential.secret_hash, _credential_hash(token)):
        return None
    now = datetime.now(UTC)
    if _is_expired(credential.expires_at, now):
        return None
    member = session.get(OrganizationMember, credential.member_id)
    organization = session.get(Organization, credential.organization_id)
    if (
        member is None
        or member.organization_id != credential.organization_id
        or member.status != "active"
        or organization is None
        or organization.status != "active"
    ):
        return None
    if update_last_used and _should_update_last_used(credential.last_used_at, now):
        credential.last_used_at = now
    return AuthenticatedIdentity(
        subject=member.subject,
        role=member.role,
        organization_id=member.organization_id,
        member_id=member.id,
        credential_id=credential.id,
        auth_version=member.auth_version,
    )


def validate_session_identity(
    session: Session,
    *,
    member_id: str,
    credential_id: str,
    auth_version: int,
) -> AuthenticatedIdentity | None:
    credential = session.get(ApiCredential, credential_id)
    member = session.get(OrganizationMember, member_id)
    if (
        credential is None
        or credential.member_id != member_id
        or credential.status != "active"
        or _is_expired(credential.expires_at, datetime.now(UTC))
        or member is None
        or member.status != "active"
        or member.auth_version != auth_version
        or member.organization_id != credential.organization_id
    ):
        return None
    organization = session.get(Organization, member.organization_id)
    if organization is None or organization.status != "active":
        return None
    return AuthenticatedIdentity(
        subject=member.subject,
        role=member.role,
        organization_id=member.organization_id,
        member_id=member.id,
        credential_id=credential.id,
        auth_version=member.auth_version,
    )


def append_audit_event(
    session: Session,
    *,
    organization_id: str,
    actor_subject: str,
    action: str,
    target_type: str,
    target_id: str,
    before_state: dict[str, object] | None = None,
    after_state: dict[str, object] | None = None,
) -> IdentityAuditEvent:
    event = IdentityAuditEvent(
        organization_id=organization_id,
        actor_subject=actor_subject,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_state=before_state,
        after_state=after_state,
    )
    session.add(event)
    return event
