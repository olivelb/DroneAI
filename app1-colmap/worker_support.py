import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer

from shared.config import DEFAULT_WORKSPACE_DIR
from shared import storage
from shared.database import (
    get_session,
    get_or_create_mission,
    Mission,
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
    vol_id = mission["vol_id"]
    # Local work directory on emptyDir (fast ext4) — no more /host prefix
    work_dir = os.path.join("/work", vol_id)
    # Input: S3 prefix for the dataset (downloaded at runtime)
    input_dataset = mission.get("input_dataset", "")
    return MissionContext(mission=mission, vol_id=vol_id, input_dir=input_dataset, work_dir=work_dir)


def decode_mission_message(message_value):
    return json.loads(message_value.decode("utf-8"))


def log_mission_start(mission_context):
    print(f"📦 Processing mission {mission_context.vol_id}")
    print(f"   Input: {mission_context.input_dir}")
    print(f"   Workspace: {mission_context.work_dir}")
    print(f"   Pipeline: {mission_context.mission.get('pipeline', 'modern')}")


def create_consumer(kafka_broker, topic_in):
    consumer = Consumer({
        "bootstrap.servers": kafka_broker,
        "group.id": "colmap-workers-v4",
        "auto.offset.reset": "latest",
        "max.poll.interval.ms": 86400000,
    })
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
    def report_progress(vol_id, step, progress, status="processing", log=None, details=None):
        msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": service_name}
        if log:
            msg["log"] = log
            print(f"[{step}] {log}")
        if details is not None:
            msg["details"] = details
        producer.produce(topic_status, key=vol_id, value=json.dumps(msg))
        producer.flush()

    return report_progress


def publish_next_stage_message(producer, topic_out, vol_id, ortho_s3_key, mission_params, normalize_ai_backend_fn):
    message = {
        "vol_id": vol_id,
        "ortho_s3_key": ortho_s3_key,
        "classes": mission_params.get("classes", ["car"]),
        "ai_confidence": mission_params.get("ai_confidence", 0.3),
        "ai_backend": normalize_ai_backend_fn(mission_params.get("ai_backend", "yolo")),
        "ai_model_variant": mission_params.get("ai_model_variant", "yolo26l"),
        "sam_prompt": mission_params.get("sam_prompt", "car"),
    }
    producer.produce(topic_out, key=vol_id, value=json.dumps(message))
    producer.flush()


def control_consumer_loop(kafka_broker, topic_control, should_cancel_fn, on_cancel_fn, logger):
    control_consumer = Consumer({
        "bootstrap.servers": kafka_broker,
        "group.id": "colmap-control-workers",
        "auto.offset.reset": "latest",
    })
    control_consumer.subscribe([topic_control])

    while True:
        msg = control_consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            continue
        try:
            data = json.loads(msg.value().decode("utf-8"))
            if data.get("command") == "cancel" and should_cancel_fn(data.get("vol_id")):
                on_cancel_fn(data.get("vol_id"))
        except Exception as error:
            logger.warning("Failed to parse control message: %s", error)