"""Add the tenant-owned dataset catalog and mission reference.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-12
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DATASET_STATUSES = "'ready', 'deleting', 'deletion_failed', 'deleted'"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def _backfill_completed_uploads() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, dataset_name, total_bytes, file_count, created_by,
                   completed_at, created_at, updated_at
            FROM dataset_upload_sessions
            WHERE status = 'completed'
            ORDER BY dataset_name, completed_at DESC NULLS LAST, id DESC
            """
        )
    ).mappings()
    now = datetime.now(UTC)
    seen_prefixes: set[str] = set()
    for row in rows:
        prefix = f"datasets/{row['dataset_name']}"
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)
        filenames = bind.execute(
            sa.text(
                """
                SELECT filename
                FROM dataset_upload_files
                WHERE upload_session_id = :upload_session_id
                  AND status = 'completed'
                """
            ),
            {"upload_session_id": row["id"]},
        ).scalars()
        image_count = sum(
            1 for filename in filenames if Path(str(filename)).suffix.lower() in IMAGE_SUFFIXES
        )
        public_id = str(uuid4())
        ready_at = row["completed_at"] or row["updated_at"] or now
        bind.execute(
            sa.text(
                """
                INSERT INTO datasets (
                    dataset_id, upload_session_id, name, owner_subject, prefix,
                    status, manifest_s3_key, file_count, image_count,
                    total_bytes, ready_at, created_at, updated_at
                ) VALUES (
                    :dataset_id, :upload_session_id, :name, :owner_subject,
                    :prefix, 'ready', :manifest_s3_key, :file_count,
                    :image_count, :total_bytes, :ready_at, :created_at,
                    :updated_at
                )
                """
            ),
            {
                "dataset_id": public_id,
                "upload_session_id": row["id"],
                "name": row["dataset_name"],
                "owner_subject": row["created_by"],
                "prefix": prefix,
                "manifest_s3_key": f"{prefix}/dataset-manifest.json",
                "file_count": row["file_count"],
                "image_count": image_count,
                "total_bytes": row["total_bytes"],
                "ready_at": ready_at,
                "created_at": row["created_at"] or now,
                "updated_at": row["updated_at"] or now,
            },
        )
        dataset_pk = bind.execute(
            sa.text("SELECT id FROM datasets WHERE dataset_id = :dataset_id"),
            {"dataset_id": public_id},
        ).scalar_one()
        bind.execute(
            sa.text(
                """
                UPDATE missions
                SET dataset_id = :dataset_id
                WHERE dataset_id IS NULL
                  AND input_dataset = :prefix
                  AND owner_subject = :owner_subject
                """
            ),
            {
                "dataset_id": dataset_pk,
                "prefix": prefix,
                "owner_subject": row["created_by"],
            },
        )


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("upload_session_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("owner_subject", sa.String(length=256), nullable=False),
        sa.Column("prefix", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest_s3_key", sa.String(length=1024), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({DATASET_STATUSES})",
            name="ck_datasets_status",
        ),
        sa.ForeignKeyConstraint(
            ["upload_session_id"],
            ["dataset_upload_sessions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasets_dataset_id", "datasets", ["dataset_id"], unique=True)
    op.create_index("ix_datasets_owner_subject", "datasets", ["owner_subject"])
    op.create_index(
        "ix_datasets_upload_session_id",
        "datasets",
        ["upload_session_id"],
        unique=True,
    )
    op.create_index(
        "uq_datasets_live_owner_name",
        "datasets",
        ["owner_subject", "name"],
        unique=True,
        postgresql_where=sa.text("status != 'deleted'"),
    )
    op.create_index(
        "uq_datasets_live_prefix",
        "datasets",
        ["prefix"],
        unique=True,
        postgresql_where=sa.text("status != 'deleted'"),
    )
    op.add_column("missions", sa.Column("dataset_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_missions_dataset_id",
        "missions",
        "datasets",
        ["dataset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_missions_dataset_id", "missions", ["dataset_id"])
    _backfill_completed_uploads()


def downgrade() -> None:
    op.drop_index("ix_missions_dataset_id", table_name="missions")
    op.drop_constraint("fk_missions_dataset_id", "missions", type_="foreignkey")
    op.drop_column("missions", "dataset_id")
    op.drop_index("uq_datasets_live_prefix", table_name="datasets")
    op.drop_index("uq_datasets_live_owner_name", table_name="datasets")
    op.drop_index("ix_datasets_upload_session_id", table_name="datasets")
    op.drop_index("ix_datasets_owner_subject", table_name="datasets")
    op.drop_index("ix_datasets_dataset_id", table_name="datasets")
    op.drop_table("datasets")
