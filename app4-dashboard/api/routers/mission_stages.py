"""Idempotent stage retry and immutable artifact publication routes."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from shared.config import TOPIC_MISSION
from shared.database import (
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    get_session,
)
from shared.inbox_outbox import enqueue_outbox
from shared.stage_contracts import (
    STAGE_DAG_VERSION,
    STAGE_DEPENDENCIES,
    StageId,
    resource_class_for_stage,
)

from ..messaging import build_stage_mission_event
from ..mission_access import get_owned_mission
from ..security import Principal, require_operator
from ..stage_orchestrator import stage_jobs_enabled
from ..stage_schemas import ArtifactCreate, StageRunCreate

router = APIRouter()


def _serialize_stage_run(run: MissionStageRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "stage": run.stage,
        "attempt": run.attempt,
        "status": run.status,
        "progress": run.progress,
        "parameters": run.parameters or {},
        "upstream_artifact_ids": run.upstream_artifact_ids or [],
        "idempotency_key": run.idempotency_key,
        "resource_class": run.resource_class,
        "executor": run.executor,
        "job_name": run.job_name,
        "dispatch_attempts": run.dispatch_attempts,
        "dispatch_error": run.dispatch_error,
        "scheduled_at": run.scheduled_at,
    }


def _request_key(principal: Principal, raw_key: str) -> str:
    return hashlib.sha256(
        f"{principal.subject}:{raw_key}".encode()
    ).hexdigest()


def _stage_parameters(stage: StageId, request: StageRunCreate) -> dict[str, Any]:
    return {
        "dag_version": STAGE_DAG_VERSION,
        **request.parameters,
    }


def _artifacts_for_request(
    session: Any,
    mission: Mission,
    artifact_ids: list[str],
) -> list[MissionArtifact]:
    if not artifact_ids:
        return []
    artifacts = cast(
        list[MissionArtifact],
        session.query(MissionArtifact)
        .filter(
            MissionArtifact.mission_id == mission.id,
            MissionArtifact.artifact_id.in_(artifact_ids),
        )
        .all(),
    )
    if {artifact.artifact_id for artifact in artifacts} != set(artifact_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Every upstream artifact must exist in the same mission",
        )
    return artifacts


def _validate_dependency_artifacts(
    artifacts: list[MissionArtifact],
    requested: dict[StageId, str],
) -> None:
    artifact_by_id = {
        cast(str, artifact.artifact_id): artifact for artifact in artifacts
    }
    for dependency, artifact_id in requested.items():
        artifact = artifact_by_id[artifact_id]
        if artifact.stage_run.stage != dependency:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Artifact {artifact_id} was produced by stage "
                    f"{artifact.stage_run.stage}, not {dependency}"
                ),
            )


def _immutable_artifact_matches(
    existing: MissionArtifact,
    request: ArtifactCreate,
    parent_artifact_ids: set[str],
) -> bool:
    existing_parent_ids = {
        cast(str, edge.parent.artifact_id) for edge in existing.parent_edges
    }
    return (
        cast(str, existing.kind) == request.kind
        and cast(str, existing.uri) == request.uri
        and cast(str, existing.checksum_sha256) == request.checksum_sha256
        and cast(int | None, existing.size_bytes) == request.size_bytes
        and cast(dict[str, Any], existing.artifact_metadata or {})
        == request.metadata
        and existing_parent_ids == parent_artifact_ids
    )


def _mark_stage_run_succeeded(run: MissionStageRun) -> None:
    now = datetime.now(UTC)
    record = cast(Any, run)
    record.status = "succeeded"
    record.progress = 100
    record.heartbeat_at = now
    record.completed_at = now
    record.error_message = None


def _queue_ready_stage_runs(session: Any, mission: Mission) -> list[str]:
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
    queued: list[str] = []
    for run in blocked_runs:
        dependencies = STAGE_DEPENDENCIES[cast(StageId, run.stage)]
        if not all(dependency in artifact_by_stage for dependency in dependencies):
            continue
        upstream = {
            dependency: cast(
                str,
                artifact_by_stage[dependency].artifact_id,
            )
            for dependency in dependencies
        }
        run_record = cast(Any, run)
        run_record.upstream_artifact_ids = list(upstream.values())
        run_record.status = "queued"
        payload = {
            **cast(dict[str, Any], mission.params or {}),
            "vol_id": cast(str, mission.vol_id),
            "attempt": cast(int, run.attempt),
            "phases": [cast(str, run.stage)],
            "stage_run_id": cast(str, run.run_id),
            "upstream_artifact_ids": upstream,
            "stage_parameters": cast(dict[str, Any], run.parameters or {}),
        }
        if not stage_jobs_enabled():
            enqueue_outbox(
                session,
                topic=TOPIC_MISSION,
                event=build_stage_mission_event(payload),
                key=cast(str, mission.vol_id),
            )
        queued.append(cast(str, run.run_id))
    return queued


@router.post(
    "/missions/{vol_id}/stages/{stage}/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_stage_run(
    vol_id: str,
    stage: StageId,
    request: StageRunCreate,
    principal: Annotated[Principal, Depends(require_operator)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=256),
    ],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> dict[str, Any]:
    durable_key = _request_key(principal, idempotency_key)
    try:
        with get_session() as session:
            existing = (
                session.query(MissionStageRun)
                .filter(MissionStageRun.idempotency_key == durable_key)
                .first()
            )
            if existing is not None:
                mission = get_owned_mission(
                    session,
                    vol_id,
                    principal,
                    requested_owner=owner_subject,
                    action="stage_retry_idempotent",
                )
                if existing.mission_id != mission.id or existing.stage != stage:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key is already used by another stage request",
                    )
                if (
                    cast(dict[str, Any], existing.parameters or {})
                    != _stage_parameters(stage, request)
                    or set(cast(list[str], existing.upstream_artifact_ids or []))
                    != set(request.upstream_artifact_ids.values())
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key was replayed with a different request",
                    )
                return _serialize_stage_run(existing)

            mission = get_owned_mission(
                session,
                vol_id,
                principal,
                requested_owner=owner_subject,
                action="stage_retry",
                for_update=True,
            )
            required = set(STAGE_DEPENDENCIES[stage])
            if set(request.upstream_artifact_ids) != required:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Stage {stage} requires exact upstream artifact keys: "
                        + ", ".join(sorted(required))
                    ),
                )
            upstream_ids = list(request.upstream_artifact_ids.values())
            upstream_artifacts = _artifacts_for_request(
                session,
                mission,
                upstream_ids,
            )
            _validate_dependency_artifacts(
                upstream_artifacts,
                request.upstream_artifact_ids,
            )
            latest_attempt = session.query(
                func.max(MissionStageRun.attempt)
            ).filter(
                MissionStageRun.mission_id == mission.id,
                MissionStageRun.stage == stage,
            ).scalar()
            attempt = int(latest_attempt if latest_attempt is not None else -1) + 1
            parameters = _stage_parameters(stage, request)
            resource_class = resource_class_for_stage(stage, parameters)
            run = MissionStageRun(
                mission_id=mission.id,
                stage=stage,
                attempt=attempt,
                status="queued",
                parameters=parameters,
                resource_class=resource_class,
                upstream_artifact_ids=upstream_ids,
                idempotency_key=durable_key,
            )
            session.add(run)
            session.flush()
            payload = {
                **cast(dict[str, Any], mission.params or {}),
                "vol_id": vol_id,
                "attempt": attempt,
                "phases": [stage],
                "stage_run_id": run.run_id,
                "upstream_artifact_ids": request.upstream_artifact_ids,
                "stage_parameters": parameters,
            }
            if not stage_jobs_enabled():
                enqueue_outbox(
                    session,
                    topic=TOPIC_MISSION,
                    event=build_stage_mission_event(payload),
                    key=vol_id,
                )
            return _serialize_stage_run(run)
    except HTTPException:
        raise
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent stage request already created this attempt",
        ) from error


@router.post(
    "/missions/{vol_id}/stages/runs/{run_id}/artifacts",
    status_code=status.HTTP_201_CREATED,
)
def publish_stage_artifact(
    vol_id: str,
    run_id: str,
    request: ArtifactCreate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> dict[str, Any]:
    with get_session() as session:
        mission = get_owned_mission(
            session,
            vol_id,
            principal,
            requested_owner=owner_subject,
            action="artifact_publish",
        )
        run: MissionStageRun | None = (
            session.query(MissionStageRun)
            .filter(
                MissionStageRun.mission_id == mission.id,
                MissionStageRun.run_id == run_id,
            )
            .first()
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Stage run not found")
        existing = (
            session.query(MissionArtifact)
            .filter(MissionArtifact.artifact_id == request.artifact_id)
            .first()
        )
        if existing is not None:
            if (
                existing.mission_id != mission.id
                or existing.stage_run_id != run.id
                or not _immutable_artifact_matches(
                    existing,
                    request,
                    set(request.parent_artifact_ids),
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Artifact identity already exists with different immutable data",
                )
            _mark_stage_run_succeeded(run)
            return {"artifact_id": existing.artifact_id, "status": "existing"}
        parents = _artifacts_for_request(
            session,
            mission,
            request.parent_artifact_ids,
        )
        artifact = MissionArtifact(
            artifact_id=request.artifact_id,
            mission_id=mission.id,
            stage_run_id=run.id,
            kind=request.kind,
            uri=request.uri,
            checksum_sha256=request.checksum_sha256,
            size_bytes=request.size_bytes,
            artifact_metadata=request.metadata,
        )
        session.add(artifact)
        session.flush()
        for parent in parents:
            session.add(
                MissionArtifactParent(
                    artifact_id=artifact.id,
                    parent_artifact_id=parent.id,
                )
            )
        _mark_stage_run_succeeded(run)
        queued_runs = _queue_ready_stage_runs(session, mission)
        return {
            "artifact_id": artifact.artifact_id,
            "status": "created",
            "queued_stage_run_ids": queued_runs,
        }
