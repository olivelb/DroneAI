"""Transactional control loop for opt-in bounded Kubernetes stage Jobs."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import or_, text

from shared.database import (
    Mission,
    MissionArtifact,
    MissionStageRun,
    Organization,
    get_session,
)
from shared.analysis_stages import sync_analysis_stage
from shared.deployment_mode import bounded_stage_jobs_enabled
from shared.detection_shard_receipts import complete_detection_shard_receipts
from shared.detection_sharding import (
    MAX_DETECTION_TILES,
    DetectionShardPlan,
    build_detection_shard_plan,
    parse_detection_shard_plan_descriptor,
)
from shared.organization_saas import append_usage_event, stage_run_limits
from shared.observability import (
    observe_control_loop,
    observe_stage_queue,
)
from shared.stage_contracts import (
    DEFAULT_STAGE_RESOURCE_CLASSES,
    RESOURCE_CLASSES,
    STAGE_ORDER,
    ResourceClassId,
    StageId,
    resource_class_for_stage,
    resource_class_meets_envelope,
)
from shared.stage_scheduler import (
    SchedulingLimits,
    StageAllocation,
    StageCandidate,
    select_stage_candidates,
)
from shared.tenancy import LEGACY_ORGANIZATION_ID

from .kubernetes_jobs import (
    IndexedJobConfig,
    KubernetesApiError,
    KubernetesJobClient,
    SecretEnvironment,
    StageJobConfig,
    StageJobRequest,
    StageJobToleration,
    StageJobWorkVolume,
    build_stage_job,
    stage_job_matches_expected,
    stage_job_name,
)

logger = logging.getLogger("droneai.stage-orchestrator")
EXECUTOR_NAME = "kubernetes-job"
ACTIVE_STATUSES = ("queued", "running")
SCHEDULER_LOCK_NAMESPACE = 0x44524F4E  # "DRON"
SCHEDULER_LOCK_KEY = 1
DETECTION_PHASE_KEY = "detection_execution_phase"
DETECTION_SHARDS_PHASE = "shards"
DETECTION_FINALIZER_PHASE = "finalizer"
DETECTION_REQUESTED_PARALLELISM_KEY = "requested_shard_parallelism"
DETECTION_EFFECTIVE_PARALLELISM_KEY = "effective_shard_parallelism"
DETECTION_INFERENCE_RESOURCE_CLASS_KEY = "detection_inference_resource_class"
CANCELLATION_JOB_CLEANUP_AT_KEY = "cancellation_job_cleanup_at"
GPU_ARCHITECTURE_LABEL = "droneai.io/gpu-architecture"
SCHEDULER_CANDIDATE_PAGE_SIZE = 500


@dataclass(frozen=True)
class StageExecutorConfig:
    image: str
    command: tuple[str, ...]
    gpu_architecture: str | None = None
    node_selector: tuple[tuple[str, str], ...] = ()
    tolerations: tuple[StageJobToleration, ...] = ()


@dataclass(frozen=True)
class StageOrchestratorSettings:
    enabled: bool
    namespace: str
    poll_seconds: float
    limits: SchedulingLimits
    executors: dict[StageId, StageExecutorConfig]
    job_environment: tuple[tuple[str, str], ...] = ()
    job_secret_environment: tuple[SecretEnvironment, ...] = ()
    job_secret_environment_by_stage: dict[StageId, tuple[SecretEnvironment, ...]] = (
        field(default_factory=dict)
    )
    detection_environment: tuple[tuple[str, str], ...] = ()
    detection_secret_environment: tuple[SecretEnvironment, ...] = ()
    service_account_name: str = "stage-job-sa"
    active_deadline_seconds: int = 86_400
    ttl_seconds_after_finished: int = 3_600
    runtime_class_name: str | None = None
    maximum_dispatch_attempts: int = 3
    maximum_candidates_per_pass: int = 5_000
    detection_fanout_enabled: bool = False
    detection_tiles_per_shard: int = 1_024
    detection_shard_parallelism: int = 2
    detection_maximum_tiles: int = MAX_DETECTION_TILES
    work_drives: dict[str, StageJobWorkVolume] = field(default_factory=dict)
    work_drive_default: str | None = None


@dataclass(frozen=True)
class ReservedStageJob:
    request: StageJobRequest
    config: StageJobConfig
    job_name: str


def stage_jobs_enabled() -> bool:
    return bool(bounded_stage_jobs_enabled())


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _strict_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return normalized == "true"


def _bounded_positive_int(name: str, default: int, maximum: int) -> int:
    value = _positive_int(name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def _executor_catalog(
    raw: str,
    *,
    require_digest: bool = False,
) -> dict[StageId, StageExecutorConfig]:
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
        tolerations = item.get("tolerations") or []
        if not isinstance(image, str):
            raise ValueError(f"Executor image for {stage} must be a string")
        digest_image = re.search(r"@sha256:[0-9a-f]{64}$", image)
        development_image = re.search(r":[0-9a-f]{7,40}$", image)
        if not digest_image and (require_digest or not development_image):
            requirement = (
                "use an OCI digest"
                if require_digest
                else "use an OCI digest or development Git-SHA tag"
            )
            raise ValueError(f"Executor image for {stage} must {requirement}")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError(
                f"Executor command for {stage} must be a non-empty string list"
            )
        default_resources = RESOURCE_CLASSES[DEFAULT_STAGE_RESOURCE_CLASSES[stage]]
        if (
            default_resources["gpu_count"] > 0
            and stage != "rasterization"
            and not isinstance(architecture, str)
        ):
            raise ValueError(f"GPU architecture must be declared for {stage}")
        if not isinstance(selector, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in selector.items()
        ):
            raise ValueError(f"Node selector for {stage} must contain string pairs")
        if not isinstance(tolerations, list) or not all(
            isinstance(toleration, dict) for toleration in tolerations
        ):
            raise ValueError(f"Tolerations for {stage} must be a list of objects")
        parsed_tolerations = tuple(
            StageJobToleration(
                key=toleration.get("key", ""),
                operator=toleration.get("operator", "Equal"),
                value=toleration.get("value"),
                effect=toleration.get("effect"),
                toleration_seconds=toleration.get("toleration_seconds"),
            )
            for toleration in tolerations
        )
        result[stage] = StageExecutorConfig(
            image=image,
            command=tuple(command),
            gpu_architecture=architecture,
            node_selector=tuple(sorted(selector.items())),
            tolerations=parsed_tolerations,
        )
    return result


def _work_drive_catalog(
    raw: str,
    *,
    empty_dir_size_limit: str,
) -> dict[str, StageJobWorkVolume]:
    payload = json.loads(raw or "[]")
    if not isinstance(payload, list):
        raise ValueError("DRONEAI_STAGE_WORK_DRIVES_JSON must be a list")
    result: dict[str, StageJobWorkVolume] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Stage Job work drive entries must be objects")
        name = item.get("name")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
            name,
        ):
            raise ValueError("Stage Job work drive name is invalid")
        if name in result:
            raise ValueError(f"Duplicate Stage Job work drive: {name}")
        host_path = item.get("hostPath")
        claim_name = item.get("existingClaim")
        drive_type = item.get("type")
        configured_sources = sum(
            (
                bool(host_path),
                bool(claim_name),
                drive_type == "emptyDir",
            )
        )
        if configured_sources != 1:
            raise ValueError(
                f"Stage Job work drive {name} requires one supported source"
            )
        if host_path:
            source = {"hostPath": {"path": host_path, "type": "Directory"}}
        elif claim_name:
            source = {"persistentVolumeClaim": {"claimName": claim_name}}
        else:
            source = {"emptyDir": {"sizeLimit": empty_dir_size_limit}}
        result[name] = StageJobWorkVolume(source)
    return result


def _storage_secret_environment(secret_name: str) -> tuple[SecretEnvironment, ...]:
    return tuple(
        SecretEnvironment(
            environment_name,
            secret_name,
            os.getenv(key_variable, default_key),
        )
        for environment_name, key_variable, default_key in (
            (
                "DATABASE_URL",
                "DRONEAI_STAGE_DATABASE_URL_SECRET_KEY",
                "stage-database-url",
            ),
            (
                "S3_ACCESS_KEY",
                "DRONEAI_STAGE_S3_ACCESS_KEY_SECRET_KEY",
                "s3-access-key",
            ),
            (
                "S3_SECRET_KEY",
                "DRONEAI_STAGE_S3_SECRET_KEY_SECRET_KEY",
                "s3-secret-key",
            ),
        )
    )


def _scoped_stage_secret_environment(
    raw: str,
    *,
    enabled: bool,
) -> dict[StageId, tuple[SecretEnvironment, ...]]:
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON must be an object")
    unknown = sorted(set(payload) - set(STAGE_ORDER))
    if unknown:
        raise ValueError(
            "Unknown stage credential Secret entries: " + ", ".join(unknown)
        )
    environment = os.getenv("DRONEAI_ENV", "development").strip().lower()
    protected = environment in {"staging", "production"}
    if not payload:
        if enabled and protected:
            raise ValueError(
                f"{environment} stage Jobs require one distinct credential Secret for every stage"
            )
        return {}
    missing = [stage for stage in STAGE_ORDER if stage not in payload]
    if missing:
        raise ValueError(
            "Missing stage credential Secret entries: " + ", ".join(missing)
        )
    names: dict[StageId, str] = {}
    for stage in STAGE_ORDER:
        value = payload[stage]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Credential Secret for {stage} must be a non-empty name")
        names[stage] = value.strip()
    if len(set(names.values())) != len(names):
        raise ValueError("Every stage must use a distinct credential Secret")
    return {
        stage: _storage_secret_environment(secret_name)
        for stage, secret_name in names.items()
    }


def settings_from_environment() -> StageOrchestratorSettings:
    enabled = stage_jobs_enabled()
    deployment_environment = os.getenv("DRONEAI_ENV", "development").strip().lower()
    protected_environment = deployment_environment in {"staging", "production"}
    detection_fanout_enabled = _strict_bool("DRONEAI_DETECTION_FANOUT_ENABLED")
    selective_restore_enabled = _strict_bool(
        "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED"
    )
    if detection_fanout_enabled and not selective_restore_enabled:
        raise ValueError(
            "Detection fan-out requires selective restore"
        )
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
    scoped_secret_environment = _scoped_stage_secret_environment(
        os.getenv("DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON", "{}"),
        enabled=enabled,
    )
    plain_environment = tuple(
        (name, os.environ[name])
        for name in (
            "S3_ENDPOINT",
            "S3_BUCKET",
            "S3_REGION",
            "S3_PUBLIC_ENDPOINT",
            "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED",
        )
        if name in os.environ
    )
    if enabled and protected_environment:
        plain_environment += (("DRONEAI_STAGE_RLS_REQUIRED", "true"),)
    secret_environment = _storage_secret_environment(storage_secret)
    detection_environment: tuple[tuple[str, str], ...] = (
        ("HF_HOME", "/cache/huggingface"),
        ("HF_HUB_CACHE", "/cache/huggingface/hub"),
        ("TRANSFORMERS_CACHE", "/cache/huggingface/transformers"),
        (
            "SAM3_MODEL_ID",
            os.getenv("DRONEAI_STAGE_SAM3_MODEL_ID", "facebook/sam3"),
        ),
        (
            "SAM3_MODEL_REVISION",
            os.getenv(
                "DRONEAI_STAGE_SAM3_MODEL_REVISION",
                "3c879f39826c281e95690f02c7821c4de09afae7",
            ),
        ),
    )
    sam3_artifact_sha256 = os.getenv(
        "DRONEAI_STAGE_SAM3_ARTIFACT_SHA256", ""
    ).strip()
    if sam3_artifact_sha256:
        detection_environment += (
            ("SAM3_MODEL_SHA256", sam3_artifact_sha256),
        )
    detection_secret_environment = (
        SecretEnvironment(
            "HF_TOKEN",
            os.getenv("DRONEAI_STAGE_HF_TOKEN_SECRET_NAME", "hf-token"),
            os.getenv("DRONEAI_STAGE_HF_TOKEN_SECRET_KEY", "HF_TOKEN"),
        ),
    )
    work_drives = _work_drive_catalog(
        os.getenv("DRONEAI_STAGE_WORK_DRIVES_JSON", "[]"),
        empty_dir_size_limit=os.getenv(
            "DRONEAI_STAGE_WORK_EMPTY_DIR_SIZE_LIMIT",
            "100Gi",
        ),
    )
    work_drive_default = (
        os.getenv("DRONEAI_STAGE_WORK_DRIVE_DEFAULT", "").strip() or None
    )
    if work_drives and work_drive_default not in work_drives:
        raise ValueError(
            "DRONEAI_STAGE_WORK_DRIVE_DEFAULT must name a configured work drive"
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
            _executor_catalog(
                os.getenv("DRONEAI_STAGE_EXECUTORS_JSON", "{}"),
                require_digest=protected_environment,
            )
            if enabled
            else {}
        ),
        job_environment=plain_environment,
        job_secret_environment=secret_environment,
        job_secret_environment_by_stage=scoped_secret_environment,
        detection_environment=detection_environment,
        detection_secret_environment=detection_secret_environment,
        service_account_name=os.getenv(
            "DRONEAI_STAGE_JOB_SERVICE_ACCOUNT", "stage-job-sa"
        ),
        active_deadline_seconds=_positive_int(
            "DRONEAI_STAGE_JOB_ACTIVE_DEADLINE_SECONDS", 86_400
        ),
        ttl_seconds_after_finished=int(
            os.getenv("DRONEAI_STAGE_JOB_TTL_SECONDS_AFTER_FINISHED", "3600")
        ),
        runtime_class_name=(
            os.getenv("DRONEAI_STAGE_JOB_RUNTIME_CLASS", "").strip() or None
        ),
        maximum_dispatch_attempts=_positive_int(
            "DRONEAI_STAGE_MAX_DISPATCH_ATTEMPTS", 3
        ),
        maximum_candidates_per_pass=_positive_int(
            "DRONEAI_STAGE_SCHEDULER_MAX_CANDIDATES", 5_000
        ),
        detection_fanout_enabled=detection_fanout_enabled,
        detection_tiles_per_shard=_positive_int(
            "DRONEAI_DETECTION_TILES_PER_SHARD",
            1_024,
        ),
        detection_shard_parallelism=_positive_int(
            "DRONEAI_DETECTION_SHARD_PARALLELISM",
            2,
        ),
        detection_maximum_tiles=_bounded_positive_int(
            "DRONEAI_DETECTION_MAXIMUM_TILES",
            MAX_DETECTION_TILES,
            MAX_DETECTION_TILES,
        ),
        work_drives=work_drives,
        work_drive_default=work_drive_default,
    )


def _detection_phase(run: MissionStageRun) -> str | None:
    provenance = cast(dict[str, Any], getattr(run, "provenance", None) or {})
    value = provenance.get(DETECTION_PHASE_KEY)
    return value if isinstance(value, str) else None


def _detection_job_name(run: MissionStageRun) -> str:
    identity = cast(str, run.run_id)
    if _detection_phase(run) == DETECTION_FINALIZER_PHASE:
        identity = f"{identity}-finalizer"
    return stage_job_name(identity)


def _integer_value(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(cast(Any, value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an integer") from error
    return result


def _prepare_detection_fanout(
    session: Any,
    run: MissionStageRun,
    settings: StageOrchestratorSettings,
) -> DetectionShardPlan | None:
    if run.stage != "detection" or not settings.detection_fanout_enabled:
        return None
    phase = _detection_phase(run)
    if phase == DETECTION_FINALIZER_PHASE:
        return None
    if phase == DETECTION_SHARDS_PHASE:
        provenance = cast(dict[str, Any], run.provenance or {})
        return parse_detection_shard_plan_descriptor(
            provenance.get("detection_shard_plan")
        )
    upstream_ids = cast(list[Any], run.upstream_artifact_ids or [])
    if (
        len(upstream_ids) != 1
        or not isinstance(upstream_ids[0], str)
        or not upstream_ids[0]
    ):
        raise ValueError(
            "Detection fan-out requires exactly one immutable upstream artifact"
        )
    artifact = (
        session.query(MissionArtifact)
        .filter(
            MissionArtifact.artifact_id == upstream_ids[0],
            MissionArtifact.mission_id == run.mission_id,
        )
        .one_or_none()
    )
    if (
        artifact is None
        or artifact.kind != "raster_product_workspace"
        or artifact.stage_run.stage != "rasterization"
        or artifact.stage_run.status != "succeeded"
    ):
        raise ValueError("Detection fan-out requires its exact raster product artifact")
    metrics = cast(dict[str, Any], artifact.stage_run.quality_metrics or {})
    width = _integer_value(metrics.get("width"), "Raster width")
    height = _integer_value(metrics.get("height"), "Raster height")
    parameters = cast(dict[str, Any], run.parameters or {})
    raw_ai = parameters.get("ai") or {}
    if not isinstance(raw_ai, dict):
        raise ValueError("Detection stage AI parameters must be an object")
    ai = cast(dict[str, Any], raw_ai)
    tile_size = _integer_value(ai.get("tile_size") or 1_024, "Detection tile size")
    overlap = _integer_value(
        (
            ai.get("tile_overlap")
            if ai.get("tile_overlap") is not None
            else tile_size // 4
        ),
        "Detection tile overlap",
    )
    plan = build_detection_shard_plan(
        width,
        height,
        tile_size,
        overlap,
        tiles_per_shard=settings.detection_tiles_per_shard,
        maximum_tiles=settings.detection_maximum_tiles,
    )
    provenance = cast(dict[str, Any], run.provenance or {})
    if plan.shard_count < 2:
        run.provenance = {
            **provenance,
            "detection_execution_mode": "monolithic",
            "detection_planned_tile_count": plan.tile_count,
        }
        return None
    run.provenance = {
        **provenance,
        "detection_execution_mode": "fanout-fanin",
        "detection_shard_plan": plan.descriptor(),
        DETECTION_PHASE_KEY: DETECTION_SHARDS_PHASE,
    }
    return plan


def _detection_shard_count(run: MissionStageRun) -> int:
    provenance = cast(dict[str, Any], run.provenance or {})
    plan = parse_detection_shard_plan_descriptor(provenance.get("detection_shard_plan"))
    return _integer_value(plan.shard_count, "Detection shard count")


def _requested_resource_units(
    run: MissionStageRun,
    settings: StageOrchestratorSettings,
) -> int:
    if run.stage != "detection" or _detection_phase(run) != DETECTION_SHARDS_PHASE:
        return 1
    return min(settings.detection_shard_parallelism, _detection_shard_count(run))


def _active_resource_units(
    run: MissionStageRun,
) -> int:
    if run.stage != "detection" or _detection_phase(run) != DETECTION_SHARDS_PHASE:
        return 1
    provenance = cast(dict[str, Any], run.provenance or {})
    shard_count = _detection_shard_count(run)
    value = provenance.get(DETECTION_EFFECTIVE_PARALLELISM_KEY)
    try:
        effective = _integer_value(value, "Effective detection shard parallelism")
    except ValueError:
        # A Job dispatched before physical accounting may still be running.
        # Count every shard conservatively until that legacy Job terminates.
        return shard_count
    if not 1 <= effective <= shard_count:
        return shard_count
    return effective


def _reserved_job(
    run: MissionStageRun,
    mission: Mission,
    settings: StageOrchestratorSettings,
) -> ReservedStageJob:
    stage = cast(StageId, run.stage)
    executor = settings.executors[stage]
    phase = _detection_phase(run)
    detection_inference = stage == "detection" and phase != DETECTION_FINALIZER_PHASE
    detection_environment = (
        settings.detection_environment if detection_inference else ()
    )
    detection_secret_environment = (
        settings.detection_secret_environment if detection_inference else ()
    )
    scoped_secret_environment = settings.job_secret_environment_by_stage.get(
        stage,
        settings.job_secret_environment,
    )
    indexed = None
    name_suffix = None
    phase_environment: tuple[tuple[str, str], ...] = ()
    if stage == "detection" and phase == DETECTION_SHARDS_PHASE:
        provenance = cast(dict[str, Any], run.provenance or {})
        plan = parse_detection_shard_plan_descriptor(
            provenance.get("detection_shard_plan")
        )
        try:
            requested_parallelism = _integer_value(
                provenance.get(DETECTION_REQUESTED_PARALLELISM_KEY),
                "Requested detection shard parallelism",
            )
        except ValueError:
            # Jobs created before physical-unit accounting did not persist the
            # configured parallelism. Preserve their previous bounded behavior
            # when a missing Kubernetes Job has to be reconstructed.
            requested_parallelism = min(
                settings.detection_shard_parallelism,
                plan.shard_count,
            )
        try:
            effective_parallelism = _integer_value(
                provenance.get(DETECTION_EFFECTIVE_PARALLELISM_KEY),
                "Effective detection shard parallelism",
            )
        except ValueError:
            effective_parallelism = requested_parallelism
        if (
            not 1
            <= effective_parallelism
            <= min(
                requested_parallelism,
                plan.shard_count,
            )
        ):
            raise ValueError(
                "Effective detection shard parallelism exceeds its reservation"
            )
        indexed = IndexedJobConfig(
            completions=plan.shard_count,
            parallelism=effective_parallelism,
        )
        phase_environment = (("DRONEAI_DETECTION_EXECUTION_MODE", "shard"),)
    elif stage == "detection" and phase == DETECTION_FINALIZER_PHASE:
        name_suffix = "finalizer"
        phase_environment = (("DRONEAI_DETECTION_EXECUTION_MODE", "finalizer"),)
    run_parameters = cast(dict[str, Any], getattr(run, "parameters", None) or {})
    mission_parameters = cast(
        dict[str, Any],
        getattr(mission, "params", None) or {},
    )
    work_drive = (
        run_parameters.get("work_drive")
        or mission_parameters.get("work_drive")
        or settings.work_drive_default
    )
    work_volume = None
    if settings.work_drives:
        if not isinstance(work_drive, str) or work_drive not in settings.work_drives:
            raise ValueError("Stage Job work_drive is not configured")
        work_volume = settings.work_drives[work_drive]
    organization_id = cast(str | None, mission.organization_id)
    workspace_prefix = cast(str | None, mission.workspace_prefix)
    if not organization_id or not workspace_prefix:
        raise ValueError(
            "Bounded stage execution requires a durable organization and mission workspace prefix"
        )
    request = StageJobRequest(
        run_id=cast(str, run.run_id),
        mission_id=cast(int, mission.id),
        organization_id=organization_id,
        vol_id=cast(str, mission.vol_id),
        workspace_prefix=workspace_prefix,
        owner_subject=cast(str, mission.owner_subject),
        stage=stage,
        resource_class=(
            "cpu-standard"
            if phase == DETECTION_FINALIZER_PHASE
            else cast(ResourceClassId, run.resource_class)
        ),
    )
    node_selector = dict(executor.node_selector)
    if phase != DETECTION_FINALIZER_PHASE and executor.gpu_architecture:
        configured_architecture = node_selector.get(GPU_ARCHITECTURE_LABEL)
        if configured_architecture not in {None, executor.gpu_architecture}:
            raise ValueError(
                "Executor GPU architecture conflicts with its node selector"
            )
        node_selector[GPU_ARCHITECTURE_LABEL] = executor.gpu_architecture
    return ReservedStageJob(
        request=request,
        config=StageJobConfig(
            namespace=settings.namespace,
            image=executor.image,
            command=executor.command,
            service_account_name=settings.service_account_name,
            active_deadline_seconds=settings.active_deadline_seconds,
            ttl_seconds_after_finished=settings.ttl_seconds_after_finished,
            runtime_class_name=settings.runtime_class_name,
            node_selector=(
                ()
                if phase == DETECTION_FINALIZER_PHASE
                else tuple(sorted(node_selector.items()))
            ),
            tolerations=(
                () if phase == DETECTION_FINALIZER_PHASE else executor.tolerations
            ),
            environment=(
                settings.job_environment + detection_environment + phase_environment
            ),
            secret_environment=(
                scoped_secret_environment + detection_secret_environment
            ),
            indexed=indexed,
            name_suffix=name_suffix,
            work_volume=work_volume,
        ),
        job_name=_detection_job_name(run),
    )


def _repair_underprovisioned_resource_class(run: MissionStageRun) -> None:
    """Upgrade legacy rows before they can be dispatched or reconstructed."""
    stage = cast(StageId, run.stage)
    actual = cast(ResourceClassId, run.resource_class)
    if stage == "detection" and _detection_phase(run) == DETECTION_FINALIZER_PHASE:
        if actual != "cpu-standard":
            run.resource_class = "cpu-standard"
            run.provenance = {
                **cast(dict[str, Any], run.provenance or {}),
                DETECTION_INFERENCE_RESOURCE_CLASS_KEY: actual,
            }
        return
    parameters = cast(dict[str, Any], run.parameters or {})
    required = resource_class_for_stage(stage, parameters)
    if resource_class_meets_envelope(actual, required):
        return
    logger.warning(
        "Repairing underprovisioned stage run %s from %s to %s",
        run.run_id,
        actual,
        required,
    )
    run.resource_class = required
    run.provenance = {
        **cast(dict[str, Any], run.provenance or {}),
        "resource_class_repaired_from": actual,
    }


def _try_acquire_scheduler_reservation_lock(session: Any) -> bool:
    """Serialize capacity reservation across all PostgreSQL API replicas."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return True
    acquired = session.execute(
        text("SELECT pg_try_advisory_xact_lock(:namespace, :lock_key)"),
        {
            "namespace": SCHEDULER_LOCK_NAMESPACE,
            "lock_key": SCHEDULER_LOCK_KEY,
        },
    ).scalar_one()
    return bool(acquired)


