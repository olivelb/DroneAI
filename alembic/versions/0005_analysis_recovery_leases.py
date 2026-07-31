"""AI analysis finalization leases

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ai_analysis_runs",
        sa.Column("finalization_owner", sa.String(256), nullable=True),
    )
    op.add_column(
        "ai_analysis_runs",
        sa.Column(
            "finalization_lease_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_analysis_runs", "finalization_lease_until")
    op.drop_column("ai_analysis_runs", "finalization_owner")
    op.drop_column("missions", "retry_count")
