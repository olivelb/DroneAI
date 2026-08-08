"""Persist integrity metadata for S3-backed AI tile results.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_analysis_tiles",
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ai_analysis_tiles",
        sa.Column("result_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ai_analysis_tiles",
        sa.Column("result_attempt", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_analysis_tiles", "result_attempt")
    op.drop_column("ai_analysis_tiles", "result_size_bytes")
    op.drop_column("ai_analysis_tiles", "result_sha256")
