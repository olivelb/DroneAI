"""Remove the mission-to-tenant SECURITY DEFINER oracle.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP FUNCTION IF EXISTS droneai_mission_audience(text)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
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
