"""PostgreSQL-only identity control-plane invariants."""

from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from shared.database import (
    AccessAuditEvent,
    ApiCredential,
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
    get_session,
)
from shared.access_audit import append_access_audit_event
from shared.identity import append_audit_event, issue_credential

credential_routes = importlib.import_module(
    "app4-dashboard.api.routers.identity_credentials"
)
security = importlib.import_module("app4-dashboard.api.security")


@pytest.mark.integration
def test_postgres_identity_audit_is_append_only() -> None:
    suffix = uuid4().hex[:12]
    organization_id = f"identity-{suffix}"
    with get_session() as session:
        organization = Organization(
            id=organization_id,
            display_name="Identity integration test",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        member = OrganizationMember(
            organization_id=organization_id,
            subject=f"admin-{suffix}",
            role="admin",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        session.add_all([organization, member])
        session.flush()
        event = append_audit_event(
            session,
            organization_id=organization_id,
            actor_subject="integration",
            action="member_created",
            target_type="member",
            target_id=member.id,
            after_state={"subject": member.subject},
        )
        session.flush()
        event_id = event.event_id

    with pytest.raises(DBAPIError, match="append-only"):
        with get_session() as session:
            session.execute(
                text(
                    "UPDATE identity_audit_events "
                    "SET actor_subject = 'tampered' WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            )

    with get_session() as session:
        persisted = (
            session.query(IdentityAuditEvent)
            .filter(IdentityAuditEvent.event_id == event_id)
            .one()
        )
        assert persisted.actor_subject == "integration"


@pytest.mark.integration
def test_postgres_cross_member_access_audit_is_append_only() -> None:
    suffix = uuid4().hex[:12]
    organization_id = f"access-audit-{suffix}"
    with get_session() as session:
        organization = Organization(
            id=organization_id,
            display_name="Access audit integration test",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        session.add(organization)
        session.flush()
        event = append_access_audit_event(
            session,
            organization_id=organization_id,
            actor_subject=f"admin-{suffix}",
            actor_role="admin",
            actor_realm="tenant",
            actor_member_id=None,
            actor_credential_id=None,
            action="raster_export",
            target_owner_subject=f"operator-{suffix}",
            resource_type="mission",
            resource_id=f"flight-{suffix}",
        )
        session.flush()
        event_id = event.event_id

    with pytest.raises(DBAPIError, match="append-only"):
        with get_session() as session:
            session.execute(
                text(
                    "UPDATE access_audit_events "
                    "SET action = 'tampered' WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            )

    with get_session() as session:
        persisted = (
            session.query(AccessAuditEvent)
            .filter(AccessAuditEvent.event_id == event_id)
            .one()
        )
        assert persisted.action == "raster_export"


@pytest.mark.integration
def test_concurrent_credential_rotation_has_one_replacement(monkeypatch) -> None:
    monkeypatch.setenv(
        "DRONEAI_CREDENTIAL_PEPPER",
        "integration-credential-pepper-at-least-32-characters",
    )
    suffix = uuid4().hex[:12]
    organization_id = f"rotation-{suffix}"
    with get_session() as session:
        organization = Organization(
            id=organization_id,
            display_name="Rotation integration test",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        member = OrganizationMember(
            organization_id=organization_id,
            subject=f"admin-{suffix}",
            role="admin",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        session.add_all([organization, member])
        session.flush()
        credential_id = issue_credential(
            session,
            member=member,
            name="concurrent rotation",
            actor_subject="integration",
        ).record.id
        member_id = member.id
        subject = member.subject

    principal = security.Principal(
        subject=subject,
        role="admin",
        organization_id=organization_id,
        member_id=member_id,
        credential_id=credential_id,
        auth_version=1,
        authentication_method="database",
    )
    barrier = Barrier(2)

    def rotate() -> str | int:
        barrier.wait()
        try:
            result = credential_routes.rotate_credential(
                credential_id,
                credential_routes.CredentialRotateRequest(),
                principal,
            )
            return str(result["id"])
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: rotate(), range(2)))
    assert outcomes.count(409) == 1
    replacements = [value for value in outcomes if isinstance(value, str)]
    assert len(replacements) == 1

    with get_session() as session:
        old = session.get(ApiCredential, credential_id)
        active = (
            session.query(ApiCredential)
            .filter(
                ApiCredential.member_id == member_id,
                ApiCredential.status == "active",
            )
            .all()
        )
        assert old.status == "revoked"
        assert [record.id for record in active] == replacements
