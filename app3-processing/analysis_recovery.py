"""Durable recovery planning for stale AI analysis campaigns."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from shared.database import AIAnalysisRun, AIAnalysisTile, Mission
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
from shared.tenancy import MissionObjectNamespace

JsonObject = dict[str, Any]
SessionFactory = Callable[[], AbstractContextManager[Any]]


def orthomosaic_recovery_event(
    run: Any,
    namespace: MissionObjectNamespace,
) -> JsonObject:
    return cast(
        JsonObject,
        make_event(
            "orthomosaic",
            {
                "vol_id": run.vol_id,
                "organization_id": namespace.organization_id,
                "workspace_prefix": namespace.root,
                "ortho_s3_key": run.ortho_s3_key,
                "analysis_run_id": run.run_id,
                "classes": run.classes or [],
                "ai_confidence": run.confidence,
                "ai_backend": run.backend,
                "ai_model_variant": run.model_variant,
                "sam_prompt": run.prompt,
                "tile_size": run.tile_size,
            },
            event_id=deterministic_tenant_event_id(
                "orthomosaic",
                namespace.organization_id,
                run.vol_id,
                run.run_id,
                run.retry_count,
            ),
            correlation_id=tenant_correlation_id(
                namespace.organization_id,
                run.run_id,
            ),
            attempt=run.retry_count,
        ),
    )


def tile_recovery_event(
    run: Any,
    tile: Any,
    namespace: MissionObjectNamespace,
) -> JsonObject:
    metadata = run.tiling_metadata or {}
    return cast(
        JsonObject,
        make_event(
            "image_tile",
            {
                "vol_id": run.vol_id,
                "organization_id": namespace.organization_id,
                "workspace_prefix": namespace.root,
                "analysis_run_id": run.run_id,
                "tile_index": tile.tile_index,
                "tile_s3_key": tile.tile_s3_key,
                "offset_x": tile.offset_x,
                "offset_y": tile.offset_y,
                "ai_backend": run.backend,
                "ai_model_variant": run.model_variant,
                "sam_prompt": run.prompt,
                "classes": run.classes or [],
                "ai_confidence": run.confidence,
                "total_tiles": run.total_tiles,
                "ortho_transform": metadata.get("transform"),
                "ortho_crs": metadata.get("crs"),
            },
            event_id=deterministic_tenant_event_id(
                "image_tile",
                namespace.organization_id,
                run.vol_id,
                run.run_id,
                tile.tile_index,
                run.retry_count,
            ),
            correlation_id=tenant_correlation_id(
                namespace.organization_id,
                run.run_id,
            ),
            attempt=run.retry_count,
        ),
    )


def _mark_attempts_exhausted(
    run: Any,
    exhausted_tiles: list[Any],
    maximum_tile_attempts: int,
) -> None:
    for tile in exhausted_tiles:
        tile.status = "dead"
        tile.last_error = (
            f"Maximum AI tile attempts exhausted ({maximum_tile_attempts})"
        )
    run.status = "failed"
    run.phase = "tile_attempts_exhausted"
    run.error_message = (
        f"{len(exhausted_tiles)} AI tile(s) exhausted "
        f"the {maximum_tile_attempts}-attempt budget; "
        "manual retry is required"
    )
    run.heartbeat_at = datetime.now(UTC)


def plan_recovery(
    *,
    session_factory: SessionFactory,
    maximum_tile_attempts: int,
) -> tuple[list[str], list[JsonObject], list[JsonObject]]:
    stale_before = datetime.now(UTC) - timedelta(minutes=10)
    ready_run_ids: list[str] = []
    tile_events: list[JsonObject] = []
    ortho_events: list[JsonObject] = []
    with session_factory() as session:
        runs = (
            session.query(AIAnalysisRun)
            .filter(
                AIAnalysisRun.status.in_(
                    ("queued", "tiling", "running", "failed", "finalizing")
                ),
                AIAnalysisRun.heartbeat_at < stale_before,
            )
            .with_for_update(skip_locked=True)
            .limit(10)
            .all()
        )
        for run in runs:
            mission = session.query(Mission).filter(Mission.id == run.mission_id).one()
            namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                mission.vol_id,
                mission.workspace_prefix,
            )
            if run.phase == "tile_attempts_exhausted":
                continue
            tiles = (
                session.query(AIAnalysisTile)
                .filter(AIAnalysisTile.analysis_run_id == run.id)
                .order_by(AIAnalysisTile.tile_index)
                .all()
            )
            completed = sum(tile.status == "completed" for tile in tiles)
            if run.total_tiles and completed >= run.total_tiles:
                run.status = "finalizing"
                run.phase = "recovery_finalizing"
                ready_run_ids.append(run.run_id)
            elif not tiles:
                run.retry_count += 1
                run.status = "queued"
                run.phase = "recovery_retiling"
                ortho_events.append(orthomosaic_recovery_event(run, namespace))
            else:
                incomplete_tiles = [
                    item for item in tiles if item.status != "completed"
                ]
                exhausted_tiles = [
                    item
                    for item in incomplete_tiles
                    if item.attempts >= maximum_tile_attempts
                ]
                if exhausted_tiles:
                    _mark_attempts_exhausted(
                        run,
                        exhausted_tiles,
                        maximum_tile_attempts,
                    )
                    continue
                for tile in incomplete_tiles[:100]:
                    tile.attempts += 1
                    tile.status = "queued"
                    tile.last_error = None
                    tile_events.append(tile_recovery_event(run, tile, namespace))
                run.status = "running"
                run.phase = "recovery_detecting"
            run.heartbeat_at = datetime.now(UTC)
    return ready_run_ids, ortho_events, tile_events
