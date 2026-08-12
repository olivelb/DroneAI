"""Add durable tenant-scoped cross-member access evidence.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("actor_realm", sa.String(length=32), nullable=False),
        sa.Column("actor_member_id", sa.String(length=36), nullable=True),
        sa.Column("actor_credential_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_owner_subject", sa.String(length=256), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_realm IN ('tenant', 'platform')",
            name="ck_access_audit_actor_realm",
        ),
        sa.CheckConstraint(
            "resource_type IN ('mission', 'dataset')",
            name="ck_access_audit_resource_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('authorized')",
            name="ck_access_audit_outcome",
        ),
        sa.CheckConstraint(
            "length(action) BETWEEN 1 AND 64",
            name="ck_access_audit_action_length",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_audit_event_id",
        "access_audit_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_access_audit_organization_id",
        "access_audit_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_access_audit_org_created",
        "access_audit_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_access_audit_owner_created",
        "access_audit_events",
        ["organization_id", "target_owner_subject", "created_at"],
    )
    op.create_index(
        "ix_access_audit_resource_created",
        "access_audit_events",
        ["resource_type", "resource_id", "created_at"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE access_audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON access_audit_events "
        "USING (organization_id = NULLIF(current_setting("
        "'droneai.organization_id', true), '')) "
        "WITH CHECK (organization_id = NULLIF(current_setting("
        "'droneai.organization_id', true), ''))"
    )
    op.execute(
        """
        CREATE FUNCTION reject_access_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'access_audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_access_audit_append_only
        BEFORE UPDATE OR DELETE ON access_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_access_audit_mutation()
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_access_audit_append_only ON access_audit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_access_audit_mutation()")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON access_audit_events")
        op.execute("ALTER TABLE access_audit_events DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_access_audit_resource_created",
        table_name="access_audit_events",
    )
    op.drop_index(
        "ix_access_audit_owner_created",
        table_name="access_audit_events",
    )
    op.drop_index(
        "ix_access_audit_org_created",
        table_name="access_audit_events",
    )
    op.drop_index(
        "ix_access_audit_organization_id",
        table_name="access_audit_events",
    )
    op.drop_index(
        "ix_access_audit_event_id",
        table_name="access_audit_events",
    )
    op.drop_table("access_audit_events")