def _scheduler_tenant(mission: Mission) -> str:
    organization_id = cast(str, mission.organization_id)
    if organization_id == LEGACY_ORGANIZATION_ID:
        return cast(str, mission.owner_subject)
    return organization_id


def reserve_ready_jobs(
    session: Any,
    settings: StageOrchestratorSettings,
    now: datetime,
) -> list[ReservedStageJob]:
    if not _try_acquire_scheduler_reservation_lock(session):
        logger.debug("Another scheduler replica owns the reservation transaction")
        return []
    active_rows = (
        session.query(MissionStageRun, Mission)
        .join(
            Mission,
            Mission.id == MissionStageRun.mission_id,
        )
        .filter(
            MissionStageRun.executor == EXECUTOR_NAME,
            MissionStageRun.status.in_(ACTIVE_STATUSES),
        )
        .all()
    )
    candidate_query = (
        session.query(MissionStageRun, Mission)
        .join(
            Mission,
            Mission.id == MissionStageRun.mission_id,
        )
        .outerjoin(
            Organization,
            Organization.id == Mission.organization_id,
        )
        .filter(
            MissionStageRun.status == "queued",
            MissionStageRun.executor.is_(None),
            MissionStageRun.dispatch_attempts < settings.maximum_dispatch_attempts,
            Mission.status.notin_(("cancelled", "deleting", "deletion_failed")),
            or_(
                Organization.status == "active",
                Mission.organization_id == LEGACY_ORGANIZATION_ID,
            ),
        )
        .order_by(
            MissionStageRun.created_at,
            MissionStageRun.run_id,
        )
        .with_for_update(skip_locked=True)
    )
    candidate_rows: list[tuple[MissionStageRun, Mission]] = []
    offset = 0
    while len(candidate_rows) < settings.maximum_candidates_per_pass:
        page_limit = min(
            SCHEDULER_CANDIDATE_PAGE_SIZE,
            settings.maximum_candidates_per_pass - len(candidate_rows),
        )
        page = candidate_query.offset(offset).limit(page_limit).all()
        candidate_rows.extend(page)
        if len(page) < page_limit:
            break
        offset += len(page)
    prepared_candidate_rows = []
    for run, mission in candidate_rows:
        _repair_underprovisioned_resource_class(run)
        try:
            _prepare_detection_fanout(session, run, settings)
        except ValueError as error:
            run.status = "failed"
            run.error_message = f"Invalid detection fan-out plan: {error}"
            run.completed_at = now
            sync_analysis_stage(session, run)
            continue
        prepared_candidate_rows.append((run, mission))
    candidate_rows = prepared_candidate_rows
    organization_ids = {
        cast(str, mission.organization_id)
        for _run, mission in (*active_rows, *candidate_rows)
        if mission.organization_id != LEGACY_ORGANIZATION_ID
    }
    commercial_limits = stage_run_limits(
        session,
        organization_ids,
        platform_limit=settings.limits.per_owner_active,
    )
    effective_owner_limits = dict(settings.limits.owner_active)
    for organization_id, limit in commercial_limits.items():
        effective_owner_limits[organization_id] = min(
            effective_owner_limits.get(
                organization_id,
                settings.limits.per_owner_active,
            ),
            limit,
        )
    effective_limits = replace(
        settings.limits,
        owner_active=effective_owner_limits,
    )
    active = [
        StageAllocation(
            run_id=cast(str, run.run_id),
            mission_id=cast(int, mission.id),
            owner_subject=_scheduler_tenant(mission),
            stage=cast(StageId, run.stage),
            resource_class=cast(ResourceClassId, run.resource_class),
            resource_units=_active_resource_units(run),
        )
        for run, mission in active_rows
    ]
    candidates = [
        StageCandidate(
            run_id=cast(str, run.run_id),
            mission_id=cast(int, mission.id),
            owner_subject=_scheduler_tenant(mission),
            stage=cast(StageId, run.stage),
            resource_class=cast(ResourceClassId, run.resource_class),
            resource_units=_requested_resource_units(run, settings),
            created_at=cast(datetime, run.created_at),
        )
        for run, mission in candidate_rows
    ]
    selected = {
        item.run_id: item
        for item in select_stage_candidates(candidates, active, effective_limits)
    }
    reserved: list[ReservedStageJob] = []
    for run, mission in candidate_rows:
        allocation = selected.get(cast(str, run.run_id))
        if allocation is None:
            continue
        executor = settings.executors[cast(StageId, run.stage)]
        if run.stage == "detection" and _detection_phase(run) == DETECTION_SHARDS_PHASE:
            run.provenance = {
                **cast(dict[str, Any], run.provenance or {}),
                DETECTION_REQUESTED_PARALLELISM_KEY: _requested_resource_units(
                    run,
                    settings,
                ),
                DETECTION_EFFECTIVE_PARALLELISM_KEY: allocation.resource_units,
            }
        try:
            reserved_job = _reserved_job(run, mission, settings)
        except ValueError as error:
            run.status = "failed"
            run.error_message = f"Invalid Stage Job workspace configuration: {error}"
            run.completed_at = now
            continue
        run.executor = EXECUTOR_NAME
        run.job_name = _detection_job_name(run)
        run.scheduled_at = now
        run.dispatch_attempts = cast(int, run.dispatch_attempts) + 1
        run.dispatch_error = None
        run.provenance = {
            **cast(dict[str, Any], run.provenance or {}),
            "executor": EXECUTOR_NAME,
            "resource_class": run.resource_class,
            "resource_units": allocation.resource_units,
            "gpu_architecture": (
                None
                if run.resource_class == "cpu-standard"
                else executor.gpu_architecture
            ),
        }
        append_usage_event(
            session,
            organization_id=cast(str, mission.organization_id),
            action="stage_scheduled",
            resource_type="stage_run",
            resource_id=cast(str, run.run_id),
            actor_subject="system:stage-scheduler",
            quantity=allocation.resource_units,
            unit="resource_units",
            idempotency_key=(f"stage-scheduled:{run.run_id}:{run.dispatch_attempts}"),
            details={
                "stage": run.stage,
                "resource_class": run.resource_class,
                "mission_id": mission.vol_id,
            },
        )
        reserved.append(reserved_job)
    for run, _mission in candidate_rows:
        sync_analysis_stage(session, run)
    return reserved


