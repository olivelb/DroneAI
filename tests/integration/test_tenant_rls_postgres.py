"""PostgreSQL-only tenant RLS and transaction-context invariants."""

from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import NoResultFound

import shared.database as database
from shared.database import (
    ApiCredential,
    Mission,
    MissionArtifact,
    MissionLog,
    MissionStageRun,
    Organization,
    OrganizationMember,
    OutboxEvent,
)
from shared.identity import issue_credential
from shared.stage_execution import load_stage_execution_context
from shared.tenancy import mission_prefix

security = importlib.import_module("app4-dashboard.api.security")


PROTECTED_TABLES = (
    "organizations",
    "organization_members",
    "api_credentials",
    "identity_audit_events",
    "dataset_upload_sessions",
    "dataset_upload_files",
    "datasets",
    "missions",
    "mission_stage_runs",
    "detection_shard_receipts",
    "mission_artifacts",
    "mission_artifact_parents",
    "detections",
    "processed_tiles",
    "ai_analysis_runs",
    "ai_analysis_tiles",
    "map_features",
    "map_feature_audit_events",
    "gcp_sets",
    "gcp_points",
    "gcp_observations",
    "gcp_audit_events",
    "raster_layer_styles",
    "mission_logs",
    "outbox_events",
)


@pytest.mark.integration
def test_non_owner_role_is_fail_closed_and_transaction_scoped(monkeypatch) -> None:
    suffix = uuid4().hex[:12]
    role = f"droneai_rls_{suffix}"
    password = f"rls-test-{uuid4().hex}"
    stage_role = f"droneai_stage_{suffix}"
    stage_password = f"stage-test-{uuid4().hex}"
    organization_a = f"rls-a-{suffix}"
    organization_b = f"rls-b-{suffix}"
    credential_b = str(uuid4())
    mission_a = f"rls-mission-a-{suffix}"
    mission_b = f"rls-mission-b-{suffix}"
    run_a = str(uuid4())
    run_b = str(uuid4())
    owner_url = database.DATABASE_URL
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv(
        "DRONEAI_CREDENTIAL_PEPPER",
        "rls-integration-credential-pepper-at-least-32-characters",
    )
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "rls-integration-session-secret-at-least-32-characters",
    )

    with database.get_session() as session:
        org_a = Organization(
            id=organization_a,
            display_name="RLS tenant A",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        org_b = Organization(
            id=organization_b,
            display_name="RLS tenant B",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        member_a = OrganizationMember(
            organization_id=organization_a,
            subject=f"member-a-{suffix}",
            role="admin",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        member_b = OrganizationMember(
            organization_id=organization_b,
            subject=f"member-b-{suffix}",
            role="admin",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        session.add_all([org_a, org_b, member_a, member_b])
        session.flush()
        issued_a = issue_credential(
            session,
            member=member_a,
            name="RLS credential A",
            actor_subject="integration",
        )
        credential_a = issued_a.record.id
        token_a = issued_a.token
        session.add(
            ApiCredential(
                id=credential_b,
                organization_id=organization_b,
                member_id=member_b.id,
                name="RLS credential B",
                secret_hash="b" * 64,
                status="active",
                created_by="integration",
            )
        )
        first_mission = Mission(
            vol_id=mission_a,
            organization_id=organization_a,
            owner_subject=member_a.subject,
            workspace_prefix=mission_prefix(organization_a, mission_a),
        )
        second_mission = Mission(
            vol_id=mission_b,
            organization_id=organization_b,
            owner_subject=member_b.subject,
            workspace_prefix=mission_prefix(organization_b, mission_b),
        )
        session.add_all([first_mission, second_mission])
        session.flush()
        session.add_all(
            [
                MissionStageRun(
                    run_id=run_a,
                    mission_id=first_mission.id,
                    stage="reconstruction",
                    attempt=0,
                    status="queued",
                    executor="kubernetes-job",
                    resource_class="gpu-geometry",
                    idempotency_key=uuid4().hex * 2,
                ),
                MissionStageRun(
                    run_id=run_b,
                    mission_id=second_mission.id,
                    stage="reconstruction",
                    attempt=0,
                    status="queued",
                    executor="kubernetes-job",
                    resource_class="gpu-geometry",
                    idempotency_key=uuid4().hex * 2,
                ),
            ]
        )
        session.add_all(
            [
                MissionLog(
                    mission_id=first_mission.id,
                    vol_id=mission_a,
                    status="processing",
                ),
                MissionLog(
                    mission_id=second_mission.id,
                    vol_id=mission_b,
                    status="processing",
                ),
                OutboxEvent(
                    organization_id=organization_a,
                    event_id=f"rls-outbox-a-{suffix}",
                    event_type="control",
                    topic="pipeline-control",
                    payload={"event_type": "control", "vol_id": mission_a},
                ),
                OutboxEvent(
                    organization_id=organization_b,
                    event_id=f"rls-outbox-b-{suffix}",
                    event_type="control",
                    topic="pipeline-control",
                    payload={"event_type": "control", "vol_id": mission_b},
                ),
            ]
        )

    with database.get_session() as session:
        session.execute(
            text(
                f'CREATE ROLE "{role}" LOGIN PASSWORD :password '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ),
            {"password": password},
        )
        session.execute(text(f'GRANT CONNECT ON DATABASE droneai TO "{role}"'))
        session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        session.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'
            )
        )
        session.execute(
            text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        )
        session.execute(
            text(
                f'CREATE ROLE "{stage_role}" LOGIN PASSWORD :password '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ),
            {"password": stage_password},
        )
        session.execute(text(f'GRANT CONNECT ON DATABASE droneai TO "{stage_role}"'))
        session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{stage_role}"'))
        session.execute(text(f'GRANT SELECT ON missions TO "{stage_role}"'))
        session.execute(
            text(f'GRANT SELECT, UPDATE ON mission_stage_runs TO "{stage_role}"')
        )
        session.execute(
            text(
                f"GRANT SELECT, INSERT ON mission_artifacts, "
                f'mission_artifact_parents, detection_shard_receipts TO "{stage_role}"'
            )
        )
        session.execute(
            text(
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                f'TO "{stage_role}"'
            )
        )

    api_url = make_url(owner_url).set(username=role, password=password)
    stage_url = make_url(owner_url).set(
        username=stage_role,
        password=stage_password,
    )
    database.reset_engine()
    database.DATABASE_URL = api_url.render_as_string(hide_password=False)
    try:
        with database.get_session() as session:
            assert session.query(Organization).count() == 0
            assert session.query(Mission).count() == 0
            assert session.query(OutboxEvent).count() == 0
            for table in PROTECTED_TABLES:
                assert (
                    session.execute(
                        text("SELECT row_security_active(to_regclass(:table))"),
                        {"table": table},
                    ).scalar_one()
                    is True
                )

        with database.get_session(organization_id=organization_a) as session:
            assert [item.id for item in session.query(Organization).all()] == [
                organization_a
            ]
            assert [item.vol_id for item in session.query(Mission).all()] == [mission_a]
            assert session.query(MissionLog).count() == 1
            assert session.query(OutboxEvent).count() == 1
            assert session.query(ApiCredential).count() == 1

        with database.get_session() as session:
            assert session.query(Mission).count() == 0

        with database.get_session(authentication_credential_id=credential_a) as session:
            assert [item.id for item in session.query(ApiCredential).all()] == [
                credential_a
            ]
            assert [item.id for item in session.query(Organization).all()] == [
                organization_a
            ]
            assert [item.id for item in session.query(OrganizationMember).all()] == [
                member_a.id
            ]
            assert session.query(Mission).count() == 0

        principal = security.authenticate_api_key(token_a)
        assert principal is not None
        assert principal.organization_id == organization_a
        browser_session = security.issue_session_token(principal, 60)
        assert security.authenticate_token(browser_session) == principal

        assert database.get_mission_audience(mission_b) == (
            organization_b,
            member_b.subject,
        )
        assert database.get_mission_audience(mission_b, organization_b) == (
            organization_b,
            member_b.subject,
        )
        assert database.get_mission_audience(mission_b, organization_a) is None

        database.reset_engine()
        database.DATABASE_URL = stage_url.render_as_string(hide_password=False)
        monkeypatch.setenv("DRONEAI_STAGE_RLS_REQUIRED", "true")
        context = load_stage_execution_context(
            run_a,
            "reconstruction",
            expected_organization_id=organization_a,
            expected_mission_id=first_mission.id,
            expected_vol_id=mission_a,
            expected_workspace_prefix=mission_prefix(organization_a, mission_a),
            expected_owner_subject=member_a.subject,
        )
        assert context.organization_id == organization_a
        assert context.workspace_prefix == mission_prefix(organization_a, mission_a)
        with pytest.raises(NoResultFound):
            load_stage_execution_context(
                run_b,
                "reconstruction",
                expected_organization_id=organization_a,
            )

        with database.get_session(organization_id=organization_a) as session:
            assert (
                session.query(MissionStageRun)
                .filter(MissionStageRun.run_id == run_b)
                .update({"current_step": "CROSS_TENANT"})
                == 0
            )
        with pytest.raises(DBAPIError):
            with database.get_session(organization_id=organization_a) as session:
                foreign_run = (
                    session.query(MissionStageRun)
                    .filter(MissionStageRun.run_id == run_a)
                    .one()
                )
                session.add(
                    MissionArtifact(
                        artifact_id=str(uuid4()),
                        mission_id=second_mission.id,
                        stage_run_id=foreign_run.id,
                        kind="reconstruction_workspace",
                        uri="s3://drone-ai/forbidden",
                        checksum_sha256="f" * 64,
                    )
                )
                session.flush()

        database.reset_engine()
        database.DATABASE_URL = api_url.render_as_string(hide_password=False)
        with pytest.raises(DBAPIError):
            with database.get_session(organization_id=organization_a) as session:
                session.add(
                    Mission(
                        vol_id=f"rls-cross-write-{suffix}",
                        organization_id=organization_b,
                        owner_subject=member_b.subject,
                    )
                )
                session.flush()
    finally:
        database.reset_engine()
        database.DATABASE_URL = owner_url
        with database.get_session() as session:
            session.execute(text(f'DROP OWNED BY "{stage_role}"'))
            session.execute(text(f'DROP ROLE "{stage_role}"'))
            session.execute(text(f'DROP OWNED BY "{role}"'))
            session.execute(text(f'DROP ROLE "{role}"'))

    with database.get_session() as session:
        assert (
            session.query(Mission)
            .filter(Mission.vol_id.in_([mission_a, mission_b]))
            .count()
            == 2
        )
