from __future__ import annotations

import importlib
import json
from contextlib import contextmanager

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.database import (
    Organization,
    PlatformAuditEvent,
    PlatformCredential,
    PlatformMember,
)
from shared.platform_identity import issue_platform_credential

security = importlib.import_module("app4-dashboard.api.security")
platform_routes = importlib.import_module(
    "app4-dashboard.api.routers.platform"
)
platform_tool = importlib.import_module("tools.manage_platform_support")

PEPPER = "platform-test-credential-pepper-at-least-32-characters"
SESSION_SECRET = "platform-test-session-secret-at-least-32-characters"
TENANT_KEY = "tenant-admin-key-with-at-least-32-characters"


@pytest.fixture
def platform_control_plane(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        Organization.__table__,
        PlatformMember.__table__,
        PlatformCredential.__table__,
        PlatformAuditEvent.__table__,
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
                    "key": TENANT_KEY,
                    "subject": "tenant-admin",
                    "role": "admin",
                    "organization_id": "tenant-a",
                }
            ]
        ),
    )
    monkeypatch.setattr(security, "get_session", session_scope)
    monkeypatch.setattr(platform_routes, "get_session", session_scope)
    with session_scope() as session:
        session.add_all(
            [
                Organization(
                    id="tenant-a",
                    display_name="Tenant A",
                    status="active",
                    created_by="test",
                    updated_by="test",
                ),
                Organization(
                    id="tenant-b",
                    display_name="Tenant B",
                    status="active",
                    created_by="test",
                    updated_by="test",
                ),
            ]
        )
        member = PlatformMember(
            subject="support@example.com",
            role="support",
            status="active",
            created_by="operator",
            updated_by="operator",
        )
        session.add(member)
        session.flush()
        issued = issue_platform_credential(
            session,
            member=member,
            name="support workstation",
            actor_subject="operator",
        )
        token = issued.token
        credential_id = str(issued.record.id)

    application = FastAPI()
    application.include_router(platform_routes.router)

    @application.get("/tenant-only")
    def tenant_only(
        _principal=Depends(security.require_authenticated),
    ) -> dict[str, bool]:
        return {"tenant": True}

    return TestClient(application), session_scope, token, credential_id


def test_platform_support_is_a_separate_realm_with_metadata_only_access(
    platform_control_plane,
):
    client, session_scope, token, _credential_id = platform_control_plane
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/platform/me", headers=headers)
    assert me.status_code == 200
    assert me.json() == {
        "subject": "support@example.com",
        "role": "support",
        "realm": "platform",
    }
    organizations = client.get("/platform/organizations", headers=headers)
    assert organizations.status_code == 200
    assert [item["id"] for item in organizations.json()] == [
        "tenant-a",
        "tenant-b",
    ]
    assert "members" not in organizations.text

    assert client.get("/tenant-only", headers=headers).status_code == 403
    assert (
        client.get(
            "/platform/organizations",
            headers={"X-API-Key": TENANT_KEY},
        ).status_code
        == 403
    )

    suspended = client.patch(
        "/platform/organizations/tenant-b/status",
        headers=headers,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    with session_scope() as session:
        assert session.get(Organization, "tenant-b").status == "suspended"
        event = session.query(PlatformAuditEvent).filter_by(
            action="organization_status_updated",
        ).one()
        assert event.before_state == {"status": "active"}
        assert event.after_state == {"status": "suspended"}


def test_platform_credential_rotation_revokes_token_and_session(
    platform_control_plane,
):
    client, session_scope, token, credential_id = platform_control_plane
    principal = security.authenticate_token(token)
    assert principal is not None
    assert principal.realm == "platform"
    session_token = security.issue_session_token(principal, 3_600)
    client.cookies.set(security.SESSION_COOKIE_NAME, session_token)
    assert client.get("/platform/me").status_code == 200

    rotated = client.post(
        f"/platform/credentials/{credential_id}/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "rotated support workstation"},
    )

    assert rotated.status_code == 201
    replacement = rotated.json()["token"]
    assert replacement.startswith("dps.")
    assert security.authenticate_token(token) is None
    assert security.authenticate_token(session_token) is None
    assert client.get("/platform/me").status_code == 401
    replacement_principal = security.authenticate_token(replacement)
    assert replacement_principal is not None
    assert replacement_principal.realm == "platform"
    with session_scope() as session:
        assert session.query(PlatformCredential).filter_by(
            status="active",
        ).count() == 1
        audit = session.query(PlatformAuditEvent).filter_by(
            action="platform_credential_rotated",
        ).one()
        assert token not in str(audit.after_state)
        assert replacement not in str(audit.after_state)


def test_operator_suspension_revokes_all_support_access_and_reactivation(
    platform_control_plane,
    monkeypatch,
    capsys,
):
    _client, session_scope, token, _credential_id = platform_control_plane
    monkeypatch.setattr(platform_tool, "get_session", session_scope)

    assert platform_tool.main(
        [
            "--action",
            "suspend",
            "--subject",
            "support@example.com",
            "--actor-subject",
            "security-operator",
        ]
    ) == 0
    assert security.authenticate_token(token) is not None

    assert platform_tool.main(
        [
            "--action",
            "suspend",
            "--subject",
            "support@example.com",
            "--actor-subject",
            "security-operator",
            "--apply",
        ]
    ) == 0

    assert security.authenticate_token(token) is None
    with session_scope() as session:
        member = session.query(PlatformMember).one()
        assert member.status == "suspended"
        assert member.auth_version == 2
        assert session.query(PlatformCredential).filter_by(
            status="active",
        ).count() == 0
        assert session.query(PlatformAuditEvent).filter_by(
            action="platform_member_suspended",
        ).count() == 1

    assert platform_tool.main(
        [
            "--action",
            "reactivate",
            "--subject",
            "support@example.com",
            "--credential-name",
            "post-incident workstation",
            "--actor-subject",
            "security-operator",
            "--apply",
        ]
    ) == 0

    assert '"token": "dps.' in capsys.readouterr().out
    with session_scope() as session:
        member = session.query(PlatformMember).one()
        assert member.status == "active"
        assert member.auth_version == 3
        assert session.query(PlatformCredential).filter_by(
            status="active",
        ).count() == 1
        assert session.query(PlatformCredential).filter_by(
            status="revoked",
        ).count() == 1
        assert session.query(PlatformAuditEvent).filter_by(
            action="platform_member_reactivated",
        ).count() == 1
