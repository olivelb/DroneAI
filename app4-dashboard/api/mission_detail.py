"""Durable owner-scoped mission detail projection."""

from __future__ import annotations

from typing import Any, cast

from shared.database import (
    AIAnalysisRun,
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionLog,
    MissionStageRun,
)

from .mission_state import serialize_mission
from .stage_projection import stage_lifecycle_logs


def mission_detail_projection(
    session: Any,
    mission: Mission,
    catalog_item: dict[str, Any],
) -> dict[str, Any]:
    logs = (
        session.query(MissionLog)
        .filter(MissionLog.mission_id == mission.id)
        .order_by(MissionLog.created_at.desc())
        .limit(200)
        .all()
    )
    analyses = (
        session.query(AIAnalysisRun)
        .filter(AIAnalysisRun.mission_id == mission.id)
        .order_by(AIAnalysisRun.created_at.desc())
        .all()
    )
    stage_runs = (
        session.query(MissionStageRun)
        .filter(MissionStageRun.mission_id == mission.id)
        .order_by(MissionStageRun.attempt, MissionStageRun.id)
        .all()
    )
    artifacts = (
        session.query(MissionArtifact)
        .filter(MissionArtifact.mission_id == mission.id)
        .order_by(MissionArtifact.created_at)
        .all()
    )
    artifact_ids = [artifact.id for artifact in artifacts]
    parent_edges = (
        session.query(MissionArtifactParent)
        .filter(MissionArtifactParent.artifact_id.in_(artifact_ids))
        .all()
        if artifact_ids
        else []
    )
    public_ids = {artifact.id: artifact.artifact_id for artifact in artifacts}
    products: list[dict[str, Any]] = []
    if mission.ortho_s3_key:
        products.append(
            {"kind": "orthomosaic", "s3_key": mission.ortho_s3_key}
        )
    products.extend(
        {
            "kind": "analysis",
            "run_id": analysis.run_id,
            "name": analysis.name,
            "status": analysis.status,
            "s3_key": analysis.result_s3_key,
        }
        for analysis in analyses
    )
    products.extend(
        {
            "kind": artifact.kind,
            "artifact_id": artifact.artifact_id,
            "stage_run_id": artifact.stage_run.run_id,
            "s3_key": artifact.uri,
            "checksum_sha256": artifact.checksum_sha256,
            "size_bytes": artifact.size_bytes,
            "metadata": artifact.artifact_metadata or {},
            "parent_artifact_ids": [
                public_ids[edge.parent_artifact_id]
                for edge in parent_edges
                if edge.artifact_id == artifact.id
                and edge.parent_artifact_id in public_ids
            ],
        }
        for artifact in artifacts
    )
    snapshot = serialize_mission(cast(Any, mission))
    persisted_logs = [
        {
            "service": entry.service,
            "step": entry.step,
            "status": entry.status,
            "progress": entry.progress,
            "message": entry.message,
            "details": entry.details,
            "created_at": entry.created_at,
        }
        for entry in reversed(logs)
    ]
    operator_logs = sorted(
        [*persisted_logs, *stage_lifecycle_logs(stage_runs)],
        key=lambda entry: (
            entry["created_at"].timestamp() if entry["created_at"] else 0.0
        ),
    )
    return {
        **catalog_item,
        "parameters": mission.params or {},
        "attempts": sorted({run.attempt for run in stage_runs})
        or [int(mission.retry_count or 0)],
        "stage_runs": [
            {
                "run_id": run.run_id,
                "stage": run.stage,
                "attempt": run.attempt,
                "status": run.status,
                "progress": run.progress,
                "current_step": run.current_step,
                "executor": run.executor,
                "resource_class": run.resource_class,
                "job_name": run.job_name,
                "dispatch_attempts": run.dispatch_attempts,
                "dispatch_error": run.dispatch_error,
                "parameters": run.parameters or {},
                "upstream_artifact_ids": run.upstream_artifact_ids or [],
                "provenance": run.provenance or {},
                "quality_metrics": run.quality_metrics or {},
                "error_message": run.error_message,
                "heartbeat_at": (
                    run.heartbeat_at.isoformat() if run.heartbeat_at else None
                ),
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "scheduled_at": (
                    run.scheduled_at.isoformat() if run.scheduled_at else None
                ),
                "completed_at": (
                    run.completed_at.isoformat() if run.completed_at else None
                ),
            }
            for run in stage_runs
        ],
        "phases": snapshot["services"],
        "heartbeat": {
            "updated_at": catalog_item["updated_at"],
            "age_seconds": catalog_item["last_event_age_seconds"],
            "delayed": catalog_item["is_stale"],
        },
        "logs": [
            {
                "service": entry["service"],
                "step": entry["step"],
                "status": entry["status"],
                "progress": entry["progress"],
                "message": entry["message"],
                "details": entry["details"],
                "created_at": (
                    entry["created_at"].isoformat()
                    if entry["created_at"]
                    else None
                ),
            }
            for entry in operator_logs
        ],
        "products": products,
    }
