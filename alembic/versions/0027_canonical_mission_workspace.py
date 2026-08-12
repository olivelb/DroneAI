"""Make the tenant-bound mission object namespace durable.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ORGANIZATION_ID = "legacy-unassigned"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE missions
            SET workspace_prefix = CASE
                WHEN organization_id = :legacy
                    THEN 'missions/' || vol_id
                ELSE
                    'organizations/' || organization_id || '/missions/' || vol_id
            END
            WHERE workspace_prefix IS NULL OR workspace_prefix = ''
            """
        ),
        {"legacy": LEGACY_ORGANIZATION_ID},
    )
    op.alter_column(
        "missions",
        "workspace_prefix",
        existing_type=sa.String(length=1024),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "missions",
        "workspace_prefix",
        existing_type=sa.String(length=1024),
        nullable=True,
    )
