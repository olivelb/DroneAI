"""Add one-time tenant invitations and self-issued recovery capabilities.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CAPABILITY_SETTING = (
    "NULLIF(current_setting('droneai.identity_capability_id', true), '')"
)
CAPABILITY_IDENTITY = "droneai_identity_capability()"


def upgrade() -> None:
    op.drop_constraint(
        "ck_identity_audit_action",
        "identity_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_identity_audit_action",
        "identity_audit_events",
        "action IN ('organization_bootstrapped', 'organization_updated', "
        "'member_created', 'member_updated', 'credential_created', "
        "'credential_revoked', 'credential_rotated', 'invitation_created', "
        "'invitation_revoked', 'invitation_accepted', 'recovery_created', "
        "'recovery_revoked', 'recovery_redeemed')",
    )
    op.create_table(
        "identity_capabilities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=256), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('invitation', 'recovery')",
            name="ck_identity_capabilities_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'redeemed', 'revoked')",
            name="ck_identity_capabilities_status",
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'operator', 'admin')",
            name="ck_identity_capabilities_role",
        ),
        sa.CheckConstraint(
            "(purpose = 'invitation' AND member_id IS NULL) "
            "OR (purpose = 'recovery' AND member_id IS NOT NULL)",
            name="ck_identity_capabilities_member_purpose",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_capabilities_organization_id",
        "identity_capabilities",
        ["organization_id"],
    )
    op.create_index(
        "ix_identity_capabilities_member_id",
        "identity_capabilities",
        ["member_id"],
    )
    op.create_index(
        "ix_identity_capabilities_org_purpose_status",
        "identity_capabilities",
        ["organization_id", "purpose", "status"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        f"""
        CREATE FUNCTION droneai_identity_capability()
        RETURNS TABLE (
            capability_id text,
            capability_organization_id text,
            capability_member_id text,
            capability_purpose text,
            capability_subject text,
            capability_role text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT capability.id::text,
                   capability.organization_id::text,
                   capability.member_id::text,
                   capability.purpose::text,
                   capability.subject::text,
                   capability.role::text
            FROM public.identity_capabilities AS capability
            WHERE capability.id = {CAPABILITY_SETTING}
            LIMIT 1
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION droneai_identity_capability_member(p_member_id text)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.identity_capabilities AS capability
                JOIN public.organization_members AS member
                  ON member.id = p_member_id
                 AND member.organization_id = capability.organization_id
                WHERE capability.id = {CAPABILITY_SETTING}
                  AND (
                    (capability.purpose = 'invitation'
                     AND capability.member_id IS NULL
                     AND member.subject = capability.subject
                     AND member.role = capability.role)
                    OR (capability.purpose = 'recovery'
                        AND member.id = capability.member_id
                        AND member.subject = capability.subject)
                  )
            )
        $$
        """
    )
    op.execute("ALTER TABLE identity_capabilities ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON identity_capabilities "
        "USING (organization_id = NULLIF(current_setting("
        "'droneai.organization_id', true), '') "
        f"OR id = {CAPABILITY_SETTING}) "
        "WITH CHECK (organization_id = NULLIF(current_setting("
        "'droneai.organization_id', true), '') "
        f"OR id = {CAPABILITY_SETTING})"
    )
    op.execute(
        "CREATE POLICY identity_capability_select ON organizations FOR SELECT "
        "USING (id = (SELECT capability_organization_id "
        f"FROM {CAPABILITY_IDENTITY}))"
    )
    member_expression = (
        "EXISTS (SELECT 1 FROM "
        f"{CAPABILITY_IDENTITY} AS capability "
        "WHERE organization_members.organization_id = "
        "capability.capability_organization_id "
        "AND ((capability.capability_purpose = 'invitation' "
        "AND organization_members.subject = capability.capability_subject "
        "AND organization_members.role = capability.capability_role) "
        "OR (capability.capability_purpose = 'recovery' "
        "AND organization_members.id = capability.capability_member_id)))"
    )
    op.execute(
        "CREATE POLICY identity_capability_access ON organization_members "
        f"USING ({member_expression}) WITH CHECK ({member_expression})"
    )
    credential_expression = (
        "droneai_identity_capability_member(api_credentials.member_id)"
    )
    op.execute(
        "CREATE POLICY identity_capability_access ON api_credentials "
        f"USING ({credential_expression}) WITH CHECK ({credential_expression})"
    )
    audit_expression = (
        "organization_id = (SELECT capability_organization_id "
        f"FROM {CAPABILITY_IDENTITY})"
    )
    op.execute(
        "CREATE POLICY identity_capability_access ON identity_audit_events "
        f"USING ({audit_expression}) WITH CHECK ({audit_expression})"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS identity_capability_access "
            "ON identity_audit_events"
        )
        op.execute(
            "DROP POLICY IF EXISTS identity_capability_access "
            "ON api_credentials"
        )
        op.execute(
            "DROP POLICY IF EXISTS identity_capability_access "
            "ON organization_members"
        )
        op.execute(
            "DROP POLICY IF EXISTS identity_capability_select ON organizations"
        )
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation ON identity_capabilities"
        )
        op.execute("ALTER TABLE identity_capabilities DISABLE ROW LEVEL SECURITY")
        op.execute(
            "DROP FUNCTION IF EXISTS droneai_identity_capability_member(text)"
        )
        op.execute("DROP FUNCTION IF EXISTS droneai_identity_capability()")
    op.drop_index(
        "ix_identity_capabilities_org_purpose_status",
        table_name="identity_capabilities",
    )
    op.drop_index(
        "ix_identity_capabilities_member_id",
        table_name="identity_capabilities",
    )
    op.drop_index(
        "ix_identity_capabilities_organization_id",
        table_name="identity_capabilities",
    )
    op.drop_table("identity_capabilities")
    # Keep the expanded action constraint. Capability lifecycle events are
    # append-only evidence and may already exist, so narrowing the constraint
    # would either make rollback fail or require destructive audit deletion.
