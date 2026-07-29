"""AI analysis campaigns and editable geospatial workspace

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(9), nullable=False, server_default="#f43f5e"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("backend", sa.String(32), nullable=False, server_default="yolo"),
        sa.Column("model_variant", sa.String(128), nullable=True),
        sa.Column("prompt", sa.String(256), nullable=True),
        sa.Column("classes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.3"),
        sa.Column("tile_size", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column(
            "persist_results",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="queued"),
        sa.Column("total_tiles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tiles_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detection_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("ortho_s3_key", sa.String(1024), nullable=False),
        sa.Column("result_s3_key", sa.String(1024), nullable=True),
        sa.Column("tiling_metadata", JSONB(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_ai_analysis_runs_run_id", "ai_analysis_runs", ["run_id"], unique=True)
    op.create_index("ix_ai_analysis_runs_vol_id", "ai_analysis_runs", ["vol_id"])
    op.create_index("ix_ai_runs_mission_created", "ai_analysis_runs", ["mission_id", "created_at"])
    op.create_index("ix_ai_runs_recovery", "ai_analysis_runs", ["status", "heartbeat_at"])

    op.create_table(
        "ai_analysis_tiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("tile_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("tile_s3_key", sa.String(1024), nullable=False),
        sa.Column("result_s3_key", sa.String(1024), nullable=True),
        sa.Column("offset_x", sa.Integer(), nullable=False),
        sa.Column("offset_y", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bounds_wgs84", JSONB(), nullable=True),
        sa.Column("detection_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["ai_analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_run_id", "tile_index", name="uq_ai_analysis_tile_run_index"),
    )
    op.create_index("ix_ai_analysis_tiles_status", "ai_analysis_tiles", ["analysis_run_id", "status"])

    op.create_table(
        "map_features",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("feature_id", sa.String(36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=True),
        sa.Column("vol_id", sa.String(256), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column(
            "geometry",
            Geometry("GEOMETRY", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(9), nullable=False, server_default="#10b981"),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("properties", JSONB(), nullable=False, server_default="{}"),
        sa.Column("class_name", sa.String(128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("tile_index", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["ai_analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature_id"),
    )
    op.create_index("ix_map_features_feature_id", "map_features", ["feature_id"], unique=True)
    op.create_index("ix_map_features_vol_id", "map_features", ["vol_id"])
    op.create_index("ix_map_features_class_name", "map_features", ["class_name"])
    op.create_index("ix_map_features_geometry", "map_features", ["geometry"], postgresql_using="gist")
    op.create_index("ix_map_features_mission_source", "map_features", ["mission_id", "source"])
    op.create_index("ix_map_features_run_class", "map_features", ["analysis_run_id", "class_name"])
    op.create_index("ix_map_features_name", "map_features", ["name"])


def downgrade() -> None:
    op.drop_table("map_features")
    op.drop_table("ai_analysis_tiles")
    op.drop_table("ai_analysis_runs")
