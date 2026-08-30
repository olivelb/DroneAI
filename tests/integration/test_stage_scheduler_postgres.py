"""PostgreSQL transaction-boundary tests for the distributed scheduler."""

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import Mission, MissionStageRun, Organization
from shared.stage_scheduler import SchedulingLimits

orchestrator = importlib.import_module("app4-dashboard.api.stage_orchestrator")


def _settings() -> orchestrator.StageOrchestratorSettings:
    image = "registry.example/worker@sha256:" + "a" * 64
    executors = {
        stage: orchestrator.StageExecutorConfig(
            image=image,
            command=("python", "-m", f"{stage}_executor"),
            gpu_architecture="ampere",
        )
        for stage in (
            "reconstruction",
            "gaussian_training",
            "gaussian_filtering",
            "rasterization",
            "detection",
            "gaussian_viewer",
        )
    }
    return orchestrator.StageOrchestratorSettings(
        enabled=True,
        namespace="drone-ai",
        poll_seconds=1.0,
        limits=SchedulingLimits(global_active=2, per_owner_active=1),
        executors=executors,
        runtime_class_name="nvidia",
    )


@pytest.mark.integration
def test_advisory_lock_serializes_two_scheduler_transactions() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    first = Session(engine)
    second = Session(engine)
    try:
        assert orchestrator._try_acquire_scheduler_reservation_lock(first)
        assert not orchestrator._try_acquire_scheduler_reservation_lock(second)

        first.commit()
        second.rollback()

        assert orchestrator._try_acquire_scheduler_reservation_lock(second)
        second.commit()
    finally:
        first.close()
        second.close()
        engine.dispose()


@pytest.mark.integration
def test_ready_job_reservation_locks_only_the_stage_run_table() -> None:
    """PostgreSQL rejects FOR UPDATE against the nullable outer-join side."""
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    session = Session(engine)
    try:
        organization_id = "scheduler-lock-regression"
        session.add(
            Organization(
                id=organization_id,
                display_name="Scheduler lock regression",
                status="active",
                created_by="integration-test",
                updated_by="integration-test",
            )
        )
        mission = Mission(
            vol_id="scheduler-lock-regression",
            owner_subject="scheduler@example.test",
            organization_id=organization_id,
            workspace_prefix=(
                "organizations/scheduler-lock-regression/"
                "missions/scheduler-lock-regression"
            ),
            status="pending",
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionStageRun(
                run_id="9" * 32,
                mission_id=mission.id,
                stage="reconstruction",
                attempt=0,
                status="queued",
                idempotency_key="9" * 64,
                resource_class="gpu-geometry",
                parameters={},
            )
        )
        session.flush()

        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )

        assert [job.request.run_id for job in reserved] == ["9" * 32]
    finally:
        session.rollback()
        session.close()
        engine.dispose()
