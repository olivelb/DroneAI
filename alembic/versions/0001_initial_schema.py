"""initial schema — missions, detections, mission_logs

Revision ID: 0001
Revises: None
Create Date: 2026-04-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure PostGIS extension is available
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # --- missions ---
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("pipeline", sa.String(32), nullable=False, server_default="modern"),
        sa.Column("input_dataset", sa.String(1024), nullable=True),
        sa.Column("workspace_prefix", sa.String(1024), nullable=True),
        sa.Column("params", JSONB(), nullable=True),
        sa.Column("current_step", sa.String(64), nullable=True),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("service_states", JSONB(), nullable=True),
        sa.Column("resume_info", JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_tiles", sa.Integer(), nullable=True),
        sa.Column("tiles_received", sa.Integer(), server_default="0"),
        sa.Column("ortho_s3_key", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vol_id"),
    )
    op.create_index("ix_missions_vol_id", "missions", ["vol_id"])

    # --- detections ---
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("tile_index", sa.Integer(), nullable=False),
        sa.Column("class_name", sa.String(128), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("pixel_x", sa.Float(), nullable=True),
        sa.Column("pixel_y", sa.Float(), nullable=True),
        sa.Column("geo_lon", sa.Float(), nullable=True),
        sa.Column("geo_lat", sa.Float(), nullable=True),
        sa.Column("segment", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_detections_vol_id", "detections", ["vol_id"])
    op.create_index("ix_detections_class_name", "detections", ["class_name"])
    op.create_index("ix_detections_vol_tile", "detections", ["vol_id", "tile_index"])

    # Add PostGIS geometry column + spatial index
    op.execute(
        "ALTER TABLE detections ADD COLUMN geometry geometry(Polygon, 4326)"
    )
    op.execute(
        "CREATE INDEX ix_detections_geometry ON detections USING GIST (geometry)"
    )

    # --- mission_logs ---
    op.create_table(
        "mission_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("service", sa.String(32), nullable=True),
        sa.Column("step", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_logs_vol_id", "mission_logs", ["vol_id"])
    op.create_index("ix_logs_mission_created", "mission_logs", ["mission_id", "created_at"])


def downgrade() -> None:
    op.drop_table("mission_logs")
    op.drop_table("detections")
    op.drop_table("missions")
