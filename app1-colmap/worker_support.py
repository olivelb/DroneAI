import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer

from shared.config import DEFAULT_WORKSPACE_DIR
from shared import storage
from shared.event_contracts import deterministic_event_id, make_event
from shared.kafka_reliability import publish_json, reliable_consumer_config
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
    def __init__(self):
        self._lock = threading.Lock()
        self._current_mission_id = None
        self._cancel_requested = False

    def start_mission(self, vol_id):
        with self._lock:
            self._current_mission_id = vol_id
            self._cancel_requested = False

    def clear(self):
        with self._lock:
            self._current_mission_id = None
            self._cancel_requested = False

    def should_cancel(self, vol_id):
        with self._lock:
            return self._current_mission_id == vol_id

    def on_cancel(self, vol_id):
        with self._lock:
            self._cancel_requested = True
        print(f"⚠️ Cancel requested for {vol_id}")

    def is_cancel_requested(self):
        with self._lock:
            return self._cancel_requested

    def ensure_not_cancelled(self, process=None):
        with self._lock:
            if not self._cancel_requested:
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

    def load_state(self, vol_id):
        """Load mission state from database."""
        try:
            with get_session() as session:
                mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
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
        previous_state = self.load_state(mission_context.vol_id)

        try:
            with get_session() as session:
                mission = get_or_create_mission(
                    session,
                    mission_context.vol_id,
                    status="processing",
                    pipeline=mission_context.mission.get("pipeline", "modern"),
                    params=mission_context.mission,
                    workspace_prefix=f"missions/{mission_context.vol_id}",
                    input_dataset=mission_context.mission.get("input_dataset"),
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
            }
        return previous_state

    def clear_mission(self, vol_id):
        with self._lock:
            self._active.pop(vol_id, None)

    def record_progress(self, vol_id, step, progress, status="processing", log=None, details=None):
        with self._lock:
            entry = self._active.get(vol_id)
            if not entry:
                return

        try:
            with get_session() as session:
                mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
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
    # Pick work drive from mission params or env default
    work_drive = mission.get("colmap_params", {}).get("work_drive") or mission.get("work_drive")
    if not work_drive:
        work_drive = os.getenv("WORK_DRIVE_DEFAULT", "system")
    work_drive = validate_work_drive(work_drive, configured_names=configured_work_drive_names())
    work_base = safe_child_path("/work", work_drive, field_name="work_drive")
    if not work_base.is_dir():
        print(f"⚠️ Work drive '{work_drive}' not mounted at {work_base}, falling back to /work/system")
        work_base = safe_child_path("/work", "system", field_name="work_drive")
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
            offset_reset="latest",
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
        )

    return report_progress


def publish_next_stage_message(producer, topic_out, vol_id, ortho_s3_key, mission_params, normalize_ai_backend_fn):
    message = make_event(
        "orthomosaic",
        {
            "vol_id": vol_id,
            "ortho_s3_key": ortho_s3_key,
            "classes": mission_params.get("classes", ["car"]),
            "ai_confidence": mission_params.get("ai_confidence", 0.3),
            "ai_backend": normalize_ai_backend_fn(mission_params.get("ai_backend", "yolo")),
            "ai_model_variant": mission_params.get("ai_model_variant", "yolo26l"),
            "sam_prompt": mission_params.get("sam_prompt", "car"),
            "tile_size": mission_params.get("tile_size", 1024),
        },
        event_id=deterministic_event_id("orthomosaic", vol_id),
        correlation_id=mission_params.get("correlation_id") or vol_id,
        causation_id=mission_params.get("event_id"),
    )
    publish_json(producer, topic_out, message, key=vol_id)


def control_consumer_loop(
    kafka_broker,
    topic_control,
    should_cancel_fn,
    on_cancel_fn,
    logger,
    producer,
    dead_letter_topic,
):
    def handle_control(data):
        if data.get("command") == "cancel" and should_cancel_fn(
            data.get("vol_id")
        ):
            on_cancel_fn(data.get("vol_id"))

    run_control_consumer(
        kafka_broker=kafka_broker,
        topic=topic_control,
        consumer_group="colmap-control-workers",
        producer=producer,
        dead_letter_topic=dead_letter_topic,
        handler=handle_control,
        logger=logger,
    )
