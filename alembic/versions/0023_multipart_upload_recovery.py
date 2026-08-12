"""Make direct multipart uploads crash recoverable.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_UPLOAD_INDEX = "uq_dataset_upload_sessions_active_name"
ACTIVE_UPLOAD_PREDICATE = (
    "status IN ('initializing', 'uploading', 'finalizing', 'failed')"
)
LEGACY_ACTIVE_UPLOAD_PREDICATE = "status IN ('uploading', 'failed')"
SESSION_STATUSES = (
    "'initializing', 'uploading', 'finalizing', "
    "'completed', 'aborted', 'failed'"
)
FILE_STATUSES = (
    "'initializing', 'uploading', 'completing', "
    "'completed', 'aborted', 'failed'"
)
LEGACY_STATUSES = "'uploading', 'completed', 'aborted', 'failed'"


def upgrade() -> None:
    op.drop_index(ACTIVE_UPLOAD_INDEX, table_name="dataset_upload_sessions")
    op.drop_constraint(
        "ck_dataset_upload_sessions_status",
        "dataset_upload_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_upload_files_status",
        "dataset_upload_files",
        type_="check",
    )
    op.add_column(
        "dataset_upload_sessions",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "dataset_upload_files",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.alter_column(
        "dataset_upload_files",
        "multipart_upload_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_dataset_upload_sessions_status",
        "dataset_upload_sessions",
        f"status IN ({SESSION_STATUSES})",
    )
    op.create_check_constraint(
        "ck_dataset_upload_files_status",
        "dataset_upload_files",
        f"status IN ({FILE_STATUSES})",
    )
    op.create_index(
        ACTIVE_UPLOAD_INDEX,
        "dataset_upload_sessions",
        ["dataset_name"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_UPLOAD_PREDICATE),
        sqlite_where=sa.text(ACTIVE_UPLOAD_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(ACTIVE_UPLOAD_INDEX, table_name="dataset_upload_sessions")
    op.drop_constraint(
        "ck_dataset_upload_sessions_status",
        "dataset_upload_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_dataset_upload_files_status",
        "dataset_upload_files",
        type_="check",
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM dataset_upload_sessions
            WHERE status = 'initializing'
               OR id IN (
                   SELECT upload_session_id
                   FROM dataset_upload_files
                   WHERE multipart_upload_id IS NULL
               )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE dataset_upload_sessions
            SET status = 'failed'
            WHERE id IN (
                SELECT upload_session_id
                FROM dataset_upload_files
                WHERE status = 'completing'
            )
            """
        )
    )
    bind.execute(
        sa.text(
            "UPDATE dataset_upload_files SET status = 'failed' "
            "WHERE status = 'completing'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE dataset_upload_sessions SET status = 'uploading' "
            "WHERE status = 'finalizing'"
        )
    )
    op.alter_column(
        "dataset_upload_files",
        "multipart_upload_id",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_column("dataset_upload_files", "last_error")
    op.drop_column("dataset_upload_sessions", "last_error")
    op.create_check_constraint(
        "ck_dataset_upload_sessions_status",
        "dataset_upload_sessions",
        f"status IN ({LEGACY_STATUSES})",
    )
    op.create_check_constraint(
        "ck_dataset_upload_files_status",
        "dataset_upload_files",
        f"status IN ({LEGACY_STATUSES})",
    )
    op.create_index(
        ACTIVE_UPLOAD_INDEX,
        "dataset_upload_sessions",
        ["dataset_name"],
        unique=True,
        postgresql_where=sa.text(LEGACY_ACTIVE_UPLOAD_PREDICATE),
        sqlite_where=sa.text(LEGACY_ACTIVE_UPLOAD_PREDICATE),
    )
