"""Add durable immutable detection shard receipts.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detection_shard_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stage_run_id", sa.Integer(), nullable=False),
        sa.Column("plan_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("shard_index", sa.Integer(), nullable=False),
        sa.Column("shard_count", sa.Integer(), nullable=False),
        sa.Column("tile_count", sa.Integer(), nullable=False),
        sa.Column("result_key", sa.String(length=1024), nullable=False),
        sa.Column("result_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(plan_checksum_sha256) = 64",
            name="ck_detection_shard_receipts_plan_checksum_length",
        ),
        sa.CheckConstraint(
            "length(result_checksum_sha256) = 64",
            name="ck_detection_shard_receipts_result_checksum_length",
        ),
        sa.CheckConstraint(
            "shard_count >= 2 AND shard_count <= 256",
            name="ck_detection_shard_receipts_shard_count",
        ),
        sa.CheckConstraint(
            "shard_index >= 0 AND shard_index < shard_count",
            name="ck_detection_shard_receipts_shard_index",
        ),
        sa.CheckConstraint(
            "tile_count > 0",
            name="ck_detection_shard_receipts_tile_count",
        ),
        sa.CheckConstraint(
            "result_size_bytes > 0",
            name="ck_detection_shard_receipts_result_size",
        ),
        sa.ForeignKeyConstraint(
            ["stage_run_id"],
            ["mission_stage_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stage_run_id",
            "plan_checksum_sha256",
            "shard_index",
            name="uq_detection_shard_receipt_identity",
        ),
    )
    op.create_index(
        "ix_detection_shard_receipts_run_plan",
        "detection_shard_receipts",
        ["stage_run_id", "plan_checksum_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_detection_shard_receipts_run_plan",
        table_name="detection_shard_receipts",
    )
    op.drop_table("detection_shard_receipts")
