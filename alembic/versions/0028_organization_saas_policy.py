"""Add organization SaaS policies, request buckets, and usage ledger.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORGANIZATION_SETTING = (
    "NULLIF(current_setting('droneai.organization_id', true), '')"
)


def _policy(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING (organization_id = {ORGANIZATION_SETTING}) "
        f"WITH CHECK (organization_id = {ORGANIZATION_SETTING})"
    )


def upgrade() -> None:
    op.create_table(
        "organization_saas_policies",
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("storage_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column("concurrent_stage_runs_limit", sa.Integer(), nullable=True),
        sa.Column("request_rate_per_minute", sa.Integer(), nullable=True),
        sa.Column("request_burst", sa.Integer(), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "storage_limit_bytes IS NULL OR storage_limit_bytes >= 1",
            name="ck_organization_saas_policy_storage_limit",
        ),
        sa.CheckConstraint(
            "concurrent_stage_runs_limit IS NULL "
            "OR concurrent_stage_runs_limit >= 1",
            name="ck_organization_saas_policy_stage_limit",
        ),
        sa.CheckConstraint(
            "request_rate_per_minute IS NULL OR request_rate_per_minute >= 1",
            name="ck_organization_saas_policy_request_rate",
        ),
        sa.CheckConstraint(
            "request_burst IS NULL OR request_burst >= 1",
            name="ck_organization_saas_policy_request_burst",
        ),
        sa.CheckConstraint(
            "(request_rate_per_minute IS NULL) = (request_burst IS NULL)",
            name="ck_organization_saas_policy_request_pair",
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days >= 1",
            name="ck_organization_saas_policy_retention",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_organization_saas_policy_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_table(
        "organization_request_buckets",
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_index(
        "ix_organization_request_buckets_updated_at",
        "organization_request_buckets",
        ["updated_at"],
    )
    op.create_table(
        "organization_usage_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("resource_type", sa.String(length=48), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('policy_updated', 'storage_reserved', "
            "'storage_released', 'stage_scheduled', 'request_throttled', "
            "'retention_deleted', 'retention_failed')",
            name="ck_organization_usage_action",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_usage_events_event_id",
        "organization_usage_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_organization_usage_events_organization_id",
        "organization_usage_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_usage_events_idempotency_key",
        "organization_usage_events",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_organization_usage_org_created",
        "organization_usage_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_organization_usage_resource",
        "organization_usage_events",
        ["organization_id", "resource_type", "resource_id"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in (
        "organization_saas_policies",
        "organization_request_buckets",
        "organization_usage_events",
    ):
        _policy(table)
    op.execute(
        """
        CREATE FUNCTION reject_organization_usage_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'organization_usage_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_usage_append_only
        BEFORE UPDATE OR DELETE ON organization_usage_events
        FOR EACH ROW EXECUTE FUNCTION reject_organization_usage_mutation()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_organization_usage_append_only "
            "ON organization_usage_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS reject_organization_usage_mutation()"
        )
        for table in (
            "organization_usage_events",
            "organization_request_buckets",
            "organization_saas_policies",
        ):
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_organization_usage_resource",
        table_name="organization_usage_events",
    )
    op.drop_index(
        "ix_organization_usage_org_created",
        table_name="organization_usage_events",
    )
    op.drop_index(
        "ix_organization_usage_events_idempotency_key",
        table_name="organization_usage_events",
    )
    op.drop_index(
        "ix_organization_usage_events_organization_id",
        table_name="organization_usage_events",
    )
    op.drop_index(
        "ix_organization_usage_events_event_id",
        table_name="organization_usage_events",
    )
    op.drop_table("organization_usage_events")
    op.drop_index(
        "ix_organization_request_buckets_updated_at",
        table_name="organization_request_buckets",
    )
    op.drop_table("organization_request_buckets")
    op.drop_table("organization_saas_policies")
