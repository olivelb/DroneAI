"""Bind standalone analyses to bounded detection Stage Jobs.

Revision ID: 0037
Revises: 0036
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_ai_analysis_run_mission", "ai_analysis_runs", ["id", "mission_id"])
    op.add_column("mission_stage_runs", sa.Column("analysis_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_stage_analysis_mission", "mission_stage_runs", "ai_analysis_runs",
        ["analysis_run_id", "mission_id"], ["id", "mission_id"], ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_stage_analysis_detection", "mission_stage_runs",
        "analysis_run_id IS NULL OR stage = 'detection'",
    )
    op.create_index("ix_stage_analysis_attempt", "mission_stage_runs", ["analysis_run_id", "attempt"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM mission_stage_runs WHERE analysis_run_id IS NOT NULL LIMIT 1"
    )).first() is not None:
        raise RuntimeError("Cannot downgrade 0037 while analysis Stage Jobs exist")
    op.drop_index("ix_stage_analysis_attempt", table_name="mission_stage_runs")
    op.drop_constraint("ck_stage_analysis_detection", "mission_stage_runs", type_="check")
    op.drop_constraint("fk_stage_analysis_mission", "mission_stage_runs", type_="foreignkey")
    op.drop_column("mission_stage_runs", "analysis_run_id")
    op.drop_constraint("uq_ai_analysis_run_mission", "ai_analysis_runs", type_="unique")
