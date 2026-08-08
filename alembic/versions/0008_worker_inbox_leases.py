"""Add renewable leases for long-running worker inbox handlers.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_inbox_events_status", "inbox_events", type_="check")
    op.add_column(
        "inbox_events",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbox_events",
        sa.Column("locked_by", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "ix_inbox_claim",
        "inbox_events",
        ["status", "locked_at"],
    )
    op.create_check_constraint(
        "ck_inbox_events_status",
        "inbox_events",
        "status IN ('processing', 'completed', 'failed')",
    )


def downgrade() -> None:
    op.execute("UPDATE inbox_events SET status = 'processing' WHERE status = 'failed'")
    op.drop_constraint("ck_inbox_events_status", "inbox_events", type_="check")
    op.drop_index("ix_inbox_claim", table_name="inbox_events")
    op.drop_column("inbox_events", "locked_by")
    op.drop_column("inbox_events", "locked_at")
    op.create_check_constraint(
        "ck_inbox_events_status",
        "inbox_events",
        "status IN ('processing', 'completed')",
    )
