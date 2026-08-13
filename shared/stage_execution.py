"""Reusable one-shot execution boundary for bounded stage containers."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from shared.database import (
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    get_session,
)
from shared.organization_saas import reserve_stage_output_storage
from shared.stage_artifacts import mark_stage_run_succeeded, release_ready_stage_runs
from shared.stage_contracts import STAGE_ARTIFACT_KINDS, StageId
from shared.tenancy import MissionObjectNamespace


class StageExecutionCancelled(RuntimeError):
    """Raised cooperatively after mission cancellation is persisted."""


class StageQualityGateRejected(RuntimeError):
    """Fail a stage while preserving its quality evidence for operators."""

    def __init__(
        self,
        message: str,
        *,
        quality_metrics: dict[str, Any],
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.quality_metrics = quality_metrics
        self.evidence = evidence or {}


@dataclass(frozen=True)
class StageArtifactInput:
    artifact_id: str
    kind: str
    uri: str
    checksum_sha256: str
    size_bytes: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StageExecutionContext:
    run_id: str
    mission_id: int
    organization_id: str
    vol_id: str
    workspace_prefix: str
    owner_subject: str
    stage: StageId
    attempt: int
    mission_attempt: int
    parameters: dict[str, Any]
    mission_parameters: dict[str, Any]
    inputs: tuple[StageArtifactInput, ...]
    run_provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def object_namespace(self) -> MissionObjectNamespace:
        return MissionObjectNamespace.from_binding(
            self.organization_id,
            self.vol_id,
            self.workspace_prefix,
        )


@dataclass(frozen=True)
class StageExecutionResult:
    kind: str
    uri: str
    checksum_sha256: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind or not self.uri:
            raise ValueError("A stage result requires kind and URI")
        if len(self.checksum_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.checksum_sha256
        ):
            raise ValueError("A stage result requires a lower-case SHA-256")
        if self.size_bytes < 0:
            raise ValueError("A stage result size cannot be negative")


class StageHandler(Protocol):
    def __call__(
        self,
        context: StageExecutionContext,
        control: StageExecutionControl,
    ) -> StageExecutionResult: ...


class StageSubtaskHandler(Protocol):
    def __call__(
        self,
        context: StageExecutionContext,
        control: StageExecutionControl,
    ) -> None: ...


_STAGE_RLS_TABLES = (
    "missions",
    "mission_stage_runs",
    "mission_artifacts",
    "mission_artifact_parents",
    "detection_shard_receipts",
)


def _require_stage_rls(session: Any) -> None:
    if os.getenv("DRONEAI_STAGE_RLS_REQUIRED", "").strip().lower() != "true":
        return
    if session.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Stage row-level security requires PostgreSQL")
    active = session.execute(
        text(
            "SELECT bool_and(row_security_active(table_name::regclass)) "
            "FROM unnest(CAST(:tables AS text[])) AS table_name"
        ),
        {"tables": list(_STAGE_RLS_TABLES)},
    ).scalar_one()
    if active is not True:
        raise RuntimeError(
            "Stage database role must be non-owner, NOBYPASSRLS, and "
            "covered by tenant row-level security"
        )


def load_stage_execution_context(
    run_id: str,
    expected_stage: StageId,
    *,
    expected_organization_id: str | None = None,
    expected_mission_id: int | None = None,
    expected_vol_id: str | None = None,
    expected_workspace_prefix: str | None = None,
    expected_owner_subject: str | None = None,
) -> StageExecutionContext:
    """Claim or rejoin one durable Kubernetes stage execution."""

    with get_session(organization_id=expected_organization_id) as session:
        _require_stage_rls(session)
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).with_for_update().one()
        mission = session.query(Mission).filter(Mission.id == run.mission_id).one()
        durable_binding = {
            "organization_id": cast(str, mission.organization_id),
            "mission_id": cast(int, mission.id),
            "vol_id": cast(str, mission.vol_id),
            "workspace_prefix": cast(str | None, mission.workspace_prefix),
            "owner_subject": cast(str, mission.owner_subject),
        }
        expected_binding = {
            "organization_id": expected_organization_id,
            "mission_id": expected_mission_id,
            "vol_id": expected_vol_id,
            "workspace_prefix": expected_workspace_prefix,
            "owner_subject": expected_owner_subject,
        }
        namespace = MissionObjectNamespace.from_binding(
            cast(str, durable_binding["organization_id"]),
            cast(str, durable_binding["vol_id"]),
            cast(str | None, durable_binding["workspace_prefix"]),
        )
        mismatches = [
            name
            for name, expected in expected_binding.items()
            if expected is not None and durable_binding[name] != expected
        ]
        if mismatches:
            raise ValueError(
                "Stage Job binding does not match durable mission fields: "
                + ", ".join(mismatches)
            )
        if run.stage != expected_stage:
            raise ValueError(
                f"Stage Job expected {expected_stage}, durable run is {run.stage}"
            )
        if run.executor != "kubernetes-job":
            raise ValueError("Stage run is not reserved for the Kubernetes Job executor")
        if run.status not in {"queued", "running"}:
            raise ValueError(f"Stage run is not executable from status {run.status}")
        if mission.status == "cancelled":
            raise StageExecutionCancelled(f"Mission {mission.vol_id} is cancelled")
        artifacts = cast(
            list[MissionArtifact],
            session.query(MissionArtifact).filter(
                MissionArtifact.mission_id == mission.id,
                MissionArtifact.artifact_id.in_(run.upstream_artifact_ids or []),
            ).all(),
        )
        artifact_by_id = {
            cast(str, artifact.artifact_id): artifact for artifact in artifacts
        }
        input_ids = cast(list[str], run.upstream_artifact_ids or [])
        if set(artifact_by_id) != set(input_ids):
            raise ValueError("One or more durable upstream artifacts are missing")
        now = datetime.now(UTC)
        record = cast(Any, run)
        record.status = "running"
        record.started_at = run.started_at or now
        record.heartbeat_at = now
        record.current_step = "EXECUTING"
        record.dispatch_error = None
        return StageExecutionContext(
            run_id=cast(str, run.run_id),
            mission_id=cast(int, mission.id),
            organization_id=cast(str, mission.organization_id),
            vol_id=cast(str, mission.vol_id),
            workspace_prefix=namespace.root,
            owner_subject=cast(str, mission.owner_subject),
            stage=cast(StageId, run.stage),
            attempt=cast(int, run.attempt),
            mission_attempt=cast(int, mission.retry_count or 0),
            parameters=cast(dict[str, Any], run.parameters or {}),
            mission_parameters=cast(dict[str, Any], mission.params or {}),
            run_provenance=cast(dict[str, Any], run.provenance or {}),
            inputs=tuple(
                StageArtifactInput(
                    artifact_id=artifact_id,
                    kind=cast(str, artifact_by_id[artifact_id].kind),
                    uri=cast(str, artifact_by_id[artifact_id].uri),
                    checksum_sha256=cast(
                        str,
                        artifact_by_id[artifact_id].checksum_sha256,
                    ),
                    size_bytes=cast(
                        int | None,
                        artifact_by_id[artifact_id].size_bytes,
                    ),
                    metadata=cast(
                        dict[str, Any],
                        artifact_by_id[artifact_id].artifact_metadata or {},
                    ),
                )
                for artifact_id in input_ids
            ),
        )


class StageExecutionControl:
    """Background heartbeat and cooperative cancellation signal."""

    def __init__(
        self,
        run_id: str,
        organization_id: str | None,
        interval_seconds: float = 15.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive")
        self.run_id = run_id
        self.organization_id = organization_id
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: Exception | None = None
        self._failure_lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def heartbeat(self) -> bool:
        with get_session(organization_id=self.organization_id) as session:
            run = session.query(MissionStageRun).filter(
                MissionStageRun.run_id == self.run_id
            ).with_for_update().one()
            mission = session.query(Mission).filter(Mission.id == run.mission_id).one()
            if mission.status == "cancelled" or run.status == "cancelled":
                self._cancelled.set()
                record = cast(Any, run)
                record.status = "cancelled"
                record.completed_at = datetime.now(UTC)
                return False
            record = cast(Any, run)
            record.heartbeat_at = datetime.now(UTC)
            return True

    def raise_if_cancelled(self) -> None:
        with self._failure_lock:
            failure = self._failure
        if failure is not None:
            raise RuntimeError("Stage heartbeat failed") from failure
        if self.cancelled or not self.heartbeat():
            raise StageExecutionCancelled(f"Stage run {self.run_id} was cancelled")

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                if not self.heartbeat():
                    return
            except Exception as error:
                with self._failure_lock:
                    self._failure = error
                return

    def __enter__(self) -> StageExecutionControl:
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"heartbeat-{self.run_id[:12]}",
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=min(2.0, self.interval_seconds))


def _artifact_id(context: StageExecutionContext, result: StageExecutionResult) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"droneai:{context.run_id}:{result.kind}:{result.checksum_sha256}",
        )
    )


def _publish_result(
    context: StageExecutionContext,
    result: StageExecutionResult,
) -> str:
    expected_kind = STAGE_ARTIFACT_KINDS[context.stage]
    if result.kind != expected_kind:
        raise ValueError(
            f"Stage {context.stage} must publish artifact kind {expected_kind}, "
            f"not {result.kind}"
        )
    artifact_id = _artifact_id(context, result)
    with get_session(organization_id=context.organization_id) as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == context.run_id
        ).with_for_update().one()
        mission = session.query(Mission).filter(Mission.id == run.mission_id).one()
        if mission.status == "cancelled" or run.status == "cancelled":
            raise StageExecutionCancelled(f"Mission {context.vol_id} was cancelled")
        if run.status != "running":
            raise ValueError(f"Stage result cannot publish from status {run.status}")
        existing = session.query(MissionArtifact).filter(
            MissionArtifact.artifact_id == artifact_id
        ).first()
        if existing is None:
            reserve_stage_output_storage(
                session,
                organization_id=context.organization_id,
                stage_run_id=context.run_id,
                artifact_id=artifact_id,
                output_bytes=result.size_bytes,
                actor_subject="system:stage-executor",
            )
            artifact = MissionArtifact(
                artifact_id=artifact_id,
                mission_id=mission.id,
                stage_run_id=run.id,
                kind=result.kind,
                uri=result.uri,
                checksum_sha256=result.checksum_sha256,
                size_bytes=result.size_bytes,
                artifact_metadata=result.metadata,
            )
            session.add(artifact)
            session.flush()
            parents = session.query(MissionArtifact).filter(
                MissionArtifact.mission_id == mission.id,
                MissionArtifact.artifact_id.in_(
                    [item.artifact_id for item in context.inputs]
                ),
            ).all()
            for parent in parents:
                session.add(
                    MissionArtifactParent(
                        artifact_id=artifact.id,
                        parent_artifact_id=parent.id,
                    )
                )
        else:
            parent_ids = {
                cast(str, edge.parent.artifact_id)
                for edge in existing.parent_edges
            }
            if (
                existing.mission_id != mission.id
                or existing.stage_run_id != run.id
                or existing.kind != result.kind
                or existing.uri != result.uri
                or existing.checksum_sha256 != result.checksum_sha256
                or existing.size_bytes != result.size_bytes
                or cast(dict[str, Any], existing.artifact_metadata or {})
                != result.metadata
                or parent_ids != {item.artifact_id for item in context.inputs}
            ):
                raise ValueError("Deterministic artifact identity has conflicting data")
        record = cast(Any, run)
        record.quality_metrics = result.quality_metrics
        record.provenance = {
            **cast(dict[str, Any], run.provenance or {}),
            **result.provenance,
        }
        mark_stage_run_succeeded(run)
        release_ready_stage_runs(session, mission)
    return artifact_id


def _mark_terminal(
    run_id: str,
    organization_id: str | None,
    status: str,
    message: str | None,
    *,
    quality_metrics: dict[str, Any] | None = None,
    rejection_evidence: dict[str, Any] | None = None,
) -> str:
    with get_session(organization_id=organization_id) as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).with_for_update().one()
        mission = session.query(Mission).filter(Mission.id == run.mission_id).one()
        effective_status = (
            "cancelled"
            if status == "failed"
            and (mission.status == "cancelled" or run.status == "cancelled")
            else status
        )
        record = cast(Any, run)
        record.status = effective_status
        record.current_step = effective_status.upper()
        record.error_message = None if effective_status == "cancelled" else message
        if quality_metrics is not None:
            record.quality_metrics = quality_metrics
        if rejection_evidence:
            record.provenance = {
                **cast(dict[str, Any], run.provenance or {}),
                "quality_gate_rejection": rejection_evidence,
            }
        record.heartbeat_at = datetime.now(UTC)
        record.completed_at = record.heartbeat_at
        return effective_status


def _prepare_execution(
    expected_stage: StageId,
    run_id: str | None,
    heartbeat_interval_seconds: float | None,
) -> tuple[str, StageExecutionContext, float]:
    durable_run_id = run_id or os.getenv("DRONEAI_STAGE_RUN_ID", "").strip()
    if not durable_run_id:
        raise ValueError("DRONEAI_STAGE_RUN_ID is required")
    organization_id = os.getenv("DRONEAI_ORGANIZATION_ID", "").strip() or None
    raw_mission_id = os.getenv("DRONEAI_MISSION_ID", "").strip()
    if raw_mission_id:
        if not raw_mission_id.isdigit() or int(raw_mission_id) < 1:
            raise ValueError("DRONEAI_MISSION_ID must be a positive integer")
        mission_id = int(raw_mission_id)
    else:
        mission_id = None
    context = load_stage_execution_context(
        durable_run_id,
        expected_stage,
        expected_organization_id=organization_id,
        expected_mission_id=mission_id,
        expected_vol_id=os.getenv("DRONEAI_VOL_ID", "").strip() or None,
        expected_workspace_prefix=(
            os.getenv("DRONEAI_WORKSPACE_PREFIX", "").strip() or None
        ),
        expected_owner_subject=(
            os.getenv("DRONEAI_OWNER_SUBJECT", "").strip() or None
        ),
    )
    interval = heartbeat_interval_seconds
    if interval is None:
        interval = float(os.getenv("DRONEAI_STAGE_HEARTBEAT_SECONDS", "15"))
    return durable_run_id, context, interval


def _record_execution_failure(
    durable_run_id: str,
    organization_id: str | None,
    error: Exception,
) -> None:
    """Persist the shared terminal failure policy for every stage executor."""

    rejection = error if isinstance(error, StageQualityGateRejected) else None
    terminal_status = _mark_terminal(
        durable_run_id,
        organization_id,
        "failed",
        str(error)[:4000],
        quality_metrics=(
            rejection.quality_metrics if rejection is not None else None
        ),
        rejection_evidence=(
            rejection.evidence if rejection is not None else None
        ),
    )
    if terminal_status == "cancelled":
        raise StageExecutionCancelled(
            f"Stage run {durable_run_id} was cancelled"
        ) from error


def execute_one_shot_stage(
    expected_stage: StageId,
    handler: StageHandler,
    *,
    run_id: str | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> str:
    durable_run_id, context, interval = _prepare_execution(
        expected_stage,
        run_id,
        heartbeat_interval_seconds,
    )
    try:
        with StageExecutionControl(
            durable_run_id,
            context.organization_id,
            interval,
        ) as control:
            result = handler(context, control)
            control.raise_if_cancelled()
        return _publish_result(context, result)
    except StageExecutionCancelled:
        _mark_terminal(
            durable_run_id,
            context.organization_id,
            "cancelled",
            None,
        )
        raise
    except Exception as error:
        _record_execution_failure(
            durable_run_id,
            context.organization_id,
            error,
        )
        raise


def execute_stage_subtask(
    expected_stage: StageId,
    handler: StageSubtaskHandler,
    *,
    run_id: str | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> None:
    """Execute one bounded child task without authority to publish an artifact."""

    durable_run_id, context, interval = _prepare_execution(
        expected_stage,
        run_id,
        heartbeat_interval_seconds,
    )
    try:
        with StageExecutionControl(
            durable_run_id,
            context.organization_id,
            interval,
        ) as control:
            handler(context, control)
            control.raise_if_cancelled()
    except StageExecutionCancelled:
        _mark_terminal(
            durable_run_id,
            context.organization_id,
            "cancelled",
            None,
        )
        raise
    except Exception as error:
        _record_execution_failure(
            durable_run_id,
            context.organization_id,
            error,
        )
        raise
