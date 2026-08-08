"""Add shared API rate-limit buckets.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limit_buckets",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key_hash"),
    )
    op.create_index(
        "ix_api_rate_limit_buckets_updated_at",
        "api_rate_limit_buckets",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_api_rate_limit_buckets_updated_at",
        table_name="api_rate_limit_buckets",
    )
    op.drop_table("api_rate_limit_buckets")
