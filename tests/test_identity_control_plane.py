from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.database import (
    AccessAuditEvent,
    ApiCredential,
    IdentityCapability,
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
)
from shared.identity import issue_credential

security = importlib.import_module("app4-dashboard.api.security")
identity_routes = importlib.import_module("app4-dashboard.api.routers.identity")
member_routes = importlib.import_module("app4-dashboard.api.routers.identity_members")
access_audit_routes = importlib.import_module(
    "app4-dashboard.api.routers.identity_access_audit"
)
credential_routes = importlib.import_module(
    "app4-dashboard.api.routers.identity_credentials"
)
capability_routes = importlib.import_module(
    "app4-dashboard.api.routers.identity_capabilities"
)

ADMIN_KEY = "bootstrap-admin-key-with-at-least-32-characters"
PEPPER = "credential-pepper-with-at-least-32-characters"
SESSION_SECRET = "session-signing-secret-with-at-least-32-characters"


@pytest.fixture
def identity_platform(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Organization.__table__,
        OrganizationMember.__table__,
        ApiCredential.__table__,
        IdentityAuditEvent.__table__,
        IdentityCapability.__table__,
        AccessAuditEvent.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope(**_context):
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(security, "get_session", session_scope)
    monkeypatch.setattr(member_routes, "get_session", session_scope)
    monkeypatch.setattr(access_audit_routes, "get_session", session_scope)
    monkeypatch.setattr(credential_routes, "get_session", session_scope)
    monkeypatch.setattr(capability_routes, "get_session", session_scope)
    monkeypatch.setenv("DRONEAI_ENV", "development")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_CREDENTIAL_PEPPER", PEPPER)
    monkeypatch.setenv("DRONEAI_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv(
        "DRONEAI_API_KEYS_JSON",
        json.dumps(
            [
                {
                    "key": ADMIN_KEY,
                    "subject": "bootstrap-admin",
                    "role": "admin",
                    "organization_id": "acme-survey",
                }
            ]
        ),
    )
    application = FastAPI()
    application.include_router(identity_routes.router)
    return TestClient(application), session_scope


def _admin_headers() -> dict[str, str]:
    return {"X-API-Key": ADMIN_KEY}


def _bootstrap(client: TestClient) -> None:
    response = client.post(
        "/auth/bootstrap",
        headers=_admin_headers(),
        json={"display_name": "Acme Survey"},
    )
    assert response.status_code == 201
    assert response.json()["member"]["role"] == "admin"


def test_bootstrap_issues_only_one_time_hashed_credentials_and_rotates_sessions(
    identity_platform,
):
    client, session_scope = identity_platform
    _bootstrap(client)

    created = client.post(
        "/auth/credentials",
        headers=_admin_headers(),
        json={"name": "admin laptop"},
    )
    assert created.status_code == 201
    token = created.json()["token"]
    credential_id = created.json()["id"]
    assert token.startswith(f"dai.{credential_id}.")

    with session_scope() as session:
        credential = session.get(ApiCredential, credential_id)
        assert credential is not None
        assert credential.secret_hash != token
        assert token not in credential.secret_hash

    session_response = client.post("/auth/session", json={"api_key": token})
    assert session_response.status_code == 200
    session_token = session_response.cookies[security.SESSION_COOKIE_NAME]
    assert security.authenticate_token(session_token).subject == "bootstrap-admin"
    with session_scope() as session:
        first_last_used = session.get(ApiCredential, credential_id).last_used_at
    assert first_last_used is not None
    assert security.authenticate_token(token).subject == "bootstrap-admin"
    with session_scope() as session:
        assert session.get(ApiCredential, credential_id).last_used_at == first_last_used

    rotated = client.post(
        f"/auth/credentials/{credential_id}/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "admin laptop rotated"},
    )
    assert rotated.status_code == 201
    replacement = rotated.json()["token"]
    assert replacement != token
    assert security.authenticate_token(token) is None
    assert security.authenticate_token(session_token) is None
    assert security.authenticate_token(replacement).subject == "bootstrap-admin"

    audit = client.get("/auth/audit-events", headers=_admin_headers())
    assert audit.status_code == 200
    assert {event["action"] for event in audit.json()} >= {
        "organization_bootstrapped",
        "credential_created",
        "credential_rotated",
    }
    assert token not in audit.text
    assert replacement not in audit.text


def test_admin_can_filter_durable_cross_member_access_events(identity_platform):
    client, session_scope = identity_platform
    _bootstrap(client)
    with session_scope() as session:
        session.add_all(
            [
                AccessAuditEvent(
                    organization_id="acme-survey",
                    actor_subject="bootstrap-admin",
                    actor_role="admin",
                    actor_realm="tenant",
                    action="detail",
                    target_owner_subject="field-operator",
                    resource_type="mission",
                    resource_id="flight-42",
                    outcome="authorized",
                ),
                AccessAuditEvent(
                    organization_id="acme-survey",
                    actor_subject="bootstrap-admin",
                    actor_role="admin",
                    actor_realm="tenant",
                    action="list",
                    target_owner_subject="other-operator",
                    resource_type="dataset",
                    outcome="authorized",
                ),
            ]
        )

    response = client.get(
        "/auth/access-audit-events",
        headers=_admin_headers(),
        params={
            "owner_subject": "field-operator",
            "resource_type": "mission",
        },
    )

    assert response.status_code == 200
    assert [event["resource_id"] for event in response.json()] == ["flight-42"]
    assert response.json()[0]["target_owner_subject"] == "field-operator"
    assert response.json()[0]["outcome"] == "authorized"


def test_member_permissions_suspension_and_last_admin_invariant(identity_platform):
    client, _session_scope = identity_platform
    _bootstrap(client)
    member = client.post(
        "/auth/members",
        headers=_admin_headers(),
        json={"subject": "field-viewer", "role": "viewer"},
    )
    assert member.status_code == 201
    issued = client.post(
        "/auth/credentials",
        headers=_admin_headers(),
        json={"name": "viewer device", "member_subject": "field-viewer"},
    )
    viewer_token = issued.json()["token"]
    viewer_session = security.issue_session_token(
        security.authenticate_api_key(viewer_token),
        3600,
    )

    forbidden = client.post(
        "/auth/members",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"subject": "another-user", "role": "viewer"},
    )
    assert forbidden.status_code == 403
    own_rotation = client.post(
        f"/auth/credentials/{issued.json()['id']}/rotate",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={},
    )
    assert own_rotation.status_code == 201

    last_admin = client.patch(
        "/auth/members/bootstrap-admin",
        headers=_admin_headers(),
        json={"status": "suspended"},
    )
    assert last_admin.status_code == 409

    suspended = client.patch(
        "/auth/members/field-viewer",
        headers=_admin_headers(),
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert security.authenticate_token(own_rotation.json()["token"]) is None
    assert security.authenticate_token(viewer_session) is None


def test_one_time_invitation_creates_member_and_hashed_credential(
    identity_platform,
):
    client, session_scope = identity_platform
    _bootstrap(client)

    invitation = client.post(
        "/auth/invitations",
        headers=_admin_headers(),
        json={
            "subject": "invited-operator",
            "role": "operator",
            "expires_in_hours": 24,
        },
    )
    assert invitation.status_code == 201
    capability_token = invitation.json()["token"]
    capability_id = invitation.json()["id"]
    assert capability_token.startswith(f"dic.{capability_id}.")
    listing = client.get("/auth/invitations", headers=_admin_headers())
    assert listing.status_code == 200
    assert capability_token not in listing.text

    redeemed = client.post(
        "/auth/capabilities/redeem",
        json={
            "token": capability_token,
            "credential_name": "invited workstation",
        },
    )
    assert redeemed.status_code == 201
    assert redeemed.json()["purpose"] == "invitation"
    assert redeemed.json()["member"]["role"] == "operator"
    credential_token = redeemed.json()["credential"]["token"]
    principal = security.authenticate_token(credential_token)
    assert principal is not None
    assert principal.subject == "invited-operator"
    assert principal.organization_id == "acme-survey"
    assert client.post(
        "/auth/capabilities/redeem",
        json={
            "token": capability_token,
            "credential_name": "replay",
        },
    ).status_code == 401

    with session_scope() as session:
        capability = session.get(IdentityCapability, capability_id)
        assert capability.status == "redeemed"
        assert capability.secret_hash != capability_token
        actions = {
            item.action for item in session.query(IdentityAuditEvent).all()
        }
        assert {"invitation_created", "invitation_accepted"} <= actions
        assert capability_token not in str(
            [item.after_state for item in session.query(IdentityAuditEvent).all()]
        )


def test_revoked_invitation_cannot_be_redeemed(identity_platform):
    client, session_scope = identity_platform
    _bootstrap(client)
    invitation = client.post(
        "/auth/invitations",
        headers=_admin_headers(),
        json={
            "subject": "revoked-invitee",
            "role": "viewer",
            "expires_in_hours": 24,
        },
    ).json()

    revoked = client.delete(
        f"/auth/invitations/{invitation['id']}",
        headers=_admin_headers(),
    )

    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.post(
        "/auth/capabilities/redeem",
        json={"token": invitation["token"], "credential_name": "forbidden"},
    ).status_code == 401
    with session_scope() as session:
        assert session.query(OrganizationMember).filter_by(
            subject="revoked-invitee",
        ).count() == 0
        assert session.query(IdentityAuditEvent).filter_by(
            action="invitation_revoked",
        ).count() == 1


def test_self_issued_recovery_restores_access_without_platform_impersonation(
    identity_platform,
):
    client, session_scope = identity_platform
    _bootstrap(client)
    credential = client.post(
        "/auth/credentials",
        headers=_admin_headers(),
        json={"name": "primary credential"},
    ).json()
    primary_token = credential["token"]
    primary_headers = {"Authorization": f"Bearer {primary_token}"}

    recovery = client.post(
        "/auth/recovery-tokens",
        headers=primary_headers,
        json={"expires_in_hours": 24},
    )
    assert recovery.status_code == 201
    recovery_token = recovery.json()["token"]
    assert recovery_token.startswith("dic.")
    with session_scope() as session:
        member = session.query(OrganizationMember).filter_by(
            subject="bootstrap-admin",
        ).one()
        member.role = "viewer"
        member.auth_version += 1
    assert client.delete(
        f"/auth/credentials/{credential['id']}",
        headers=primary_headers,
    ).status_code == 200
    assert security.authenticate_token(primary_token) is None

    restored = client.post(
        "/auth/capabilities/redeem",
        json={
            "token": recovery_token,
            "credential_name": "recovered credential",
        },
    )
    assert restored.status_code == 201
    replacement = restored.json()["credential"]["token"]
    principal = security.authenticate_token(replacement)
    assert principal is not None
    assert principal.subject == "bootstrap-admin"
    assert principal.role == "viewer"
    with session_scope() as session:
        actions = {
            item.action for item in session.query(IdentityAuditEvent).all()
        }
        assert {"recovery_created", "recovery_redeemed"} <= actions


def test_credentials_are_hidden_across_organization_boundaries(identity_platform):
    client, session_scope = identity_platform
    _bootstrap(client)
    with session_scope() as session:
        organization = Organization(
            id="beta-mapping",
            display_name="Beta Mapping",
            status="active",
            created_by="test",
            updated_by="test",
        )
        member = OrganizationMember(
            organization_id=organization.id,
            subject="beta-admin",
            role="admin",
            status="active",
            created_by="test",
            updated_by="test",
        )
        session.add_all([organization, member])
        session.flush()
        beta_credential = issue_credential(
            session,
            member=member,
            name="beta key",
            actor_subject="test",
        ).record
        beta_credential_id = beta_credential.id

    response = client.delete(
        f"/auth/credentials/{beta_credential_id}",
        headers=_admin_headers(),
    )
    assert response.status_code == 404


def test_expiry_and_organization_suspension_fail_closed(identity_platform):
    client, session_scope = identity_platform
    _bootstrap(client)
    active = client.post(
        "/auth/credentials",
        headers=_admin_headers(),
        json={"name": "active key"},
    ).json()["token"]
    with session_scope() as session:
        member = (
            session.query(OrganizationMember)
            .filter(OrganizationMember.subject == "bootstrap-admin")
            .one()
        )
        expired = issue_credential(
            session,
            member=member,
            name="expired key",
            actor_subject="test",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ).token
    assert security.authenticate_token(expired) is None
    assert security.authenticate_token(f"{active}tampered") is None

    with session_scope() as session:
        organization = session.get(Organization, "acme-survey")
        organization.status = "suspended"
    assert security.authenticate_token(active) is None


def test_production_can_run_without_static_bootstrap_keys(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_RLS_REQUIRED", "true")
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_CREDENTIAL_PEPPER", PEPPER)
    monkeypatch.setenv("DRONEAI_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.setenv("S3_ACCESS_KEY", "production-access")
    monkeypatch.setenv("S3_SECRET_KEY", "production-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.example.com/droneai")
    monkeypatch.delenv("DRONEAI_API_KEYS_JSON", raising=False)

    security.validate_production_configuration()