def _record_dispatch_error(
    run_id: str, error: Exception, maximum_attempts: int
) -> None:
    with get_session() as session:
        run = (
            session.query(MissionStageRun)
            .filter(MissionStageRun.run_id == run_id)
            .with_for_update()
            .one()
        )
        run.dispatch_error = str(error)[:4000]
        if cast(int, run.dispatch_attempts) >= maximum_attempts:
            run.status = "failed"
            run.error_message = "Stage Job dispatch failed after bounded retries"
            run.completed_at = datetime.now(UTC)
        else:
            run.executor = None
            run.scheduled_at = None
        sync_analysis_stage(session, run)


def _create_job(client: KubernetesJobClient, reserved: ReservedStageJob) -> None:
    job = build_stage_job(reserved.request, reserved.config)
    if job["metadata"]["name"] != reserved.job_name:
        raise RuntimeError("Reserved stage Job identity does not match its manifest")
    try:
        client.create(job)
    except KubernetesApiError as error:
        if error.status_code != 409:
            raise
        existing = client.get(reserved.job_name)
        if not stage_job_matches_expected(existing, job):
            raise RuntimeError(
                "Existing Kubernetes Job conflicts with the reserved Stage Job manifest"
            ) from error


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


def _job_failure_message(status: dict[str, Any]) -> str:
    prefix = "Kubernetes stage Job failed"
    conditions = status.get("conditions") or []
    if not isinstance(conditions, list):
        return prefix
    for condition in reversed(conditions):
        if not isinstance(condition, dict):
            continue
        if condition.get("type") not in {"Failed", "FailureTarget"}:
            continue
        if str(condition.get("status", "True")).lower() != "true":
            continue
        details = [
            str(value).strip()
            for value in (condition.get("reason"), condition.get("message"))
            if isinstance(value, str) and value.strip()
        ]
        if details:
            return f"{prefix}: {': '.join(details)}"[:4000]
    return prefix


