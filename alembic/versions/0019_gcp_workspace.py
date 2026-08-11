"""Add durable ground-control sets, points and image observations.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op


revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gcp_sets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("set_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("vol_id", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("source_format", sa.String(length=64), nullable=False),
        sa.Column("source_crs", sa.String(length=128), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_gcp_sets_version"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "name", name="uq_gcp_set_mission_name"),
    )
    op.create_index("ix_gcp_sets_set_id", "gcp_sets", ["set_id"], unique=True)
    op.create_index("ix_gcp_sets_vol_id", "gcp_sets", ["vol_id"])
    op.create_index(
        "ix_gcp_sets_mission_created",
        "gcp_sets",
        ["mission_id", "created_at"],
    )

    op.create_table(
        "gcp_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("point_id", sa.String(length=36), nullable=False),
        sa.Column("gcp_set_id", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column("source_x", sa.Float(), nullable=False),
        sa.Column("source_y", sa.Float(), nullable=False),
        sa.Column("source_z", sa.Float(), nullable=False),
        sa.Column("altitude_m", sa.Float(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="adjustment", nullable=False),
        sa.Column("horizontal_accuracy_m", sa.Float(), nullable=False),
        sa.Column("vertical_accuracy_m", sa.Float(), nullable=False),
        sa.Column("image_accuracy_px", sa.Float(), nullable=False),
        sa.Column("properties", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('adjustment', 'checkpoint', 'disabled')",
            name="ck_gcp_points_role",
        ),
        sa.CheckConstraint(
            "horizontal_accuracy_m > 0 AND vertical_accuracy_m > 0 "
            "AND image_accuracy_px > 0",
            name="ck_gcp_points_accuracy",
        ),
        sa.CheckConstraint("version >= 1", name="ck_gcp_points_version"),
        sa.ForeignKeyConstraint(["gcp_set_id"], ["gcp_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gcp_set_id", "external_id", name="uq_gcp_point_external_id"),
    )
    op.create_index("ix_gcp_points_point_id", "gcp_points", ["point_id"], unique=True)
    op.create_index("ix_gcp_points_mission_role", "gcp_points", ["mission_id", "role"])
    op.create_index(
        "ix_gcp_points_geometry",
        "gcp_points",
        ["geometry"],
        postgresql_using="gist",
    )

    op.create_table(
        "gcp_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("observation_id", sa.String(length=36), nullable=False),
        sa.Column("gcp_point_id", sa.Integer(), nullable=False),
        sa.Column("image_name", sa.String(length=512), nullable=False),
        sa.Column("image_s3_key", sa.String(length=1024)),
        sa.Column("status", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("pixel_x", sa.Float()),
        sa.Column("pixel_y", sa.Float()),
        sa.Column("candidate_distance_m", sa.Float()),
        sa.Column("image_longitude", sa.Float()),
        sa.Column("image_latitude", sa.Float()),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("updated_by", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('candidate', 'marked', 'skipped')",
            name="ck_gcp_observations_status",
        ),
        sa.CheckConstraint(
            "(status != 'marked') OR (pixel_x IS NOT NULL AND pixel_y IS NOT NULL)",
            name="ck_gcp_observations_marked_pixel",
        ),
        sa.CheckConstraint("version >= 1", name="ck_gcp_observations_version"),
        sa.ForeignKeyConstraint(["gcp_point_id"], ["gcp_points.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gcp_point_id", "image_name", name="uq_gcp_observation_image"),
    )
    op.create_index(
        "ix_gcp_observations_observation_id",
        "gcp_observations",
        ["observation_id"],
        unique=True,
    )
    op.create_index(
        "ix_gcp_observations_point_status",
        "gcp_observations",
        ["gcp_point_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_gcp_observations_point_status", table_name="gcp_observations")
    op.drop_index("ix_gcp_observations_observation_id", table_name="gcp_observations")
    op.drop_table("gcp_observations")
    op.drop_index("ix_gcp_points_geometry", table_name="gcp_points")
    op.drop_index("ix_gcp_points_mission_role", table_name="gcp_points")
    op.drop_index("ix_gcp_points_point_id", table_name="gcp_points")
    op.drop_table("gcp_points")
    op.drop_index("ix_gcp_sets_mission_created", table_name="gcp_sets")
    op.drop_index("ix_gcp_sets_vol_id", table_name="gcp_sets")
    op.drop_index("ix_gcp_sets_set_id", table_name="gcp_sets")
    op.drop_table("gcp_sets")
