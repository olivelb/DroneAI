"""Shared stage-artifact completion and DAG release operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from shared.database import Mission, MissionArtifact, MissionStageRun
from shared.stage_contracts import STAGE_DEPENDENCIES, StageId


def mark_stage_run_succeeded(run: MissionStageRun) -> None:
    now = datetime.now(UTC)
    record = cast(Any, run)
    record.status = "succeeded"
    record.progress = 100
    record.current_step = "SUCCEEDED"
    record.heartbeat_at = now
    record.completed_at = now
    record.error_message = None


def release_ready_stage_runs(
    session: Any,
    mission: Mission,
) -> list[MissionStageRun]:
    """Release direct dependants whose exact input stage now has an artifact."""
    artifacts = cast(
        list[MissionArtifact],
        session.query(MissionArtifact)
        .filter(MissionArtifact.mission_id == mission.id)
        .order_by(MissionArtifact.created_at.desc())
        .all(),
    )
    artifact_by_stage: dict[str, MissionArtifact] = {}
    for artifact in artifacts:
        artifact_by_stage.setdefault(
            cast(str, artifact.stage_run.stage),
            artifact,
        )
    blocked_runs = cast(
        list[MissionStageRun],
        session.query(MissionStageRun)
        .filter(
            MissionStageRun.mission_id == mission.id,
            MissionStageRun.status == "blocked",
        )
        .with_for_update()
        .all(),
    )
    released: list[MissionStageRun] = []
    for run in blocked_runs:
        dependencies = STAGE_DEPENDENCIES[cast(StageId, run.stage)]
        if not all(dependency in artifact_by_stage for dependency in dependencies):
            continue
        upstream = [
            cast(str, artifact_by_stage[dependency].artifact_id)
            for dependency in dependencies
        ]
        record = cast(Any, run)
        record.upstream_artifact_ids = upstream
        record.status = "queued"
        record.current_step = "QUEUED"
        released.append(run)
    return released
