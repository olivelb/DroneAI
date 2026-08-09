"""Add resource-aware stage scheduling state.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESOURCE_CLASSES = (
    "'cpu-standard', 'gpu-standard', 'gpu-geometry', 'gpu-high-memory'"
)


def upgrade() -> None:
    op.add_column(
        "mission_stage_runs",
        sa.Column("resource_class", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mission_stage_runs",
        sa.Column("job_name", sa.String(length=253), nullable=True),
    )
    op.add_column(
        "mission_stage_runs",
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "mission_stage_runs",
        sa.Column("dispatch_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "mission_stage_runs",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE mission_stage_runs
        SET resource_class = CASE stage
            WHEN 'reconstruction' THEN 'gpu-geometry'
            WHEN 'gaussian_training' THEN 'gpu-high-memory'
            WHEN 'gaussian_filtering' THEN 'gpu-high-memory'
            WHEN 'rasterization' THEN 'cpu-standard'
            WHEN 'detection' THEN 'gpu-standard'
        END
        """
    )
    op.alter_column("mission_stage_runs", "resource_class", nullable=False)
    op.create_check_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        f"resource_class IN ({RESOURCE_CLASSES})",
    )
    op.create_check_constraint(
        "ck_mission_stage_runs_dispatch_attempts",
        "mission_stage_runs",
        "dispatch_attempts >= 0",
    )
    op.create_index(
        "ix_mission_stage_runs_job_name",
        "mission_stage_runs",
        ["job_name"],
        unique=True,
    )
    op.create_index(
        "ix_mission_stage_runs_dispatch",
        "mission_stage_runs",
        ["status", "executor", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mission_stage_runs_dispatch", table_name="mission_stage_runs")
    op.drop_index("ix_mission_stage_runs_job_name", table_name="mission_stage_runs")
    op.drop_constraint(
        "ck_mission_stage_runs_dispatch_attempts",
        "mission_stage_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        type_="check",
    )
    op.drop_column("mission_stage_runs", "scheduled_at")
    op.drop_column("mission_stage_runs", "dispatch_error")
    op.drop_column("mission_stage_runs", "dispatch_attempts")
    op.drop_column("mission_stage_runs", "job_name")
    op.drop_column("mission_stage_runs", "resource_class")
