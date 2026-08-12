"""Add an isolated durable platform-support identity realm.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_SETTING = (
    "NULLIF(current_setting('droneai.platform_credential_id', true), '')"
)
PLATFORM_IDENTITY = "droneai_platform_identity()"


def upgrade() -> None:
    op.create_table(
        "platform_members",
        sa.Column("id", sa.String(length=36), nullable=False),
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
            name="ck_platform_members_auth_version",
        ),
        sa.CheckConstraint(
            "role IN ('support')",
            name="ck_platform_members_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_platform_members_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject", name="uq_platform_members_subject"),
    )
    op.create_index(
        "ix_platform_members_subject",
        "platform_members",
        ["subject"],
    )
    op.create_table(
        "platform_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
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
            name="ck_platform_credentials_status",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["platform_members.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_id"],
            ["platform_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_credentials_member_id",
        "platform_credentials",
        ["member_id"],
    )
    op.create_index(
        "ix_platform_credentials_member_status",
        "platform_credentials",
        ["member_id", "status"],
    )
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=256), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('platform_member_provisioned', "
            "'platform_member_suspended', 'platform_member_reactivated', "
            "'platform_credential_created', 'platform_credential_revoked', "
            "'platform_credential_rotated', 'organization_status_updated')",
            name="ck_platform_audit_action",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_audit_event_id",
        "platform_audit_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_platform_audit_action_created",
        "platform_audit_events",
        ["action", "created_at"],
    )
    op.create_index(
        "ix_platform_audit_target_created",
        "platform_audit_events",
        ["target_type", "target_id", "created_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        f"""
        CREATE FUNCTION droneai_platform_identity()
        RETURNS TABLE (
            platform_member_id text,
            platform_subject text,
            platform_role text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT member.id::text, member.subject::text, member.role::text
            FROM public.platform_credentials AS credential
            JOIN public.platform_members AS member
              ON member.id = credential.member_id
            WHERE credential.id = {PLATFORM_SETTING}
              AND credential.status = 'active'
              AND (
                credential.expires_at IS NULL
                OR credential.expires_at > CURRENT_TIMESTAMP
              )
              AND member.status = 'active'
              AND member.role = 'support'
            LIMIT 1
        $$
        """
    )
    op.execute("ALTER TABLE platform_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY platform_identity ON platform_members "
        f"USING (id = (SELECT platform_member_id FROM {PLATFORM_IDENTITY})) "
        f"WITH CHECK (id = (SELECT platform_member_id FROM {PLATFORM_IDENTITY}))"
    )
    op.execute("ALTER TABLE platform_credentials ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY platform_identity ON platform_credentials "
        f"USING (member_id = (SELECT platform_member_id FROM {PLATFORM_IDENTITY})) "
        f"WITH CHECK (member_id = (SELECT platform_member_id FROM {PLATFORM_IDENTITY}))"
    )
    op.execute("ALTER TABLE platform_audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY platform_identity ON platform_audit_events "
        f"USING (EXISTS (SELECT 1 FROM {PLATFORM_IDENTITY})) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM {PLATFORM_IDENTITY}))"
    )
    op.execute(
        "CREATE POLICY platform_support_select ON organizations FOR SELECT "
        f"USING (EXISTS (SELECT 1 FROM {PLATFORM_IDENTITY}))"
    )
    op.execute(
        "CREATE POLICY platform_support_update ON organizations FOR UPDATE "
        f"USING (EXISTS (SELECT 1 FROM {PLATFORM_IDENTITY})) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM {PLATFORM_IDENTITY}))"
    )
    op.execute(
        """
        CREATE FUNCTION restrict_platform_organization_update()
        RETURNS trigger AS $$
        BEGIN
            IF NULLIF(
                current_setting('droneai.platform_credential_id', true),
                ''
            ) IS NOT NULL AND (
                NEW.id IS DISTINCT FROM OLD.id
                OR NEW.display_name IS DISTINCT FROM OLD.display_name
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            ) THEN
                RAISE EXCEPTION
                    'platform support may only update organization status';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_platform_organization_update_scope
        BEFORE UPDATE ON organizations
        FOR EACH ROW EXECUTE FUNCTION restrict_platform_organization_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_platform_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'platform_audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_platform_audit_append_only
        BEFORE UPDATE OR DELETE ON platform_audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_platform_audit_mutation()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_platform_organization_update_scope "
            "ON organizations"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS restrict_platform_organization_update()"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_platform_audit_append_only "
            "ON platform_audit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_platform_audit_mutation()")
        op.execute(
            "DROP POLICY IF EXISTS platform_support_update ON organizations"
        )
        op.execute(
            "DROP POLICY IF EXISTS platform_support_select ON organizations"
        )
        for table in (
            "platform_audit_events",
            "platform_credentials",
            "platform_members",
        ):
            op.execute(f'DROP POLICY IF EXISTS platform_identity ON "{table}"')
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute("DROP FUNCTION IF EXISTS droneai_platform_identity()")
    op.drop_index(
        "ix_platform_audit_target_created",
        table_name="platform_audit_events",
    )
    op.drop_index(
        "ix_platform_audit_action_created",
        table_name="platform_audit_events",
    )
    op.drop_index(
        "ix_platform_audit_event_id",
        table_name="platform_audit_events",
    )
    op.drop_table("platform_audit_events")
    op.drop_index(
        "ix_platform_credentials_member_status",
        table_name="platform_credentials",
    )
    op.drop_index(
        "ix_platform_credentials_member_id",
        table_name="platform_credentials",
    )
    op.drop_table("platform_credentials")
    op.drop_index(
        "ix_platform_members_subject",
        table_name="platform_members",
    )
    op.drop_table("platform_members")
