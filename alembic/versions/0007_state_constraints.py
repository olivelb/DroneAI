"""Constrain durable workflow states at the database boundary.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONSTRAINTS = (
    (
        "ck_missions_status",
        "missions",
        "status IN ('pending', 'processing', 'success', 'completed', 'error', "
        "'cancelled', 'stale', 'deleting', 'deletion_failed')",
    ),
    (
        "ck_missions_aggregation_status",
        "missions",
        "aggregation_status IN ('pending', 'collecting', 'finalizing', "
        "'completed', 'failed')",
    ),
    (
        "ck_ai_analysis_runs_status",
        "ai_analysis_runs",
        "status IN ('queued', 'tiling', 'running', 'finalizing', 'completed', "
        "'failed', 'cancelled')",
    ),
    (
        "ck_ai_analysis_runs_phase",
        "ai_analysis_runs",
        "phase IN ('queued', 'tiling', 'detecting', 'deduplicating', 'completed', "
        "'tiling_failed', 'finalization_failed', 'recovery_queued', 'cancelled', "
        "'recovery_finalizing', 'recovery_retiling', 'tile_attempts_exhausted', "
        "'recovery_detecting')",
    ),
    (
        "ck_ai_analysis_tiles_status",
        "ai_analysis_tiles",
        "status IN ('queued', 'completed', 'dead')",
    ),
    (
        "ck_map_features_source",
        "map_features",
        "source IN ('manual', 'ai')",
    ),
    (
        "ck_mission_logs_status",
        "mission_logs",
        "status IS NULL OR status IN ('processing', 'success', 'error', 'cancelled')",
    ),
    (
        "ck_inbox_events_status",
        "inbox_events",
        "status IN ('processing', 'completed')",
    ),
    (
        "ck_outbox_events_status",
        "outbox_events",
        "status IN ('pending', 'publishing', 'published', 'failed', 'dead')",
    ),
)


def upgrade() -> None:
    for name, table, condition in CONSTRAINTS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for name, table, _condition in reversed(CONSTRAINTS):
        op.drop_constraint(name, table, type_="check")