def reconcile_stage_jobs(
    client: KubernetesJobClient,
    settings: StageOrchestratorSettings,
) -> None:
    with get_session() as session:
        rows = (
            session.query(MissionStageRun, Mission)
            .join(
                Mission,
                Mission.id == MissionStageRun.mission_id,
            )
            .filter(
                MissionStageRun.executor == EXECUTOR_NAME,
                MissionStageRun.status.in_((*ACTIVE_STATUSES, "cancelled")),
            )
            .with_for_update(skip_locked=True)
            .all()
        )
        now = datetime.now(UTC)
        for run, mission in rows:
            name = cast(str, run.job_name)
            if mission.status == "cancelled" or run.status == "cancelled":
                provenance = cast(dict[str, Any], run.provenance or {})
                if CANCELLATION_JOB_CLEANUP_AT_KEY not in provenance:
                    try:
                        client.delete(name)
                    except KubernetesApiError as error:
                        if error.status_code != 404:
                            raise
                    run.provenance = {
                        **provenance,
                        CANCELLATION_JOB_CLEANUP_AT_KEY: now.isoformat(),
                    }
                run.status = "cancelled"
                run.completed_at = run.completed_at or now
                run.heartbeat_at = run.heartbeat_at or now
                continue
            try:
                job = client.get(name)
            except KubernetesApiError as error:
                if error.status_code != 404:
                    raise
                run.dispatch_attempts = cast(int, run.dispatch_attempts) + 1
                if (
                    cast(int, run.dispatch_attempts)
                    > settings.maximum_dispatch_attempts
                ):
                    run.status = "failed"
                    run.error_message = "Stage Job disappeared after bounded retries"
                    run.completed_at = now
                    continue
                try:
                    _repair_underprovisioned_resource_class(run)
                    _create_job(client, _reserved_job(run, mission, settings))
                    run.dispatch_error = None
                except Exception as create_error:
                    run.dispatch_error = str(create_error)[:4000]
                    if (
                        cast(int, run.dispatch_attempts)
                        >= settings.maximum_dispatch_attempts
                    ):
                        run.status = "failed"
                        run.error_message = (
                            "Stage Job recreation failed after bounded retries"
                        )
                        run.completed_at = now
                continue
            status = cast(dict[str, Any], job.get("status") or {})
            run.heartbeat_at = now
            if int(status.get("active") or 0) > 0:
                run.status = "running"
                run.started_at = run.started_at or now
            elif int(status.get("failed") or 0) > 0:
                run.status = "failed"
                run.error_message = _job_failure_message(status)
                run.completed_at = now
            elif int(status.get("succeeded") or 0) > 0:
                if (
                    run.stage == "detection"
                    and _detection_phase(run) == DETECTION_SHARDS_PHASE
                ):
                    provenance = cast(dict[str, Any], run.provenance or {})
                    try:
                        plan = parse_detection_shard_plan_descriptor(
                            provenance.get("detection_shard_plan")
                        )
                        complete_detection_shard_receipts(
                            session,
                            run_id=cast(str, run.run_id),
                            plan=plan,
                        )
                    except ValueError as error:
                        run.status = "failed"
                        run.error_message = f"Detection shards exited without a complete durable receipt set: {error}"
                        run.completed_at = now
                        continue
                    shard_job_name = cast(str, run.job_name)
                    shard_dispatch_attempts = cast(int, run.dispatch_attempts)
                    inference_resource_class = cast(str, run.resource_class)
                    run.provenance = {
                        **provenance,
                        DETECTION_PHASE_KEY: DETECTION_FINALIZER_PHASE,
                        "detection_shard_job_name": shard_job_name,
                        "detection_shard_dispatch_attempts": shard_dispatch_attempts,
                        DETECTION_INFERENCE_RESOURCE_CLASS_KEY: (
                            inference_resource_class
                        ),
                    }
                    run.resource_class = "cpu-standard"
                    run.executor = None
                    run.job_name = None
                    run.scheduled_at = None
                    run.dispatch_attempts = 0
                    run.dispatch_error = None
                    run.status = "queued"
                    run.current_step = "DETECTION_FINALIZING"
                    continue
                run.status = "failed"
                run.error_message = (
                    "Stage Job exited without publishing its immutable artifact"
                )
                run.completed_at = now

        for run, _mission in rows:
            sync_analysis_stage(session, run)


