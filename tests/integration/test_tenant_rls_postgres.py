"""PostgreSQL-only tenant RLS and transaction-context invariants."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import NoResultFound

import shared.database as database
from shared.database import (
    AccessAuditEvent,
    ApiCredential,
    IdentityCapability,
    Mission,
    MissionArtifact,
    MissionLog,
    MissionStageRun,
    Organization,
    OrganizationMember,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
    OutboxEvent,
    PlatformAuditEvent,
    PlatformCredential,
    PlatformMember,
    ProcessedTile,
)
from shared.access_audit import append_access_audit_event
from shared.identity import append_audit_event, issue_credential
from shared.identity_capabilities import (
    authenticate_capability,
    issue_capability,
    redeem_capability,
)
from shared.platform_identity import (
    append_platform_audit_event,
    issue_platform_credential,
    revoke_platform_credential,
)
from shared.stage_execution import load_stage_execution_context
from shared.tenancy import LEGACY_ORGANIZATION_ID, mission_prefix

security = importlib.import_module("app4-dashboard.api.security")


PROTECTED_TABLES = (
    "organizations",
    "organization_members",
    "api_credentials",
    "identity_audit_events",
    "access_audit_events",
    "identity_capabilities",
    "organization_saas_policies",
    "organization_request_buckets",
    "organization_usage_events",
    "platform_members",
    "platform_credentials",
    "platform_audit_events",
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
def test_mission_and_tile_identity_are_tenant_scoped() -> None:
    suffix = uuid4().hex[:12]
    shared_vol_id = f"shared-flight-{suffix}"
    organization_a = f"identity-a-{suffix}"
    organization_b = f"identity-b-{suffix}"

    with database.get_session() as session:
        session.add_all(
            [
                Organization(
                    id=organization_a,
                    display_name="Identity tenant A",
                    status="active",
                    created_by="integration",
                    updated_by="integration",
                ),
                Organization(
                    id=organization_b,
                    display_name="Identity tenant B",
                    status="active",
                    created_by="integration",
                    updated_by="integration",
                ),
            ]
        )
        session.flush()
        mission_a = Mission(
            vol_id=shared_vol_id,
            organization_id=organization_a,
            owner_subject="operator-a",
            workspace_prefix=mission_prefix(organization_a, shared_vol_id),
        )
        mission_b = Mission(
            vol_id=shared_vol_id,
            organization_id=organization_b,
            owner_subject="operator-b",
            workspace_prefix=mission_prefix(organization_b, shared_vol_id),
        )
        session.add_all([mission_a, mission_b])
        session.flush()
        session.add_all(
            [
                ProcessedTile(
                    mission_id=mission_a.id,
                    vol_id=shared_vol_id,
                    tile_index=0,
                    detection_count=0,
                ),
                ProcessedTile(
                    mission_id=mission_b.id,
                    vol_id=shared_vol_id,
                    tile_index=0,
                    detection_count=0,
                ),
            ]
        )
        session.flush()

        assert mission_a.id != mission_b.id
        assert (
            session.query(ProcessedTile)
            .filter(ProcessedTile.mission_id.in_([mission_a.id, mission_b.id]))
            .count()
            == 2
        )
        session.rollback()


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
    legacy_mission = f"rls-legacy-mission-{suffix}"
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
        platform_member = PlatformMember(
            subject=f"support-{suffix}@example.com",
            role="support",
            status="active",
            created_by="integration",
            updated_by="integration",
        )
        session.add(platform_member)
        session.flush()
        platform_issued = issue_platform_credential(
            session,
            member=platform_member,
            name="RLS support credential",
            actor_subject="integration",
        )
        platform_credential_id = str(platform_issued.record.id)
        platform_token = platform_issued.token
        invited_subject = f"invited-{suffix}"
        capability_issued = issue_capability(
            session,
            organization_id=organization_a,
            purpose="invitation",
            subject=invited_subject,
            role="operator",
            actor_subject=member_a.subject,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        capability_id = str(capability_issued.record.id)
        capability_token = capability_issued.token
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
        historical_mission = Mission(
            vol_id=legacy_mission,
            organization_id=LEGACY_ORGANIZATION_ID,
            owner_subject="legacy-operator",
            workspace_prefix=mission_prefix(
                LEGACY_ORGANIZATION_ID,
                legacy_mission,
            ),
        )
        session.add_all([first_mission, second_mission, historical_mission])
        session.flush()
        append_access_audit_event(
            session,
            organization_id=organization_a,
            actor_subject=member_a.subject,
            actor_role="admin",
            actor_realm="tenant",
            actor_member_id=member_a.id,
            actor_credential_id=credential_a,
            action="detail",
            target_owner_subject=f"other-member-a-{suffix}",
            resource_type="mission",
            resource_id=mission_a,
        )
        append_access_audit_event(
            session,
            organization_id=organization_b,
            actor_subject=member_b.subject,
            actor_role="admin",
            actor_realm="tenant",
            actor_member_id=member_b.id,
            actor_credential_id=credential_b,
            action="list",
            target_owner_subject=f"other-member-b-{suffix}",
            resource_type="dataset",
            resource_id=None,
        )
        session.add_all(
            [
                OrganizationSaasPolicy(
                    organization_id=organization_a,
                    storage_limit_bytes=1_000,
                    version=1,
                    created_by="integration",
                    updated_by="integration",
                ),
                OrganizationSaasPolicy(
                    organization_id=organization_b,
                    storage_limit_bytes=2_000,
                    version=1,
                    created_by="integration",
                    updated_by="integration",
                ),
                OrganizationUsageEvent(
                    organization_id=organization_a,
                    action="policy_updated",
                    resource_type="organization_policy",
                    resource_id=organization_a,
                    actor_subject="integration",
                    quantity=1,
                    unit="policy_version",
                ),
                OrganizationUsageEvent(
                    organization_id=organization_b,
                    action="policy_updated",
                    resource_type="organization_policy",
                    resource_id=organization_b,
                    actor_subject="integration",
                    quantity=1,
                    unit="policy_version",
                ),
            ]
        )
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
        database_name = session.scalar(text("SELECT current_database()"))
        quoted_database = session.get_bind().dialect.identifier_preparer.quote(database_name)
        session.execute(text(f'GRANT CONNECT ON DATABASE {quoted_database} TO "{role}"'))
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
                "GRANT EXECUTE ON FUNCTION droneai_platform_identity(), "
                "droneai_identity_capability(), "
                f'droneai_identity_capability_member(text) TO "{role}"'
            )
        )
        session.execute(
            text(
                f'CREATE ROLE "{stage_role}" LOGIN PASSWORD :password '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ),
            {"password": stage_password},
        )
        session.execute(text(f'GRANT CONNECT ON DATABASE {quoted_database} TO "{stage_role}"'))
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
            assert session.query(OrganizationSaasPolicy).count() == 1
            assert session.query(OrganizationUsageEvent).count() == 1
            assert session.query(AccessAuditEvent).count() == 1

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

        with database.get_session(
            platform_credential_id=platform_credential_id,
        ) as session:
            assert {item.id for item in session.query(Organization).all()} >= {
                organization_a,
                organization_b,
            }
            assert [
                item.id for item in session.query(PlatformMember).all()
            ] == [platform_member.id]
            assert [
                item.id for item in session.query(PlatformCredential).all()
            ] == [platform_credential_id]
            assert session.query(Mission).count() == 0
            assert session.query(OrganizationMember).count() == 0
            assert session.query(ApiCredential).count() == 0
            assert session.query(OrganizationSaasPolicy).count() == 0
            assert session.query(OrganizationUsageEvent).count() == 0
            assert session.query(AccessAuditEvent).count() == 0
            assert session.query(OutboxEvent).count() == 0
            target = session.get(Organization, organization_b)
            assert target is not None
            target.status = "suspended"
            session.add(
                PlatformAuditEvent(
                    actor_subject=platform_member.subject,
                    action="organization_status_updated",
                    target_type="organization",
                    target_id=organization_b,
                    before_state={"status": "active"},
                    after_state={"status": "suspended"},
                )
            )

        platform_principal = security.authenticate_api_key(platform_token)
        assert platform_principal is not None
        assert platform_principal.realm == "platform"
        assert platform_principal.role == "support"
        assert platform_principal.organization_id == "platform-control"
        with database.get_session(
            platform_credential_id=platform_credential_id,
        ) as session:
            current_credential = session.get(
                PlatformCredential,
                platform_credential_id,
            )
            current_member = session.get(PlatformMember, platform_member.id)
            assert current_credential is not None
            assert current_member is not None
            replacement = issue_platform_credential(
                session,
                member=current_member,
                name="RLS rotated support credential",
                actor_subject=current_member.subject,
                rotated_from_id=platform_credential_id,
            )
            revoked_at = datetime.now(UTC)
            append_platform_audit_event(
                session,
                actor_subject=current_member.subject,
                action="platform_credential_rotated",
                target_type="platform_credential",
                target_id=platform_credential_id,
                after_state={
                    "status": "revoked",
                    "replacement_id": replacement.record.id,
                },
            )
            session.flush()
            revoke_platform_credential(
                current_credential,
                actor_subject=current_member.subject,
                reason="rotated",
                revoked_at=revoked_at,
            )
            session.flush()
            replacement_platform_token = replacement.token
            replacement_platform_credential_id = replacement.record.id
        assert security.authenticate_api_key(platform_token) is None
        replacement_platform_principal = security.authenticate_api_key(
            replacement_platform_token
        )
        assert replacement_platform_principal is not None
        assert replacement_platform_principal.realm == "platform"
        with pytest.raises(DBAPIError):
            with database.get_session(
                platform_credential_id=replacement_platform_credential_id,
            ) as session:
                target = session.get(Organization, organization_b)
                assert target is not None
                target.display_name = "Support must not rename customers"
                session.flush()

        with database.get_session(
            identity_capability_id=capability_id,
        ) as session:
            capability = authenticate_capability(
                session,
                capability_token,
                lock=True,
            )
            assert capability is not None
            assert [item.id for item in session.query(Organization).all()] == [
                organization_a
            ]
            assert session.query(Mission).count() == 0
            invited_member = OrganizationMember(
                organization_id=organization_a,
                subject=invited_subject,
                role="operator",
                status="active",
                created_by=member_a.subject,
                updated_by=member_a.subject,
            )
            session.add(invited_member)
            session.flush()
            invited_credential = issue_credential(
                session,
                member=invited_member,
                name="invited credential",
                actor_subject=invited_subject,
            )
            capability_context = session.execute(
                text(
                    "SELECT current_setting("
                    "'droneai.identity_capability_id', true), "
                    "(SELECT capability_organization_id "
                    "FROM droneai_identity_capability())"
                )
            ).one()
            assert capability_context == (capability_id, organization_a)
            append_audit_event(
                session,
                organization_id=organization_a,
                actor_subject=invited_subject,
                action="invitation_accepted",
                target_type="identity_capability",
                target_id=capability_id,
                after_state={"status": "redeemed"},
            )
            session.flush()
            redeem_capability(capability)
            session.flush()
            invited_token = invited_credential.token

        invited_principal = security.authenticate_api_key(invited_token)
        assert invited_principal is not None
        assert invited_principal.organization_id == organization_a
        assert invited_principal.role == "operator"

        principal = security.authenticate_api_key(token_a)
        assert principal is not None
        assert principal.organization_id == organization_a
        browser_session = security.issue_session_token(principal, 60)
        assert security.authenticate_token(browser_session) == principal

        assert database.get_mission_audience(mission_b) is None
        assert database.get_mission_audience(mission_b, organization_b) == (
            organization_b,
            member_b.subject,
        )
        assert database.get_mission_audience(mission_b, organization_a) is None
        assert database.get_mission_audience(legacy_mission) == (
            LEGACY_ORGANIZATION_ID,
            "legacy-operator",
        )
        with database.get_session() as session:
            assert (
                session.execute(
                    text(
                        "SELECT to_regprocedure("
                        "'droneai_mission_audience(text)')"
                    )
                ).scalar_one()
                is None
            )

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
                usage_event = session.query(OrganizationUsageEvent).one()
                usage_event.quantity = 999
                session.flush()
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
