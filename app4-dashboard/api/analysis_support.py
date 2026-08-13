"""Transactional ownership and event helpers for AI analysis routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from fastapi import HTTPException

from shared.database import AIAnalysisRun, get_session
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
from shared.tenancy import LEGACY_ORGANIZATION_ID, MissionObjectNamespace

from .map_support import AnalysisRunRecord, JsonObject, RouteSession, get_mission
from .security import Principal


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
        yield typed_session, get_owned_run(
            typed_session,
            vol_id,
            run_id,
            principal,
            owner_subject,
            action=action,
            lock=True,
        )
