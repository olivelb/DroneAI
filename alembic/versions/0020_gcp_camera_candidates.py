"""Add camera-projection metadata to GCP observations.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gcp_observations",
        sa.Column("candidate_method", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "gcp_observations",
        sa.Column("projected_pixel_x", sa.Float(), nullable=True),
    )
    op.add_column(
        "gcp_observations",
        sa.Column("projected_pixel_y", sa.Float(), nullable=True),
    )
    op.add_column(
        "gcp_observations",
        sa.Column("image_width_px", sa.Integer(), nullable=True),
    )
    op.add_column(
        "gcp_observations",
        sa.Column("image_height_px", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_gcp_observations_image_dimensions",
        "gcp_observations",
        "(image_width_px IS NULL AND image_height_px IS NULL) OR (image_width_px > 0 AND image_height_px > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_gcp_observations_image_dimensions",
        "gcp_observations",
        type_="check",
    )
    op.drop_column("gcp_observations", "image_height_px")
    op.drop_column("gcp_observations", "image_width_px")
    op.drop_column("gcp_observations", "projected_pixel_y")
    op.drop_column("gcp_observations", "projected_pixel_x")
    op.drop_column("gcp_observations", "candidate_method")
