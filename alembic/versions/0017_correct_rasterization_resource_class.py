"""Correct queued rasterization runs created with a CPU resource class.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE mission_stage_runs
        SET resource_class = 'gpu-standard',
            updated_at = CURRENT_TIMESTAMP
        WHERE stage = 'rasterization'
          AND resource_class = 'cpu-standard'
          AND status IN ('blocked', 'queued')
          AND executor IS NULL
        """
    )


def downgrade() -> None:
    # This migration repairs invalid scheduling data. Restoring the invalid
    # CPU assignment would make pending rasterization runs undispatchable.
    pass
