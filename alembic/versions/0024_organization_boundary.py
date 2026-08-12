"""Add an organization boundary to tenant-owned resources.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ORGANIZATION_ID = "legacy-unassigned"
LOCAL_ORGANIZATION_ID = "local-development"
ACTIVE_UPLOADS = "'initializing', 'uploading', 'finalizing', 'failed'"


def _organization_column() -> sa.Column[str]:
    return sa.Column(
        "organization_id",
        sa.String(length=64),
        nullable=False,
        server_default=LEGACY_ORGANIZATION_ID,
    )


def upgrade() -> None:
    op.add_column("dataset_upload_sessions", _organization_column())
    op.add_column("datasets", _organization_column())
    op.add_column("missions", _organization_column())

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE dataset_upload_sessions
            SET organization_id = :local
            WHERE created_by = :local
            """
        ),
        {"local": LOCAL_ORGANIZATION_ID},
    )
    for table in ("datasets", "missions"):
        bind.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET organization_id = :local
                WHERE owner_subject = :local
                """
            ),
            {"local": LOCAL_ORGANIZATION_ID},
        )

    op.drop_index(
        "uq_dataset_upload_sessions_active_name",
        table_name="dataset_upload_sessions",
    )
    op.create_index(
        "uq_dataset_upload_sessions_active_org_name",
        "dataset_upload_sessions",
        ["organization_id", "dataset_name"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({ACTIVE_UPLOADS})"),
    )
    op.drop_index("uq_datasets_live_owner_name", table_name="datasets")
    op.create_index(
        "uq_datasets_live_organization_name",
        "datasets",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text("status != 'deleted'"),
    )
    op.create_index(
        "ix_dataset_upload_sessions_organization_id",
        "dataset_upload_sessions",
        ["organization_id"],
    )
    op.create_index(
        "ix_datasets_organization_id",
        "datasets",
        ["organization_id"],
    )
    op.create_index(
        "ix_missions_organization_id",
        "missions",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_missions_organization_id", table_name="missions")
    op.drop_index("ix_datasets_organization_id", table_name="datasets")
    op.drop_index(
        "ix_dataset_upload_sessions_organization_id",
        table_name="dataset_upload_sessions",
    )
    op.drop_index("uq_datasets_live_organization_name", table_name="datasets")
    op.create_index(
        "uq_datasets_live_owner_name",
        "datasets",
        ["owner_subject", "name"],
        unique=True,
        postgresql_where=sa.text("status != 'deleted'"),
    )
    op.drop_index(
        "uq_dataset_upload_sessions_active_org_name",
        table_name="dataset_upload_sessions",
    )
    op.create_index(
        "uq_dataset_upload_sessions_active_name",
        "dataset_upload_sessions",
        ["dataset_name"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({ACTIVE_UPLOADS})"),
    )
    op.drop_column("missions", "organization_id")
    op.drop_column("datasets", "organization_id")
    op.drop_column("dataset_upload_sessions", "organization_id")
