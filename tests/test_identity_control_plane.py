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
    ApiCredential,
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
)
from shared.identity import issue_credential

security = importlib.import_module("app4-dashboard.api.security")
identity_routes = importlib.import_module("app4-dashboard.api.routers.identity")
member_routes = importlib.import_module("app4-dashboard.api.routers.identity_members")
credential_routes = importlib.import_module(
    "app4-dashboard.api.routers.identity_credentials"
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
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
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
    monkeypatch.setattr(credential_routes, "get_session", session_scope)
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
