"""Durable credentials for platform support, isolated from tenant identity."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from shared.database import (
    PlatformAuditEvent,
    PlatformCredential,
    PlatformMember,
)
from shared.identity import (
    credential_hash,
    credential_is_expired,
    should_update_credential_last_used,
)

PLATFORM_CREDENTIAL_TOKEN_PREFIX = "dps"
PLATFORM_PRINCIPAL_ORGANIZATION = "platform-control"


@dataclass(frozen=True)
class AuthenticatedPlatformIdentity:
    subject: str
    role: str
    member_id: str
    credential_id: str
    auth_version: int


@dataclass(frozen=True)
class IssuedPlatformCredential:
    record: PlatformCredential
    token: str


def platform_credential_id_from_token(token: str) -> str | None:
    prefix, separator, remainder = token.partition(".")
    credential_id, second_separator, secret = remainder.partition(".")
    if (
        prefix != PLATFORM_CREDENTIAL_TOKEN_PREFIX
        or not separator
        or not second_separator
        or len(secret) < 32
    ):
        return None
    try:
        return str(UUID(credential_id))
    except ValueError:
        return None


def issue_platform_credential(
    session: Session,
    *,
    member: PlatformMember,
    name: str,
    actor_subject: str,
    expires_at: datetime | None = None,
    rotated_from_id: str | None = None,
) -> IssuedPlatformCredential:
    name = name.strip()
    actor_subject = actor_subject.strip()
    if not name or len(name) > 160:
        raise ValueError("Platform credential name must contain 1 to 160 characters")
    if not actor_subject or len(actor_subject) > 256:
        raise ValueError("Platform actor subject must contain 1 to 256 characters")
    credential_id = str(uuid4())
    token = (
        f"{PLATFORM_CREDENTIAL_TOKEN_PREFIX}.{credential_id}."
        f"{secrets.token_urlsafe(32)}"
    )
    record = PlatformCredential(
        id=credential_id,
        member_id=member.id,
        name=name,
        secret_hash=credential_hash(token),
        status="active",
        expires_at=expires_at,
        rotated_from_id=rotated_from_id,
        created_by=actor_subject,
    )
    session.add(record)
    session.flush()
    return IssuedPlatformCredential(record=record, token=token)


def _authenticated_identity(
    credential: PlatformCredential,
    member: PlatformMember,
) -> AuthenticatedPlatformIdentity:
    return AuthenticatedPlatformIdentity(
        subject=cast(str, member.subject),
        role=cast(str, member.role),
        member_id=cast(str, member.id),
        credential_id=cast(str, credential.id),
        auth_version=cast(int, member.auth_version),
    )


def authenticate_platform_credential(
    session: Session,
    token: str,
    *,
    update_last_used: bool = True,
) -> AuthenticatedPlatformIdentity | None:
    credential_id = platform_credential_id_from_token(token)
    if credential_id is None:
        return None
    credential = session.get(PlatformCredential, credential_id)
    if credential is None or credential.status != "active":
        return None
    if not secrets.compare_digest(
        cast(str, credential.secret_hash),
        credential_hash(token),
    ):
        return None
    now = datetime.now(UTC)
    if credential_is_expired(cast(datetime | None, credential.expires_at), now):
        return None
    member = session.get(PlatformMember, credential.member_id)
    if (
        member is None
        or member.status != "active"
        or member.role != "support"
    ):
        return None
    if update_last_used and should_update_credential_last_used(
        cast(datetime | None, credential.last_used_at),
        now,
    ):
        credential.last_used_at = now
    return _authenticated_identity(credential, member)


def validate_platform_session_identity(
    session: Session,
    *,
    member_id: str,
    credential_id: str,
    auth_version: int,
) -> AuthenticatedPlatformIdentity | None:
    credential = session.get(PlatformCredential, credential_id)
    member = session.get(PlatformMember, member_id)
    if (
        credential is None
        or credential.member_id != member_id
        or credential.status != "active"
        or credential_is_expired(
            cast(datetime | None, credential.expires_at),
            datetime.now(UTC),
        )
        or member is None
        or member.status != "active"
        or member.role != "support"
        or member.auth_version != auth_version
    ):
        return None
    return _authenticated_identity(credential, member)


def revoke_platform_credential(
    credential: PlatformCredential,
    *,
    actor_subject: str,
    reason: str | None,
    revoked_at: datetime | None = None,
) -> None:
    record = cast(Any, credential)
    record.status = "revoked"
    record.revoked_at = revoked_at or datetime.now(UTC)
    record.revoked_by = actor_subject
    record.revocation_reason = reason


def append_platform_audit_event(
    session: Session,
    *,
    actor_subject: str,
    action: str,
    target_type: str,
    target_id: str,
    before_state: dict[str, object] | None = None,
    after_state: dict[str, object] | None = None,
) -> PlatformAuditEvent:
    event = PlatformAuditEvent(
        actor_subject=actor_subject,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_state=before_state,
        after_state=after_state,
    )
    session.add(event)
    return event
