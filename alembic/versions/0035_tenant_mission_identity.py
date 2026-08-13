"""Scope mission and tile identities to their durable tenant parent.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op


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
