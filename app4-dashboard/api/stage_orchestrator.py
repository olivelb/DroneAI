"""Transactional control loop for opt-in bounded Kubernetes stage Jobs."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from shared.database import Mission, MissionStageRun, get_session
from shared.stage_contracts import STAGE_ORDER, ResourceClassId, StageId
from shared.stage_scheduler import (
    SchedulingLimits,
    StageAllocation,
    StageCandidate,
    select_stage_candidates,
)

from .kubernetes_jobs import (
    KubernetesApiError,
    KubernetesJobClient,
    SecretEnvironment,
    StageJobConfig,
    StageJobRequest,
    build_stage_job,
    stage_job_name,
)

logger = logging.getLogger("droneai.stage-orchestrator")
EXECUTOR_NAME = "kubernetes-job"
ACTIVE_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class StageExecutorConfig:
    image: str
    command: tuple[str, ...]
    gpu_architecture: str | None = None
    node_selector: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StageOrchestratorSettings:
    enabled: bool
    namespace: str
    poll_seconds: float
    limits: SchedulingLimits
    executors: dict[StageId, StageExecutorConfig]
    job_environment: tuple[tuple[str, str], ...] = ()
    job_secret_environment: tuple[SecretEnvironment, ...] = ()
    service_account_name: str = "stage-job-sa"
    active_deadline_seconds: int = 86_400
    ttl_seconds_after_finished: int = 3_600
    maximum_dispatch_attempts: int = 3


@dataclass(frozen=True)
class ReservedStageJob:
    request: StageJobRequest
    config: StageJobConfig
    job_name: str


def stage_jobs_enabled() -> bool:
    return os.getenv("DRONEAI_STAGE_JOBS_ENABLED", "false").strip().lower() == "true"


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _executor_catalog(raw: str) -> dict[StageId, StageExecutorConfig]:
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("DRONEAI_STAGE_EXECUTORS_JSON must be an object")
    result: dict[StageId, StageExecutorConfig] = {}
    for stage in STAGE_ORDER:
        item = payload.get(stage)
        if not isinstance(item, dict):
            raise ValueError(f"Missing one-shot executor configuration for {stage}")
        image = item.get("image")
        command = item.get("command")
        architecture = item.get("gpu_architecture")
        selector = item.get("node_selector") or {}
        if not isinstance(image, str) or not (
            re.search(r"@sha256:[0-9a-f]{64}$", image)
            or re.search(r":[0-9a-f]{7,40}$", image)
        ):
            raise ValueError(f"Executor image for {stage} must be immutable")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError(f"Executor command for {stage} must be a non-empty string list")
        if stage != "rasterization" and not isinstance(architecture, str):
            raise ValueError(f"GPU architecture must be declared for {stage}")
        if not isinstance(selector, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in selector.items()
        ):
            raise ValueError(f"Node selector for {stage} must contain string pairs")
        result[stage] = StageExecutorConfig(
            image=image,
            command=tuple(command),
            gpu_architecture=architecture,
            node_selector=tuple(sorted(selector.items())),
        )
    return result


def settings_from_environment() -> StageOrchestratorSettings:
    enabled = stage_jobs_enabled()
    resource_limits_raw = json.loads(
        os.getenv("DRONEAI_STAGE_RESOURCE_CONCURRENCY_JSON", "{}")
    )
    if not isinstance(resource_limits_raw, dict):
        raise ValueError("DRONEAI_STAGE_RESOURCE_CONCURRENCY_JSON must be an object")
    resource_limits = cast(
        dict[ResourceClassId, int],
        {str(key): int(value) for key, value in resource_limits_raw.items()},
    )
    storage_secret = os.getenv("DRONEAI_STAGE_STORAGE_SECRET_NAME", "drone-ai-storage")
    plain_environment = tuple(
        (name, os.environ[name])
        for name in (
            "KAFKA_BROKER",
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_REGION",
            "S3_PUBLIC_ENDPOINT",
        )
        if name in os.environ
    )
    secret_environment = tuple(
        SecretEnvironment(environment_name, storage_secret, os.getenv(key_variable, default_key))
        for environment_name, key_variable, default_key in (
            ("DATABASE_URL", "DRONEAI_STAGE_DATABASE_URL_SECRET_KEY", "database-url"),
            ("S3_ACCESS_KEY", "DRONEAI_STAGE_S3_ACCESS_KEY_SECRET_KEY", "s3-access-key"),
            ("S3_SECRET_KEY", "DRONEAI_STAGE_S3_SECRET_KEY_SECRET_KEY", "s3-secret-key"),
        )
    )
    return StageOrchestratorSettings(
        enabled=enabled,
        namespace=os.getenv("POD_NAMESPACE", "drone-ai"),
        poll_seconds=float(os.getenv("DRONEAI_STAGE_SCHEDULER_POLL_SECONDS", "5")),
        limits=SchedulingLimits(
            global_active=_positive_int("DRONEAI_STAGE_GLOBAL_CONCURRENCY", 2),
            per_owner_active=_positive_int("DRONEAI_STAGE_OWNER_CONCURRENCY", 1),
            per_mission_active=_positive_int("DRONEAI_STAGE_MISSION_CONCURRENCY", 1),
            resource_active=resource_limits,
        ),
        executors=(
            _executor_catalog(os.getenv("DRONEAI_STAGE_EXECUTORS_JSON", "{}"))
            if enabled
            else {}
        ),
        job_environment=plain_environment,
        job_secret_environment=secret_environment,
        service_account_name=os.getenv("DRONEAI_STAGE_JOB_SERVICE_ACCOUNT", "stage-job-sa"),
        active_deadline_seconds=_positive_int("DRONEAI_STAGE_JOB_ACTIVE_DEADLINE_SECONDS", 86_400),
        ttl_seconds_after_finished=int(
            os.getenv("DRONEAI_STAGE_JOB_TTL_SECONDS_AFTER_FINISHED", "3600")
        ),
        maximum_dispatch_attempts=_positive_int("DRONEAI_STAGE_MAX_DISPATCH_ATTEMPTS", 3),
    )


def _reserved_job(
    run: MissionStageRun,
    mission: Mission,
    settings: StageOrchestratorSettings,
) -> ReservedStageJob:
    stage = cast(StageId, run.stage)
    executor = settings.executors[stage]
    request = StageJobRequest(
        run_id=cast(str, run.run_id),
        mission_id=cast(int, mission.id),
        vol_id=cast(str, mission.vol_id),
        owner_subject=cast(str, mission.owner_subject),
        stage=stage,
        resource_class=cast(ResourceClassId, run.resource_class),
    )
    return ReservedStageJob(
        request=request,
        config=StageJobConfig(
            namespace=settings.namespace,
            image=executor.image,
            command=executor.command,
            service_account_name=settings.service_account_name,
            active_deadline_seconds=settings.active_deadline_seconds,
            ttl_seconds_after_finished=settings.ttl_seconds_after_finished,
            node_selector=executor.node_selector,
            environment=settings.job_environment,
            secret_environment=settings.job_secret_environment,
        ),
        job_name=stage_job_name(request.run_id),
    )


def reserve_ready_jobs(
    session: Any,
    settings: StageOrchestratorSettings,
    now: datetime,
) -> list[ReservedStageJob]:
    active_rows = session.query(MissionStageRun, Mission).join(
        Mission,
        Mission.id == MissionStageRun.mission_id,
    ).filter(
        MissionStageRun.executor == EXECUTOR_NAME,
        MissionStageRun.status.in_(ACTIVE_STATUSES),
    ).all()
    candidate_rows = session.query(MissionStageRun, Mission).join(
        Mission,
        Mission.id == MissionStageRun.mission_id,
    ).filter(
        MissionStageRun.status == "queued",
        MissionStageRun.executor.is_(None),
        MissionStageRun.dispatch_attempts < settings.maximum_dispatch_attempts,
        Mission.status != "cancelled",
    ).order_by(
        MissionStageRun.created_at,
        MissionStageRun.run_id,
    ).with_for_update(skip_locked=True).limit(500).all()
    active = [
        StageAllocation(
            run_id=cast(str, run.run_id),
            mission_id=cast(int, mission.id),
            owner_subject=cast(str, mission.owner_subject),
            stage=cast(StageId, run.stage),
            resource_class=cast(ResourceClassId, run.resource_class),
        )
        for run, mission in active_rows
    ]
    candidates = [
        StageCandidate(
            run_id=cast(str, run.run_id),
            mission_id=cast(int, mission.id),
            owner_subject=cast(str, mission.owner_subject),
            stage=cast(StageId, run.stage),
            resource_class=cast(ResourceClassId, run.resource_class),
            created_at=cast(datetime, run.created_at),
        )
        for run, mission in candidate_rows
    ]
    selected_ids = {
        item.run_id
        for item in select_stage_candidates(candidates, active, settings.limits)
    }
    reserved: list[ReservedStageJob] = []
    for run, mission in candidate_rows:
        if run.run_id not in selected_ids:
            continue
        executor = settings.executors[cast(StageId, run.stage)]
        run.executor = EXECUTOR_NAME
        run.job_name = stage_job_name(cast(str, run.run_id))
        run.scheduled_at = now
        run.dispatch_attempts = cast(int, run.dispatch_attempts) + 1
        run.dispatch_error = None
        run.provenance = {
            **cast(dict[str, Any], run.provenance or {}),
            "executor": EXECUTOR_NAME,
            "resource_class": run.resource_class,
            "gpu_architecture": executor.gpu_architecture,
        }
        reserved.append(_reserved_job(run, mission, settings))
    return reserved


def _record_dispatch_error(run_id: str, error: Exception, maximum_attempts: int) -> None:
    with get_session() as session:
        run = session.query(MissionStageRun).filter(MissionStageRun.run_id == run_id).with_for_update().one()
        run.dispatch_error = str(error)[:4000]
        if cast(int, run.dispatch_attempts) >= maximum_attempts:
            run.status = "failed"
            run.error_message = "Stage Job dispatch failed after bounded retries"
            run.completed_at = datetime.now(UTC)
        else:
            run.executor = None
            run.scheduled_at = None


def _create_job(client: KubernetesJobClient, reserved: ReservedStageJob) -> None:
    try:
        client.create(build_stage_job(reserved.request, reserved.config))
    except KubernetesApiError as error:
        if error.status_code != 409:
            raise


def dispatch_reserved_jobs(
    client: KubernetesJobClient,
    reserved: list[ReservedStageJob],
    maximum_attempts: int,
) -> None:
    for item in reserved:
        try:
            _create_job(client, item)
        except Exception as error:
            logger.exception("Unable to create stage Job %s", item.job_name)
            _record_dispatch_error(item.request.run_id, error, maximum_attempts)


def reconcile_stage_jobs(
    client: KubernetesJobClient,
    settings: StageOrchestratorSettings,
) -> None:
    with get_session() as session:
        rows = session.query(MissionStageRun, Mission).join(
            Mission,
            Mission.id == MissionStageRun.mission_id,
        ).filter(
            MissionStageRun.executor == EXECUTOR_NAME,
            MissionStageRun.status.in_((*ACTIVE_STATUSES, "cancelled")),
        ).with_for_update(skip_locked=True).all()
        now = datetime.now(UTC)
        for run, mission in rows:
            name = cast(str, run.job_name)
            if mission.status == "cancelled" or run.status == "cancelled":
                try:
                    client.delete(name)
                except KubernetesApiError as error:
                    if error.status_code != 404:
                        raise
                run.status = "cancelled"
                run.completed_at = now
                run.heartbeat_at = now
                continue
            try:
                job = client.get(name)
            except KubernetesApiError as error:
                if error.status_code != 404:
                    raise
                run.dispatch_attempts = cast(int, run.dispatch_attempts) + 1
                if cast(int, run.dispatch_attempts) > settings.maximum_dispatch_attempts:
                    run.status = "failed"
                    run.error_message = "Stage Job disappeared after bounded retries"
                    run.completed_at = now
                    continue
                try:
                    _create_job(client, _reserved_job(run, mission, settings))
                    run.dispatch_error = None
                except Exception as create_error:
                    run.dispatch_error = str(create_error)[:4000]
                    if cast(int, run.dispatch_attempts) >= settings.maximum_dispatch_attempts:
                        run.status = "failed"
                        run.error_message = "Stage Job recreation failed after bounded retries"
                        run.completed_at = now
                    else:
                        run.executor = None
                        run.scheduled_at = None
                continue
            status = cast(dict[str, Any], job.get("status") or {})
            run.heartbeat_at = now
            if int(status.get("active") or 0) > 0:
                run.status = "running"
                run.started_at = run.started_at or now
            elif int(status.get("failed") or 0) > 0:
                run.status = "failed"
                run.error_message = "Kubernetes stage Job failed"
                run.completed_at = now
            elif int(status.get("succeeded") or 0) > 0:
                run.status = "failed"
                run.error_message = "Stage Job exited without publishing its immutable artifact"
                run.completed_at = now


def orchestrator_tick(
    client: KubernetesJobClient,
    settings: StageOrchestratorSettings,
) -> None:
    reconcile_stage_jobs(client, settings)
    with get_session() as session:
        reserved = reserve_ready_jobs(session, settings, datetime.now(UTC))
    dispatch_reserved_jobs(client, reserved, settings.maximum_dispatch_attempts)


def run_stage_orchestrator(stop_event: threading.Event) -> None:
    settings = settings_from_environment()
    if not settings.enabled:
        return
    client = KubernetesJobClient(settings.namespace)
    while not stop_event.is_set():
        try:
            orchestrator_tick(client, settings)
        except Exception:
            logger.exception("Stage orchestrator tick failed")
        stop_event.wait(settings.poll_seconds)


def start_stage_orchestrator(stop_event: threading.Event) -> threading.Thread | None:
    settings = settings_from_environment()
    if not settings.enabled:
        return None
    thread = threading.Thread(
        target=run_stage_orchestrator,
        args=(stop_event,),
        daemon=True,
        name="stage-orchestrator",
    )
    thread.start()
    return thread
