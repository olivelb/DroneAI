"""One-time organization invitation and self-issued recovery capabilities."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from shared.database import IdentityCapability, OrganizationMember
from shared.identity import credential_hash, credential_is_expired

CAPABILITY_TOKEN_PREFIX = "dic"
CAPABILITY_MAX_LIFETIME = timedelta(days=30)


@dataclass(frozen=True)
class IssuedCapability:
    record: IdentityCapability
    token: str


def capability_id_from_token(token: str) -> str | None:
    prefix, separator, remainder = token.partition(".")
    capability_id, second_separator, secret = remainder.partition(".")
    if (
        prefix != CAPABILITY_TOKEN_PREFIX
        or not separator
        or not second_separator
        or len(secret) < 32
    ):
        return None
    try:
        return str(UUID(capability_id))
    except ValueError:
        return None


def issue_capability(
    session: Session,
    *,
    organization_id: str,
    purpose: str,
    subject: str,
    role: str,
    actor_subject: str,
    expires_at: datetime,
    member_id: str | None = None,
) -> IssuedCapability:
    now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now or expires_at > now + CAPABILITY_MAX_LIFETIME:
        raise ValueError("Capability expiry must be within the next 30 days")
    if purpose not in {"invitation", "recovery"}:
        raise ValueError("Unsupported identity capability purpose")
    if (purpose == "invitation") != (member_id is None):
        raise ValueError("Capability member does not match its purpose")
    capability_id = str(uuid4())
    token = (
        f"{CAPABILITY_TOKEN_PREFIX}.{capability_id}."
        f"{secrets.token_urlsafe(32)}"
    )
    record = IdentityCapability(
        id=capability_id,
        organization_id=organization_id,
        member_id=member_id,
        purpose=purpose,
        subject=subject,
        role=role,
        secret_hash=credential_hash(token),
        status="pending",
        expires_at=expires_at,
        created_by=actor_subject,
    )
    session.add(record)
    session.flush()
    return IssuedCapability(record=record, token=token)


def authenticate_capability(
    session: Session,
    token: str,
    *,
    lock: bool = False,
) -> IdentityCapability | None:
    capability_id = capability_id_from_token(token)
    if capability_id is None:
        return None
    query = session.query(IdentityCapability).filter(
        IdentityCapability.id == capability_id,
    )
    if lock:
        query = query.with_for_update()
    record = query.one_or_none()
    if (
        record is None
        or record.status != "pending"
        or credential_is_expired(
            cast(datetime, record.expires_at),
            datetime.now(UTC),
        )
        or not secrets.compare_digest(
            cast(str, record.secret_hash),
            credential_hash(token),
        )
    ):
        return None
    return cast(IdentityCapability, record)


def redeem_capability(record: IdentityCapability) -> None:
    mutable = cast(Any, record)
    mutable.status = "redeemed"
    mutable.redeemed_at = datetime.now(UTC)


def revoke_capability(
    record: IdentityCapability,
    *,
    actor_subject: str,
) -> None:
    mutable = cast(Any, record)
    mutable.status = "revoked"
    mutable.revoked_at = datetime.now(UTC)
    mutable.revoked_by = actor_subject


def capability_member(
    session: Session,
    record: IdentityCapability,
) -> OrganizationMember | None:
    if record.member_id is not None:
        return cast(
            OrganizationMember | None,
            session.get(OrganizationMember, record.member_id),
        )
    return cast(
        OrganizationMember | None,
        session.query(OrganizationMember).filter(
            OrganizationMember.organization_id == record.organization_id,
            OrganizationMember.subject == record.subject,
        ).one_or_none(),
    )
