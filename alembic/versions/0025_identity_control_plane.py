"""Add durable organization members, credentials, and identity audit.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ORGANIZATION_ID = "legacy-unassigned"
LOCAL_ORGANIZATION_ID = "local-development"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_organizations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO organizations (
                id, display_name, status, created_by, updated_by, created_at, updated_at
            )
            SELECT organization_id, organization_id, 'active', 'migration-0025',
                   'migration-0025', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM (
                SELECT organization_id FROM dataset_upload_sessions
                UNION SELECT organization_id FROM datasets
                UNION SELECT organization_id FROM missions
                UNION SELECT :legacy
                UNION SELECT :local
            ) AS known_organizations
            """
        ),
        {"legacy": LEGACY_ORGANIZATION_ID, "local": LOCAL_ORGANIZATION_ID},
    )
    for table in ("dataset_upload_sessions", "datasets", "missions"):
        op.create_foreign_key(
            f"fk_{table}_organization_id",
            table,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "auth_version >= 1",
            name="ck_organization_members_auth_version",
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'operator', 'admin')",
            name="ck_organization_members_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_organization_members_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_members_subject",
        ),
    )
    op.create_index(
        "ix_organization_members_organization_id",
        "organization_members",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_members_org_status_role",
        "organization_members",
        ["organization_id", "status", "role"],
    )

    op.create_table(
        "api_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=256), nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        sa.Column("rotated_from_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_api_credentials_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["organization_members.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_id"],
            ["api_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_api_credentials_organization_id",
        "api_credentials",
        ["organization_id"],
    )
    op.create_index("ix_api_credentials_member_id", "api_credentials", ["member_id"])
    op.create_index(
        "ix_api_credentials_org_member_status",
        "api_credentials",
        ["organization_id", "member_id", "status"],
    )

    op.create_table(
        "identity_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=256), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('organization_bootstrapped', 'organization_updated', "
            "'member_created', 'member_updated', 'credential_created', "
            "'credential_revoked', 'credential_rotated')",
            name="ck_identity_audit_action",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_audit_event_id",
        "identity_audit_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_identity_audit_organization_id",
        "identity_audit_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_identity_audit_org_created",
        "identity_audit_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_identity_audit_target_created",
        "identity_audit_events",
        ["target_type", "target_id", "created_at"],
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_identity_audit_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'identity_audit_events is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_identity_audit_append_only
            BEFORE UPDATE OR DELETE ON identity_audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_identity_audit_mutation()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_identity_audit_append_only "
            "ON identity_audit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_identity_audit_mutation()")
    op.drop_index("ix_identity_audit_target_created", table_name="identity_audit_events")
    op.drop_index("ix_identity_audit_org_created", table_name="identity_audit_events")
    op.drop_index("ix_identity_audit_organization_id", table_name="identity_audit_events")
    op.drop_index("ix_identity_audit_event_id", table_name="identity_audit_events")
    op.drop_table("identity_audit_events")
    op.drop_index("ix_api_credentials_org_member_status", table_name="api_credentials")
    op.drop_index("ix_api_credentials_member_id", table_name="api_credentials")
    op.drop_index("ix_api_credentials_organization_id", table_name="api_credentials")
    op.drop_table("api_credentials")
    op.drop_index(
        "ix_organization_members_org_status_role",
        table_name="organization_members",
    )
    op.drop_index(
        "ix_organization_members_organization_id",
        table_name="organization_members",
    )
    op.drop_table("organization_members")
    for table in ("missions", "datasets", "dataset_upload_sessions"):
        op.drop_constraint(
            f"fk_{table}_organization_id",
            table,
            type_="foreignkey",
        )
    op.drop_table("organizations")
