"""Add versioned mission stage runs and immutable artifact edges.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = "'reconstruction', 'gaussian_training', 'gaussian_filtering', 'rasterization', 'detection'"
STATUSES = "'blocked', 'queued', 'running', 'succeeded', 'failed', 'cancelled'"


def upgrade() -> None:
    op.create_table(
        "mission_stage_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="blocked", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("executor", sa.String(length=256), nullable=True),
        sa.Column("parameters", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("upstream_artifact_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("provenance", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("quality_metrics", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"stage IN ({STAGES})", name="ck_mission_stage_runs_stage"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_mission_stage_runs_status"),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="ck_mission_stage_runs_progress"),
        sa.CheckConstraint("attempt >= 0", name="ck_mission_stage_runs_attempt"),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_mission_stage_runs_idempotency_length",
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_mission_stage_run_idempotency"),
        sa.UniqueConstraint("mission_id", "stage", "attempt", name="uq_mission_stage_run_attempt"),
    )
    op.create_index("ix_mission_stage_runs_mission_id", "mission_stage_runs", ["mission_id"])
    op.create_index("ix_mission_stage_runs_run_id", "mission_stage_runs", ["run_id"], unique=True)
    op.create_index(
        "ix_mission_stage_runs_mission_stage",
        "mission_stage_runs",
        ["mission_id", "stage", "attempt"],
    )
    op.create_index(
        "ix_mission_stage_runs_recovery",
        "mission_stage_runs",
        ["status", "heartbeat_at"],
    )

    op.create_table(
        "mission_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("stage_run_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("uri", sa.String(length=2048), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("artifact_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(checksum_sha256) = 64", name="ck_mission_artifacts_sha256_length"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_run_id"], ["mission_stage_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mission_artifacts_artifact_id", "mission_artifacts", ["artifact_id"], unique=True)
    op.create_index("ix_mission_artifacts_kind", "mission_artifacts", ["kind"])
    op.create_index("ix_mission_artifacts_mission_id", "mission_artifacts", ["mission_id"])
    op.create_index("ix_mission_artifacts_stage_run_id", "mission_artifacts", ["stage_run_id"])

    op.create_table(
        "mission_artifact_parents",
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("parent_artifact_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(length=64), server_default="derived_from", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("artifact_id <> parent_artifact_id", name="ck_mission_artifact_parent_not_self"),
        sa.ForeignKeyConstraint(["artifact_id"], ["mission_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_artifact_id"], ["mission_artifacts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("artifact_id", "parent_artifact_id"),
    )
    op.create_index(
        "ix_mission_artifact_parents_parent",
        "mission_artifact_parents",
        ["parent_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mission_artifact_parents_parent",
        table_name="mission_artifact_parents",
    )
    op.drop_table("mission_artifact_parents")
    op.drop_index("ix_mission_artifacts_stage_run_id", table_name="mission_artifacts")
    op.drop_index("ix_mission_artifacts_mission_id", table_name="mission_artifacts")
    op.drop_index("ix_mission_artifacts_kind", table_name="mission_artifacts")
    op.drop_index("ix_mission_artifacts_artifact_id", table_name="mission_artifacts")
    op.drop_table("mission_artifacts")
    op.drop_index("ix_mission_stage_runs_recovery", table_name="mission_stage_runs")
    op.drop_index("ix_mission_stage_runs_mission_stage", table_name="mission_stage_runs")
    op.drop_index("ix_mission_stage_runs_run_id", table_name="mission_stage_runs")
    op.drop_index("ix_mission_stage_runs_mission_id", table_name="mission_stage_runs")
    op.drop_table("mission_stage_runs")
