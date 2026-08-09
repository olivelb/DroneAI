"""Reserve active dataset names across API replicas.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_UPLOAD_PREDICATE = "status IN ('uploading', 'failed')"
ACTIVE_UPLOAD_INDEX = "uq_dataset_upload_sessions_active_name"


def upgrade() -> None:
    op.create_index(
        ACTIVE_UPLOAD_INDEX,
        "dataset_upload_sessions",
        ["dataset_name"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_UPLOAD_PREDICATE),
        sqlite_where=sa.text(ACTIVE_UPLOAD_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(
        ACTIVE_UPLOAD_INDEX,
        table_name="dataset_upload_sessions",
    )
