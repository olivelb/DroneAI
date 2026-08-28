"""Versioned mission-stage DAG, idempotency, and compatibility projection."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

from shared.stage_contracts import (
    STAGE_DAG_VERSION,
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    ResourceClassId,
    StageId,
    resource_class_for_stage,
    validate_stage_selection,
)

if TYPE_CHECKING:
    from shared.database import Mission, MissionStageRun

class StageRunSpec(TypedDict):
    stage: StageId
    attempt: int
    status: str
    parameters: dict[str, Any]
    upstream_artifact_ids: list[str]
    idempotency_key: str
    resource_class: ResourceClassId


class SessionProtocol(Protocol):
    def add(self, instance: Any) -> None: ...

    def flush(self) -> None: ...

    def query(self, *entities: Any) -> Any: ...


def stage_idempotency_key(
    vol_id: str,
    stage: StageId,
    attempt: int,
    parameters: dict[str, Any],
    upstream_artifact_ids: list[str],
) -> str:
    canonical = json.dumps(
        {
            "vol_id": vol_id,
            "dag_version": STAGE_DAG_VERSION,
            "stage": stage,
            "attempt": attempt,
            "parameters": parameters,
            "upstream_artifact_ids": sorted(upstream_artifact_ids),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_stage_run_specs(payload: dict[str, Any]) -> list[StageRunSpec]:
    vol_id = str(payload["vol_id"])
    stages = validate_stage_selection(
        cast(list[StageId], payload.get("phases") or list(STAGE_ORDER)),
        cast(dict[StageId, str], payload.get("upstream_artifact_ids") or {}),
    )
    upstream_by_stage = cast(
        dict[StageId, str],
        payload.get("upstream_artifact_ids") or {},
    )
    selected = set(stages)
    specs: list[StageRunSpec] = []
    for stage in stages:
        dependencies = STAGE_DEPENDENCIES[stage]
        external_inputs = [
            upstream_by_stage[dependency]
            for dependency in dependencies
            if dependency in upstream_by_stage
        ]
        waits_for_selected_stage = any(
            dependency in selected and dependency not in upstream_by_stage
            for dependency in dependencies
        )
        parameters = {
            "dag_version": STAGE_DAG_VERSION,
            "quality_profile": payload.get("quality_profile"),
            "quality_profile_version": payload.get("quality_profile_version"),
            "work_drive": payload.get("work_drive"),
            "colmap_params": payload.get("colmap_params") or {},
            "ai": {
                "backend": payload.get("ai_backend"),
                "model_variant": payload.get("ai_model_variant"),
                "classes": payload.get("classes") or [],
                "confidence": payload.get("ai_confidence"),
                "sam_prompt": payload.get("sam_prompt"),
                "tile_size": payload.get("tile_size"),
            },
        }
        specs.append(
            {
                "stage": stage,
                "attempt": 0,
                "status": "blocked" if waits_for_selected_stage else "queued",
                "parameters": parameters,
                "upstream_artifact_ids": external_inputs,
                "idempotency_key": stage_idempotency_key(
                    vol_id,
                    stage,
                    0,
                    parameters,
                    external_inputs,
                ),
                "resource_class": resource_class_for_stage(stage, parameters),
            }
        )
    return specs


def initialize_stage_runs(
    session: SessionProtocol,
    mission: Mission,
    payload: dict[str, Any],
) -> list[MissionStageRun]:
    from shared.database import MissionStageRun

    runs = [
        MissionStageRun(
            mission_id=mission.id,
            stage=spec["stage"],
            attempt=spec["attempt"],
            status=spec["status"],
            parameters=spec["parameters"],
            upstream_artifact_ids=spec["upstream_artifact_ids"],
            idempotency_key=spec["idempotency_key"],
            resource_class=spec["resource_class"],
        )
        for spec in build_stage_run_specs(payload)
    ]
    for run in runs:
        session.add(run)
    session.flush()
    return runs


def stage_for_status_event(service: str, step: str | None) -> StageId:
    normalized = (step or "").upper()
    if service == "IA":
        return "detection"
    if service == "TILER":
        return "rasterization"
    if "FILTER" in normalized:
        return "gaussian_filtering"
    if normalized in {"GAUSS", "GAUSSIAN", "TRAINING"}:
        return "gaussian_training"
    if normalized in {"ORTHO", "UPLOADING", "DONE"}:
        return "rasterization"
    return "reconstruction"


def project_status_to_stage_run(
    session: SessionProtocol,
    mission: Mission,
    *,
    service: str,
    step: str | None,
    event_status: str,
    progress: int,
    error_message: str | None,
    stage_run_id: str | None = None,
) -> None:
    from shared.database import MissionStageRun

    stage = stage_for_status_event(service, step)
    runs = cast(
        list[MissionStageRun],
        session.query(MissionStageRun)
        .filter(
            MissionStageRun.mission_id == mission.id,
            MissionStageRun.analysis_run_id.is_(None),
        )
        .order_by(MissionStageRun.attempt.desc())
        .all(),
    )
    latest_by_stage: dict[str, MissionStageRun] = {}
    for candidate in runs:
        latest_by_stage.setdefault(str(candidate.stage), candidate)
    run = (
        next(
            (
                candidate
                for candidate in runs
                if str(candidate.run_id) == stage_run_id
            ),
            None,
        )
        if stage_run_id is not None
        else latest_by_stage.get(stage)
    )
    if event_status in {"error", "cancelled"} and (step or "").upper() in {
        "ERROR",
        "CANCELLED",
    }:
        run = next(
            (
                latest_by_stage[candidate]
                for candidate in reversed(STAGE_ORDER)
                if candidate in latest_by_stage
                and str(latest_by_stage[candidate].status) in {"queued", "running"}
            ),
            run,
        )
    if run is None:
        return
    now = datetime.now(UTC)
    stage_position = STAGE_ORDER.index(cast(StageId, str(run.stage)))
    for previous_stage in STAGE_ORDER[:stage_position]:
        previous = latest_by_stage.get(previous_stage)
        if previous is None or str(previous.status) in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            continue
        previous.status = "succeeded"
        previous.progress = 100
        previous.completed_at = now
        previous.heartbeat_at = now
        previous.updated_at = now
    status_map = {
        "processing": "running",
        "success": "succeeded",
        "error": "failed",
        "cancelled": "cancelled",
    }
    run.status = status_map.get(event_status, "running")
    run.progress = max(0, min(100, progress))
    run.current_step = step
    run.heartbeat_at = now
    if run.started_at is None:
        run.started_at = now
    if run.status in {"succeeded", "failed", "cancelled"}:
        run.completed_at = now
    run.error_message = error_message if run.status == "failed" else None
    run.updated_at = now
