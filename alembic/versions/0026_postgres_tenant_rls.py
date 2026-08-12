"""Add PostgreSQL row-level security for organization-owned data.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ORGANIZATION_ID = "legacy-unassigned"
ORGANIZATION_SETTING = (
    "NULLIF(current_setting('droneai.organization_id', true), '')"
)
AUTHENTICATION_SETTING = (
    "NULLIF(current_setting("
    "'droneai.authentication_credential_id', true), '')"
)


def _policy(table: str, expression: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({expression}) WITH CHECK ({expression})"
    )


def _exists(parent: str, alias: str, join: str) -> str:
    return f"EXISTS (SELECT 1 FROM {parent} {alias} WHERE {join})"


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column(
            "organization_id",
            sa.String(length=64),
            nullable=False,
            server_default=LEGACY_ORGANIZATION_ID,
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE outbox_events AS event
                SET organization_id = COALESCE(
                    (
                        SELECT mission.organization_id
                        FROM missions AS mission
                        WHERE mission.vol_id = event.payload ->> 'vol_id'
                    ),
                    NULLIF(event.payload ->> 'organization_id', ''),
                    :legacy
                )
                """
            ),
            {"legacy": LEGACY_ORGANIZATION_ID},
        )
    op.create_foreign_key(
        "fk_outbox_events_organization_id",
        "outbox_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_outbox_events_organization_id",
        "outbox_events",
        ["organization_id"],
    )

    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        CREATE FUNCTION droneai_mission_audience(p_vol_id text)
        RETURNS TABLE (
            audience_organization_id text,
            audience_owner_subject text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT mission.organization_id::text, mission.owner_subject::text
            FROM public.missions AS mission
            WHERE mission.vol_id = p_vol_id
            LIMIT 1
        $$
        """
    )

    _policy(
        "organizations",
        f"id = {ORGANIZATION_SETTING} OR "
        + _exists(
            "api_credentials",
            "credential",
            f"credential.id = {AUTHENTICATION_SETTING} "
            "AND credential.organization_id = organizations.id",
        ),
    )
    _policy(
        "organization_members",
        f"organization_id = {ORGANIZATION_SETTING} OR "
        + _exists(
            "api_credentials",
            "credential",
            f"credential.id = {AUTHENTICATION_SETTING} "
            "AND credential.member_id = organization_members.id",
        ),
    )
    _policy(
        "api_credentials",
        f"organization_id = {ORGANIZATION_SETTING} OR "
        f"id = {AUTHENTICATION_SETTING}",
    )
    for table in (
        "identity_audit_events",
        "dataset_upload_sessions",
        "datasets",
        "missions",
        "outbox_events",
    ):
        _policy(table, f"organization_id = {ORGANIZATION_SETTING}")

    _policy(
        "dataset_upload_files",
        _exists(
            "dataset_upload_sessions",
            "upload_session",
            "upload_session.id = dataset_upload_files.upload_session_id",
        ),
    )
    for table in (
        "ai_analysis_runs",
        "detections",
        "gcp_sets",
        "mission_logs",
        "mission_stage_runs",
        "processed_tiles",
        "gcp_points",
        "map_features",
        "mission_artifacts",
        "map_feature_audit_events",
        "raster_layer_styles",
        "gcp_audit_events",
    ):
        _policy(
            table,
            _exists(
                "missions",
                "mission",
                f"mission.id = {table}.mission_id",
            ),
        )
    _policy(
        "ai_analysis_tiles",
        _exists(
            "ai_analysis_runs",
            "analysis_run",
            "analysis_run.id = ai_analysis_tiles.analysis_run_id",
        ),
    )
    _policy(
        "detection_shard_receipts",
        _exists(
            "mission_stage_runs",
            "stage_run",
            "stage_run.id = detection_shard_receipts.stage_run_id",
        ),
    )
    _policy(
        "gcp_observations",
        _exists(
            "gcp_points",
            "gcp_point",
            "gcp_point.id = gcp_observations.gcp_point_id",
        ),
    )
    _policy(
        "mission_artifact_parents",
        _exists(
            "mission_artifacts",
            "artifact",
            "artifact.id = mission_artifact_parents.artifact_id",
        )
        + " AND "
        + _exists(
            "mission_artifacts",
            "parent_artifact",
            "parent_artifact.id = mission_artifact_parents.parent_artifact_id",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        protected_tables = (
            "mission_artifact_parents",
            "gcp_observations",
            "detection_shard_receipts",
            "ai_analysis_tiles",
            "gcp_audit_events",
            "raster_layer_styles",
            "map_feature_audit_events",
            "mission_artifacts",
            "map_features",
            "gcp_points",
            "processed_tiles",
            "mission_stage_runs",
            "mission_logs",
            "gcp_sets",
            "detections",
            "ai_analysis_runs",
            "dataset_upload_files",
            "outbox_events",
            "missions",
            "datasets",
            "dataset_upload_sessions",
            "identity_audit_events",
            "api_credentials",
            "organization_members",
            "organizations",
        )
        for table in protected_tables:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute("DROP FUNCTION IF EXISTS droneai_mission_audience(text)")
    op.drop_index("ix_outbox_events_organization_id", table_name="outbox_events")
    op.drop_constraint(
        "fk_outbox_events_organization_id",
        "outbox_events",
        type_="foreignkey",
    )
    op.drop_column("outbox_events", "organization_id")
