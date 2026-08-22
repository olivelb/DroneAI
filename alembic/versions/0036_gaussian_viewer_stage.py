"""Add the independent Gaussian viewer stage and CPU resource envelope.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = "'reconstruction', 'gaussian_training', 'gaussian_filtering', 'rasterization', 'detection', 'gaussian_viewer'"
OLD_STAGES = "'reconstruction', 'gaussian_training', 'gaussian_filtering', 'rasterization', 'detection'"
RESOURCE_CLASSES = "'cpu-standard', 'cpu-high-memory', 'gpu-standard', 'gpu-geometry', 'gpu-high-memory'"
OLD_RESOURCE_CLASSES = "'cpu-standard', 'gpu-standard', 'gpu-geometry', 'gpu-high-memory'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_mission_stage_runs_stage",
        "mission_stage_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_mission_stage_runs_stage",
        "mission_stage_runs",
        f"stage IN ({STAGES})",
    )
    op.drop_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        f"resource_class IN ({RESOURCE_CLASSES})",
    )


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.execute(
        text(
            "SELECT run_id FROM mission_stage_runs "
            "WHERE stage = 'gaussian_viewer' "
            "OR resource_class = 'cpu-high-memory' LIMIT 1"
        )
    ).first()
    if incompatible is not None:
        raise RuntimeError(
            "Cannot downgrade 0036 while Gaussian viewer stage runs exist; "
            "restore the application/schema forward instead of deleting lineage"
        )
    op.drop_constraint(
        "ck_mission_stage_runs_stage",
        "mission_stage_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_mission_stage_runs_stage",
        "mission_stage_runs",
        f"stage IN ({OLD_STAGES})",
    )
    op.drop_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_mission_stage_runs_resource_class",
        "mission_stage_runs",
        f"resource_class IN ({OLD_RESOURCE_CLASSES})",
    )
