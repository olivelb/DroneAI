"""Add the authenticated owner boundary to missions.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "owner_subject",
            sa.String(length=256),
            nullable=False,
            server_default="legacy-unassigned",
        ),
    )
    op.create_index(
        "ix_missions_owner_subject",
        "missions",
        ["owner_subject"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_missions_owner_subject", table_name="missions")
    op.drop_column("missions", "owner_subject")
