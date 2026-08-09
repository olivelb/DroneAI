"""Add feature tombstones, review audit and named raster styles.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = "'created', 'updated', 'reviewed', 'unreviewed', 'tombstoned', 'restored'"


def upgrade() -> None:
    op.add_column("map_features", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("map_features", sa.Column("reviewed_by", sa.String(length=256)))
    op.add_column("map_features", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("map_features", sa.Column("deleted_by", sa.String(length=256)))
    op.add_column("map_features", sa.Column("deletion_reason", sa.Text()))
    op.create_index(
        "ix_map_features_visibility",
        "map_features",
        ["mission_id", "deleted_at"],
    )
    op.create_index(
        "ix_map_features_review",
        "map_features",
        ["mission_id", "reviewed_at"],
    )

    op.create_table(
        "map_feature_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("actor_subject", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("before_state", sa.JSON()),
        sa.Column("after_state", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"action IN ({ACTIONS})", name="ck_map_feature_audit_action"),
        sa.ForeignKeyConstraint(["feature_id"], ["map_features.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_map_feature_audit_events_event_id",
        "map_feature_audit_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_feature_audit_mission_created",
        "map_feature_audit_events",
        ["mission_id", "created_at"],
    )
    op.create_index(
        "ix_feature_audit_feature_created",
        "map_feature_audit_events",
        ["feature_id", "created_at"],
    )

    op.create_table(
        "raster_layer_styles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("style_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer()),
        sa.Column("layer_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("style", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_raster_layer_styles_version"),
        sa.ForeignKeyConstraint(["artifact_id"], ["mission_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "layer_key",
            "name",
            name="uq_raster_layer_style_name",
        ),
    )
    op.create_index(
        "ix_raster_layer_styles_style_id",
        "raster_layer_styles",
        ["style_id"],
        unique=True,
    )
    op.create_index(
        "ix_raster_layer_styles_mission_layer",
        "raster_layer_styles",
        ["mission_id", "layer_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_raster_layer_styles_mission_layer", table_name="raster_layer_styles")
    op.drop_index("ix_raster_layer_styles_style_id", table_name="raster_layer_styles")
    op.drop_table("raster_layer_styles")
    op.drop_index("ix_feature_audit_feature_created", table_name="map_feature_audit_events")
    op.drop_index("ix_feature_audit_mission_created", table_name="map_feature_audit_events")
    op.drop_index("ix_map_feature_audit_events_event_id", table_name="map_feature_audit_events")
    op.drop_table("map_feature_audit_events")
    op.drop_index("ix_map_features_review", table_name="map_features")
    op.drop_index("ix_map_features_visibility", table_name="map_features")
    op.drop_column("map_features", "deletion_reason")
    op.drop_column("map_features", "deleted_by")
    op.drop_column("map_features", "deleted_at")
    op.drop_column("map_features", "reviewed_by")
    op.drop_column("map_features", "reviewed_at")
