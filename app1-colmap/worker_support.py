import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer

from shared.cancellation import AttemptCancellationRegistry
from shared.config import DEFAULT_WORKSPACE_DIR
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
from shared.kafka_partitioning import tenant_mission_key
from shared.kafka_reliability import publish_json, reliable_consumer_config
from shared.tenancy import LEGACY_ORGANIZATION_ID, mission_event_namespace
from shared.worker_messaging import (
    make_progress_publisher,
    run_control_consumer,
)
from shared.database import (
    get_session,
    get_or_create_mission,
    Mission,
)
from shared.validation import (
    configured_work_drive_names,
    safe_child_path,
    validate_dataset_prefix,
    validate_mission_id,
    validate_work_drive,
)


class WorkerCancellationState:
    def __init__(self, registry=None):
        self._lock = threading.Lock()
        self._registry = registry or AttemptCancellationRegistry()
        self._current_mission_id = None
        self._current_organization_id = None
        self._current_attempt = 0
        self._cancel_requested = False

    def start_mission(self, vol_id, attempt=0, organization_id=None):
        with self._lock:
            self._current_mission_id = vol_id
            self._current_organization_id = organization_id
            self._current_attempt = int(attempt)
            self._cancel_requested = self._registry.is_cancelled(
                vol_id,
                None,
                attempt,
                organization_id=organization_id,
            )

    def clear(self):
        with self._lock:
            vol_id = self._current_mission_id
            attempt = self._current_attempt
            organization_id = self._current_organization_id
            self._current_mission_id = None
            self._current_organization_id = None
            self._current_attempt = 0
            self._cancel_requested = False
        if vol_id is not None:
            self._registry.clear(
                vol_id,
                None,
                attempt,
                organization_id=organization_id,
            )

    def should_cancel(self, vol_id):
        with self._lock:
            return self._current_mission_id == vol_id

    def on_cancel(self, vol_id):
        with self._lock:
            attempt = self._current_attempt
            organization_id = self._current_organization_id
        self.cancel(
            vol_id,
            None,
            attempt,
            organization_id=organization_id,
        )

    def cancel(self, vol_id, run_id=None, attempt=0, *, organization_id=None):
        self._registry.cancel(
            vol_id,
            run_id,
            attempt,
            organization_id=organization_id,
        )
        with self._lock:
            if (
                run_id is None
                and self._current_mission_id == vol_id
                and self._current_attempt == int(attempt)
                and self._current_organization_id == organization_id
            ):
                self._cancel_requested = True
        print(f"⚠️ Cancel requested for {vol_id}")

    def is_cancel_requested(self):
        with self._lock:
            if self._cancel_requested:
                return True
            vol_id = self._current_mission_id
            attempt = self._current_attempt
            organization_id = self._current_organization_id
        if vol_id is None:
            return False
        cancelled = self._registry.is_cancelled(
            vol_id,
            None,
            attempt,
            organization_id=organization_id,
        )
        if cancelled:
            with self._lock:
                if (
                    self._current_mission_id == vol_id
                    and self._current_attempt == attempt
                ):
                    self._cancel_requested = True
        return cancelled

    def ensure_not_cancelled(self, process=None):
        if not self.is_cancel_requested():
            return
        if process is not None:
            process.kill()
        raise RuntimeError("Mission cancelled by user")


