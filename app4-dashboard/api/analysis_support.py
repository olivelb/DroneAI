"""Transactional ownership and event helpers for AI analysis routes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from fastapi import HTTPException

from shared.database import AIAnalysisRun, get_session

from .map_support import AnalysisRunRecord, JsonObject, RouteSession, get_mission
from .security import Principal


def analysis_event(run: AnalysisRunRecord) -> JsonObject:
    return {
        "vol_id": run.vol_id,
        "ortho_s3_key": run.ortho_s3_key,
        "analysis_run_id": run.run_id,
        "classes": run.classes or [],
        "ai_confidence": run.confidence,
        "ai_backend": run.backend,
        "ai_model_variant": run.model_variant,
        "sam_prompt": run.prompt,
        "tile_size": run.tile_size,
    }


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
    get_mission(
        session,
        vol_id,
        principal,
        owner_subject=owner_subject,
        action=action,
    )
    query = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.vol_id == vol_id,
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
