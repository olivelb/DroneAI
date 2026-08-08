"""Persist immutable AI model provenance

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_analysis_runs",
        sa.Column("model_manifest", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_analysis_runs", "model_manifest")