class MissionStateTracker:
    """Track mission state in PostGIS database (replaces mission_state.json)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}

    @staticmethod
    def _now_iso():
        return datetime.now(timezone.utc).isoformat()

    def load_state(self, vol_id, organization_id=LEGACY_ORGANIZATION_ID):
        """Load mission state from database."""
        try:
            with get_session() as session:
                mission = (
                    session.query(Mission)
                    .filter(
                        Mission.vol_id == vol_id,
                        Mission.organization_id == organization_id,
                    )
                    .first()
                )
                if not mission:
                    return None
                return {
                    "version": 2,
                    "vol_id": mission.vol_id,
                    "status": mission.status,
                    "step": mission.current_step,
                    "progress": mission.progress,
                    "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
                    "last_log": mission.error_message,
                    "mission": mission.params,
                    "resume_info": mission.resume_info,
                }
        except Exception:
            return None

    def start_mission(self, mission_context):
        namespace = mission_event_namespace(
            {**mission_context.mission, "vol_id": mission_context.vol_id}
        )
        previous_state = self.load_state(
            mission_context.vol_id,
            namespace.organization_id,
        )

        try:
            with get_session() as session:
                mission = get_or_create_mission(
                    session,
                    mission_context.vol_id,
                    status="processing",
                    pipeline=mission_context.mission.get("pipeline", "modern"),
                    owner_subject=mission_context.mission.get(
                        "owner_subject",
                        "legacy-unassigned",
                    ),
                    organization_id=mission_context.mission.get(
                        "organization_id",
                        "legacy-unassigned",
                    ),
                    params=mission_context.mission,
                    workspace_prefix=namespace.root,
                    input_dataset=mission_context.mission.get("input_dataset"),
                )
                durable_namespace = mission_event_namespace(
                    {
                        "vol_id": mission.vol_id,
                        "organization_id": mission.organization_id,
                        "workspace_prefix": mission.workspace_prefix,
                    }
                )
                if durable_namespace != namespace:
                    raise RuntimeError(
                        "Mission event namespace does not match durable state"
                    )
                mission.status = "processing"
                mission.current_step = "STARTING"
                mission.progress = 0
                mission.updated_at = datetime.now(timezone.utc)

                resume_info = dict(mission.resume_info or {})
                resume_info["mode"] = "best-effort"
                if previous_state:
                    resume_info["resumed_from"] = {
                        "status": previous_state.get("status"),
                        "step": previous_state.get("step"),
                        "progress": previous_state.get("progress"),
                        "updated_at": previous_state.get("updated_at"),
                        "last_log": previous_state.get("last_log"),
                    }
                mission.resume_info = resume_info
        except Exception as exc:
            print(f"Failed to persist mission start to DB: {exc}")

        with self._lock:
            self._active[mission_context.vol_id] = {
                "work_dir": mission_context.work_dir,
                "organization_id": namespace.organization_id,
            }
        return previous_state

    def active_organization_id(self, vol_id):
        with self._lock:
            entry = self._active.get(vol_id)
            if not entry:
                return "legacy-unassigned"
            return entry["organization_id"]

    def clear_mission(self, vol_id):
        with self._lock:
            self._active.pop(vol_id, None)

    def record_progress(self, vol_id, step, progress, status="processing", log=None, details=None):
        with self._lock:
            entry = self._active.get(vol_id)
            if not entry:
                return
            organization_id = entry["organization_id"]

        try:
            with get_session() as session:
                mission = (
                    session.query(Mission)
                    .filter(
                        Mission.vol_id == vol_id,
                        Mission.organization_id == organization_id,
                    )
                    .first()
                )
                if not mission:
                    return

                mission.current_step = step
                mission.progress = progress
                mission.status = status
                mission.updated_at = datetime.now(timezone.utc)
                if log and status == "error":
                    mission.error_message = log

                if details:
                    event_kind = details.get("event")
                    resume_info = dict(mission.resume_info or {})
                    if event_kind in ("command_started", "command_finished", "command_failed", "command_cancelled"):
                        resume_info["last_command_event"] = {
                            "step": step,
                            "command": details.get("command"),
                            "event": event_kind,
                            "timestamp": self._now_iso(),
                        }
                    elif event_kind == "copy_progress":
                        resume_info["copy_progress"] = details
                    mission.resume_info = resume_info
        except Exception as exc:
            print(f"Failed to persist progress to DB for {vol_id}: {exc}")


@dataclass(frozen=True)
class MissionContext:
    mission: dict
    vol_id: str
    input_dir: str
    work_dir: str


def build_mission_context(mission):
    vol_id = validate_mission_id(mission["vol_id"])
    mission_event_namespace(mission)
    # Pick work drive from mission params or env default
    work_drive = mission.get("colmap_params", {}).get("work_drive") or mission.get("work_drive")
    if not work_drive:
        work_drive = os.getenv("WORK_DRIVE_DEFAULT", "system")
    work_drive = validate_work_drive(work_drive, configured_names=configured_work_drive_names())
    work_base = safe_child_path("/work", work_drive, field_name="work_drive")
    if not work_base.is_dir():
        raise RuntimeError(
            f"Configured work drive '{work_drive}' is not mounted at {work_base}. "
            "Redeploy after restoring the disk or select another advertised drive."
        )
    work_dir = safe_child_path(work_base, vol_id, field_name="vol_id")
    # Input: S3 prefix for the dataset (downloaded at runtime)
    input_dataset = validate_dataset_prefix(mission.get("input_dataset", ""))
    return MissionContext(
        mission=mission,
        vol_id=vol_id,
        input_dir=input_dataset,
        work_dir=str(work_dir),
    )


def decode_mission_message(message_value):
    return json.loads(message_value.decode("utf-8"))


def log_mission_start(mission_context):
    print(f"📦 Processing mission {mission_context.vol_id}")
    print(f"   Input: {mission_context.input_dir}")
    print(f"   Workspace: {mission_context.work_dir}")
    print(f"   Pipeline: {mission_context.mission.get('pipeline', 'modern')}")


def create_consumer(kafka_broker, topic_in):
    consumer = Consumer(
        reliable_consumer_config(
            kafka_broker,
            "colmap-workers-v4",
            offset_reset="earliest",
            **{"max.poll.interval.ms": 86400000},
        )
    )
    consumer.subscribe([topic_in])
    return consumer


def create_producer(kafka_broker):
    return Producer({"bootstrap.servers": kafka_broker})


def resolve_workspace_dir(workspace_value, vol_id):
    workspace_root = os.path.normpath(workspace_value or DEFAULT_WORKSPACE_DIR)
    if os.path.basename(workspace_root) == vol_id:
        return workspace_root
    return os.path.join(workspace_root, vol_id)


def make_progress_reporter(producer, topic_status, service_name="COLMAP"):
    publish = make_progress_publisher(
        producer,
        topic_status,
        service_name=service_name,
    )

    def report_progress(
        vol_id,
        step,
        progress,
        status="processing",
        log=None,
        details=None,
        organization_id="legacy-unassigned",
    ):
        if log:
            print(f"[{step}] {log}")
        publish(
            vol_id,
            step,
            progress,
            status=status,
            log=log,
            details=details,
            organization_id=organization_id,
        )

    return report_progress


def publish_next_stage_message(producer, topic_out, vol_id, ortho_s3_key, mission_params, normalize_ai_backend_fn):
    attempt = int(mission_params.get("attempt", 0))
    organization_id = mission_params.get(
        "organization_id",
        "legacy-unassigned",
    )
    message = make_event(
        "orthomosaic",
        {
            "vol_id": vol_id,
            "organization_id": organization_id,
            "workspace_prefix": mission_params.get("workspace_prefix"),
            "ortho_s3_key": ortho_s3_key,
            "classes": mission_params.get("classes", ["car"]),
            "ai_confidence": mission_params.get("ai_confidence", 0.3),
            "ai_backend": normalize_ai_backend_fn(mission_params.get("ai_backend", "yolo")),
            "ai_model_variant": mission_params.get("ai_model_variant", "yolo26l"),
            "sam_prompt": mission_params.get("sam_prompt", "car"),
            "tile_size": mission_params.get("tile_size", 1024),
        },
        event_id=deterministic_tenant_event_id(
            "orthomosaic", organization_id, vol_id, attempt
        ),
        correlation_id=tenant_correlation_id(organization_id, vol_id),
        causation_id=mission_params.get("event_id"),
        attempt=attempt,
    )
    publish_json(
        producer,
        topic_out,
        message,
        key=tenant_mission_key(organization_id, vol_id),
    )


def control_consumer_loop(
    kafka_broker,
    topic_control,
    cancel_fn,
    logger,
    producer,
    dead_letter_topic,
):
    def handle_control(data):
        if data.get("command") == "cancel" and data.get("vol_id"):
            cancel_fn(
                data["vol_id"],
                data.get("analysis_run_id"),
                int(data.get("attempt", 0)),
                organization_id=data.get("organization_id"),
            )

    run_control_consumer(
        kafka_broker=kafka_broker,
        topic=topic_control,
        consumer_group="colmap-control-workers",
        producer=producer,
        dead_letter_topic=dead_letter_topic,
        handler=handle_control,
        logger=logger,
    )
