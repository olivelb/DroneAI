"""Scope mission and tile identities to their durable tenant parent.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("missions_vol_id_key", "missions", type_="unique")
    op.create_unique_constraint(
        "uq_missions_organization_vol_id",
        "missions",
        ["organization_id", "vol_id"],
    )
    op.drop_constraint(
        "uq_processed_tile_vol_index",
        "processed_tiles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_processed_tile_mission_index",
        "processed_tiles",
        ["mission_id", "tile_index"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_mission = (
        connection.execute(
            text("SELECT vol_id, COUNT(*) AS duplicate_count FROM missions GROUP BY vol_id HAVING COUNT(*) > 1 LIMIT 1")
        )
        .mappings()
        .first()
    )
    if duplicate_mission is not None:
        raise RuntimeError(
            "Cannot downgrade 0035: tenant-scoped mission identity has "
            f"duplicate vol_id {duplicate_mission['vol_id']!r} across "
            f"{duplicate_mission['duplicate_count']} rows. Restore the "
            "application/schema forward instead of applying a destructive "
            "database downgrade."
        )
    duplicate_tile = (
        connection.execute(
            text(
                "SELECT vol_id, tile_index, COUNT(*) AS duplicate_count "
                "FROM processed_tiles GROUP BY vol_id, tile_index "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .mappings()
        .first()
    )
    if duplicate_tile is not None:
        raise RuntimeError(
            "Cannot downgrade 0035: tenant-scoped tile identity has duplicate "
            f"({duplicate_tile['vol_id']!r}, {duplicate_tile['tile_index']}) "
            f"across {duplicate_tile['duplicate_count']} rows. Restore the "
            "application/schema forward instead of applying a destructive "
            "database downgrade."
        )
    op.drop_constraint(
        "uq_processed_tile_mission_index",
        "processed_tiles",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_processed_tile_vol_index",
        "processed_tiles",
        ["vol_id", "tile_index"],
    )
    op.drop_constraint(
        "uq_missions_organization_vol_id",
        "missions",
        type_="unique",
    )
    op.create_unique_constraint(
        "missions_vol_id_key",
        "missions",
        ["vol_id"],
    )
