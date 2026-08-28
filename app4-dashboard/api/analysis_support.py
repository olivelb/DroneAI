"""Transactional ownership and Stage Job helpers for AI analysis routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from fastapi import HTTPException, status

from shared.analysis_stages import cancel_analysis_stages, create_analysis_stage, sync_analysis_stage
from shared.database import AIAnalysisRun, MissionArtifact, MissionStageRun, get_session
from shared.organization_saas import MANUAL_DELETION_FAILED_STEP, MANUAL_DELETION_STEP

from .map_support import AnalysisRunRecord, RouteSession, get_mission
from .security import Principal

ACTIVE_ANALYSIS_STATUSES = ("queued", "tiling", "running", "finalizing")


def latest_analysis_stage(session: Any, run: Any) -> Any:
    stage = session.query(MissionStageRun).filter(
        MissionStageRun.analysis_run_id == run.id,
        MissionStageRun.mission_id == run.mission_id,
    ).order_by(MissionStageRun.attempt.desc()).with_for_update().first()
    if stage is None:
        raise HTTPException(status_code=409, detail="Analysis has no bounded stage attempt")
    sync_analysis_stage(session, stage)
    return stage


def queue_analysis_stage(session: Any, mission: Any, run: Any, artifact_id: str) -> None:
    artifact = session.query(MissionArtifact).filter(
        MissionArtifact.mission_id == mission.id,
        MissionArtifact.artifact_id == artifact_id,
    ).first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Analysis raster artifact is not available")
    try:
        create_analysis_stage(session, mission, run, artifact)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def ensure_mission_accepts_new_analysis(mission: Any) -> None:
    """Reject analysis creation/retry once mission deletion has started."""

    if mission.status == "cancelled":
        raise HTTPException(status_code=409, detail="Mission is cancelled; new analyses are blocked")
    if mission.status in {"deleting", "deletion_failed"} or mission.current_step in {
        MANUAL_DELETION_STEP,
        MANUAL_DELETION_FAILED_STEP,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mission deletion is in progress; new analyses are blocked",
        )


def request_mission_analysis_cancellations(
    session: Any,
    mission: Any,
) -> int:
    """Cancel analysis Stage Jobs while the caller holds the mission lock."""
    runs = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.mission_id == mission.id,
        AIAnalysisRun.status.in_(ACTIVE_ANALYSIS_STATUSES),
    ).all()
    cancelled = 0
    for run in runs:
        if cancel_analysis_stages(session, run):
            cancelled += 1
    return cancelled


def get_owned_run(
    session: RouteSession,
    vol_id: str,
    run_id: str,
    principal: Principal,
    owner_subject: str | None,
    *,
    action: str,
    lock: bool = False,
) -> AnalysisRunRecord:
    mission = get_mission(
        session,
        vol_id,
        principal,
        owner_subject=owner_subject,
        action=action,
        for_update=lock,
    )
    query = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.mission_id == mission.id,
        AIAnalysisRun.run_id == run_id,
    )
    run = cast(AnalysisRunRecord | None, query.first())
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return run


@contextmanager
def owned_run_scope(
    vol_id: str,
    run_id: str,
    principal: Principal,
    owner_subject: str | None,
    action: str,
) -> Iterator[tuple[RouteSession, AnalysisRunRecord]]:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        yield (
            typed_session,
            get_owned_run(
                typed_session,
                vol_id,
                run_id,
                principal,
                owner_subject,
                action=action,
                lock=True,
            ),
        )
