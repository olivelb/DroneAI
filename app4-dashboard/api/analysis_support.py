"""Transactional ownership and event helpers for AI analysis routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException, status

from shared.cancellation import mark_cancellation_requested
from shared.config import TOPIC_CONTROL
from shared.database import AIAnalysisRun, AIAnalysisTile, get_session
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
from shared.inbox_outbox import enqueue_outbox
from shared.kafka_partitioning import tenant_mission_key
from shared.tenancy import LEGACY_ORGANIZATION_ID, MissionObjectNamespace

from .map_support import AnalysisRunRecord, JsonObject, RouteSession, get_mission
from .security import Principal

ACTIVE_ANALYSIS_STATUSES = ("queued", "tiling", "running", "finalizing")


def analysis_event(
    run: AnalysisRunRecord,
    namespace: MissionObjectNamespace | None = None,
) -> JsonObject:
    namespace = namespace or MissionObjectNamespace.create(
        LEGACY_ORGANIZATION_ID,
        run.vol_id,
    )
    event: JsonObject = {
        "vol_id": run.vol_id,
        "organization_id": namespace.organization_id,
        "workspace_prefix": namespace.root,
        "ortho_s3_key": run.ortho_s3_key,
        "analysis_run_id": run.run_id,
        "classes": run.classes or [],
        "ai_confidence": run.confidence,
        "ai_backend": run.backend,
        "sam_prompt": run.prompt,
        "tile_size": run.tile_size,
    }
    if run.backend == "yolo":
        event["ai_model_variant"] = run.model_variant
    return event


def build_analysis_pipeline_event(
    run: AnalysisRunRecord,
    namespace: MissionObjectNamespace,
    *,
    attempt: int = 0,
) -> JsonObject:
    """Build one tenant-qualified orthomosaic analysis command."""

    return cast(
        JsonObject,
        make_event(
            "orthomosaic",
            analysis_event(run, namespace),
            event_id=deterministic_tenant_event_id(
                "orthomosaic",
                namespace.organization_id,
                run.vol_id,
                run.run_id,
                attempt,
            ),
            correlation_id=tenant_correlation_id(
                namespace.organization_id,
                run.run_id,
            ),
            attempt=attempt,
        ),
    )


def build_analysis_cancel_event(
    vol_id: str,
    run_id: str,
    organization_id: str,
    attempt: int,
) -> JsonObject:
    """Build one tenant-qualified analysis cancellation command."""

    return cast(
        JsonObject,
        make_event(
            "control",
            {
                "vol_id": vol_id,
                "organization_id": organization_id,
                "command": "cancel",
                "analysis_run_id": run_id,
            },
            event_id=deterministic_tenant_event_id(
                "control",
                organization_id,
                vol_id,
                "cancel",
                run_id,
                attempt,
            ),
            correlation_id=tenant_correlation_id(organization_id, run_id),
            attempt=attempt,
        ),
    )


def ensure_mission_accepts_new_analysis(mission: Any) -> None:
    """Reject analysis creation/retry once mission deletion has started."""

    if mission.status in {"deleting", "deletion_failed"} or mission.current_step in {
        "DELETION_REQUESTED",
        "DELETION_FAILED",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mission deletion is in progress; new analyses are blocked",
        )


def request_mission_analysis_cancellations(
    session: Any,
    mission: Any,
) -> int:
    """Cancel every active analysis under the already-locked mission row.

    Revoking finalization ownership and retiring queued tile journal entries
    prevents a cancelled worker from publishing logical state after deletion
    has entered its drain phase. The outbox command remains the prompt signal;
    the durable run status is the worker-side source of truth.
    """

    runs = (
        session.query(AIAnalysisRun)
        .filter(
            AIAnalysisRun.mission_id == mission.id,
            AIAnalysisRun.status.in_(ACTIVE_ANALYSIS_STATUSES),
        )
        .with_for_update()
        .all()
    )
    now = datetime.now(UTC)
    cancelled = 0
    for run in runs:
        attempt = int(run.retry_count or 0)
        if not mark_cancellation_requested(
            session,
            vol_id=str(mission.vol_id),
            run_id=str(run.run_id),
            attempt=attempt,
            organization_id=str(mission.organization_id),
        ):
            continue
        run.finalization_owner = None
        run.finalization_lease_until = None
        run.completed_at = now
        (
            session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.status == "queued",
            )
            .update(
                {
                    AIAnalysisTile.status: "dead",
                    AIAnalysisTile.last_error: "Analysis cancelled for mission deletion",
                    AIAnalysisTile.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        enqueue_outbox(
            session,
            topic=TOPIC_CONTROL,
            event=build_analysis_cancel_event(
                str(mission.vol_id),
                str(run.run_id),
                str(mission.organization_id),
                attempt,
            ),
            key=tenant_mission_key(str(mission.organization_id), str(mission.vol_id)),
        )
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
    if lock:
        query = query.with_for_update()
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
