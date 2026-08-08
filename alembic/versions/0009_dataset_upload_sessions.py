"""Add durable direct-to-S3 dataset upload sessions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPLOAD_STATUSES = "'uploading', 'completed', 'aborted', 'failed'"


def upgrade() -> None:
    op.create_table(
        "dataset_upload_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("part_size", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({UPLOAD_STATUSES})",
            name="ck_dataset_upload_sessions_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index(
        "ix_dataset_upload_sessions_dataset_name",
        "dataset_upload_sessions",
        ["dataset_name"],
    )
    op.create_index(
        "ix_dataset_upload_sessions_expiry",
        "dataset_upload_sessions",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_dataset_upload_sessions_session_id",
        "dataset_upload_sessions",
        ["session_id"],
        unique=True,
    )

    op.create_table(
        "dataset_upload_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("upload_session_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=256), nullable=False),
        sa.Column("multipart_upload_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completed_parts", sa.JSON(), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({UPLOAD_STATUSES})",
            name="ck_dataset_upload_files_status",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["dataset_upload_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id"),
        sa.UniqueConstraint(
            "upload_session_id",
            "filename",
            name="uq_dataset_upload_file_name",
        ),
    )
    op.create_index(
        "ix_dataset_upload_files_file_id",
        "dataset_upload_files",
        ["file_id"],
        unique=True,
    )
    op.create_index(
        "ix_dataset_upload_files_upload_session_id",
        "dataset_upload_files",
        ["upload_session_id"],
    )


def downgrade() -> None:
    op.drop_table("dataset_upload_files")
    op.drop_table("dataset_upload_sessions")
