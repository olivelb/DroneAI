"""Add an append-only audit trail for GCP operator actions.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gcp_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("gcp_set_id", sa.Integer(), nullable=False),
        sa.Column("gcp_point_id", sa.Integer(), nullable=True),
        sa.Column("gcp_observation_id", sa.Integer(), nullable=True),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('imported', 'point_updated', 'observation_updated', "
            "'candidates_refreshed', 'bundle_materialized')",
            name="ck_gcp_audit_action",
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gcp_set_id"], ["gcp_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gcp_point_id"], ["gcp_points.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["gcp_observation_id"],
            ["gcp_observations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gcp_audit_event_id", "gcp_audit_events", ["event_id"], unique=True)
    op.create_index(
        "ix_gcp_audit_set_created",
        "gcp_audit_events",
        ["gcp_set_id", "created_at"],
    )
    op.create_index(
        "ix_gcp_audit_mission_created",
        "gcp_audit_events",
        ["mission_id", "created_at"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_gcp_audit_mutation() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' AND NOT EXISTS (
                    SELECT 1 FROM gcp_sets WHERE id = OLD.gcp_set_id
                ) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'gcp_audit_events is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_gcp_audit_append_only
            BEFORE UPDATE OR DELETE ON gcp_audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_gcp_audit_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_gcp_audit_no_update
            BEFORE UPDATE ON gcp_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'gcp_audit_events is append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_gcp_audit_no_delete
            BEFORE DELETE ON gcp_audit_events
            WHEN EXISTS (SELECT 1 FROM gcp_sets WHERE id = OLD.gcp_set_id)
            BEGIN
                SELECT RAISE(ABORT, 'gcp_audit_events is append-only');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_gcp_audit_append_only ON gcp_audit_events")
        op.execute("DROP FUNCTION IF EXISTS reject_gcp_audit_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_gcp_audit_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_gcp_audit_no_delete")
    op.drop_index("ix_gcp_audit_mission_created", table_name="gcp_audit_events")
    op.drop_index("ix_gcp_audit_set_created", table_name="gcp_audit_events")
    op.drop_index("ix_gcp_audit_event_id", table_name="gcp_audit_events")
    op.drop_table("gcp_audit_events")