def orchestrator_tick(
    client: KubernetesJobClient,
    settings: StageOrchestratorSettings,
) -> None:
    reconcile_stage_jobs(client, settings)
    with get_session() as session:
        reserved = reserve_ready_jobs(session, settings, datetime.now(UTC))
        active_runs = (
            session.query(MissionStageRun)
            .filter(
                MissionStageRun.status.in_(ACTIVE_STATUSES),
            )
            .all()
        )
        queue_counts: dict[tuple[str, str], tuple[int, int]] = {}
        oldest_queued_age = 0.0
        observed_at = datetime.now(UTC)
        for run in active_runs:
            key = (str(run.status), str(run.resource_class))
            runs, units = queue_counts.get(key, (0, 0))
            resource_units = (
                _active_resource_units(run)
                if run.status == "running"
                else _requested_resource_units(run, settings)
            )
            queue_counts[key] = (runs + 1, units + resource_units)
            if run.status == "queued":
                created_at = run.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                oldest_queued_age = max(
                    oldest_queued_age,
                    (observed_at - created_at).total_seconds(),
                )
        observe_stage_queue(
            queue_counts,
            oldest_queued_age_seconds=oldest_queued_age,
        )
    dispatch_reserved_jobs(client, reserved, settings.maximum_dispatch_attempts)


def run_stage_orchestrator(stop_event: threading.Event) -> None:
    settings = settings_from_environment()
    if not settings.enabled:
        return
    client = KubernetesJobClient(settings.namespace)
    while not stop_event.is_set():
        try:
            orchestrator_tick(client, settings)
            observe_control_loop("stage_orchestrator", succeeded=True)
        except Exception:
            observe_control_loop("stage_orchestrator", succeeded=False)
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
