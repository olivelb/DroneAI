"""durable tile aggregation and geospatial publication state

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("tiling_metadata", JSONB(), nullable=True),
    )
    op.add_column(
        "missions",
        sa.Column(
            "aggregation_status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "missions",
        sa.Column(
            "aggregation_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "outbox_events",
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "processed_tiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("tile_index", sa.Integer(), nullable=False),
        sa.Column(
            "detection_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "vol_id",
            "tile_index",
            name="uq_processed_tile_vol_index",
        ),
    )
    op.create_index(
        "ix_processed_tiles_vol_id",
        "processed_tiles",
        ["vol_id"],
    )
    op.create_index(
        "ix_processed_tiles_mission",
        "processed_tiles",
        ["mission_id", "tile_index"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processed_tiles_mission",
        table_name="processed_tiles",
    )
    op.drop_index(
        "ix_processed_tiles_vol_id",
        table_name="processed_tiles",
    )
    op.drop_table("processed_tiles")
    op.drop_column("outbox_events", "dead_at")
    op.drop_column("missions", "aggregation_completed_at")
    op.drop_column("missions", "aggregation_status")
    op.drop_column("missions", "tiling_metadata")
