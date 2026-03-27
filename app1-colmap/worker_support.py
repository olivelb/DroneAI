import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer

from shared.config import DEFAULT_WORKSPACE_DIR


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
    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}

    @staticmethod
    def _now_iso():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _state_path(work_dir):
        return os.path.join(work_dir, "mission_state.json")

    @staticmethod
    def _history_path(work_dir):
        return os.path.join(work_dir, "mission_state_history.jsonl")

    def load_state(self, work_dir):
        state_path = self._state_path(work_dir)
        if not os.path.exists(state_path):
            return None
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def start_mission(self, mission_context):
        os.makedirs(mission_context.work_dir, exist_ok=True)
        previous_state = self.load_state(mission_context.work_dir)
        state = {
            "version": 1,
            "vol_id": mission_context.vol_id,
            "workspace_dir": mission_context.work_dir,
            "mission": mission_context.mission,
            "started_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "status": "processing",
            "step": "STARTING",
            "progress": 0,
            "last_log": None,
            "current_command": None,
            "copy_progress": None,
            "resume_info": {
                "mode": "best-effort",
                "note": "Resume is exact for file-copy checkpoints and stage-level for COLMAP subprocesses. Mid-command resume depends on COLMAP artifact support.",
            },
        }
        if previous_state:
            state["resume_info"]["resumed_from"] = {
                "status": previous_state.get("status"),
                "step": previous_state.get("step"),
                "progress": previous_state.get("progress"),
                "updated_at": previous_state.get("updated_at"),
                "last_log": previous_state.get("last_log"),
            }
        with self._lock:
            self._active[mission_context.vol_id] = {
                "work_dir": mission_context.work_dir,
                "state": state,
            }
        self._write_state(mission_context.work_dir, state)
        self._append_history(
            mission_context.work_dir,
            {
                "timestamp": self._now_iso(),
                "event": "mission_started",
                "state": state,
                "previous_state": previous_state,
            },
        )
        return previous_state

    def clear_mission(self, vol_id):
        with self._lock:
            self._active.pop(vol_id, None)

    def record_progress(self, vol_id, step, progress, status="processing", log=None, details=None):
        with self._lock:
            entry = self._active.get(vol_id)
            if not entry:
                return
            work_dir = entry["work_dir"]
            state = dict(entry["state"])

            state["updated_at"] = self._now_iso()
            state["status"] = status
            state["step"] = step
            state["progress"] = progress
            if log is not None:
                state["last_log"] = log

            event_kind = (details or {}).get("event")
            if event_kind == "command_started":
                state["current_command"] = {
                    "step": step,
                    "command": details.get("command"),
                    "started_at": state["updated_at"],
                    "resume_note": "If interrupted here, resume restarts this COLMAP command from the beginning unless the command itself produced reusable artifacts.",
                }
            elif event_kind in {"command_finished", "command_failed", "command_cancelled"}:
                state["last_command"] = {
                    "step": step,
                    "command": details.get("command"),
                    "finished_at": state["updated_at"],
                    "event": event_kind,
                }
                state["current_command"] = None
            elif event_kind == "copy_progress":
                state["copy_progress"] = {
                    "processed": details.get("processed"),
                    "total": details.get("total"),
                    "copied": details.get("copied"),
                    "skipped": details.get("skipped"),
                    "updated_at": state["updated_at"],
                    "resume_note": "Resume continues copying by comparing the clean_images files already present in the mission workspace.",
                }

            if status in {"success", "error"}:
                state["current_command"] = None

            entry["state"] = state

        self._write_state(work_dir, state)
        self._append_history(
            work_dir,
            {
                "timestamp": state["updated_at"],
                "event": "progress",
                "vol_id": vol_id,
                "step": step,
                "progress": progress,
                "status": status,
                "log": log,
                "details": details,
            },
        )

    def _write_state(self, work_dir, state):
        state_path = self._state_path(work_dir)
        temp_path = f"{state_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, state_path)

    def _append_history(self, work_dir, event):
        history_path = self._history_path(work_dir)
        with open(history_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True))
            handle.write("\n")


@dataclass(frozen=True)
class MissionContext:
    mission: dict
    vol_id: str
    input_dir: str
    work_dir: str


def make_host_path(path):
    if path.startswith("/host"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return "/host" + path


def build_mission_context(mission):
    vol_id = mission["vol_id"]
    workspace_base = mission.get("workspace_dir", DEFAULT_WORKSPACE_DIR)
    work_dir = make_host_path(resolve_workspace_dir(workspace_base, vol_id))
    input_dir = make_host_path(mission["input_dir"])
    return MissionContext(mission=mission, vol_id=vol_id, input_dir=input_dir, work_dir=work_dir)


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


def publish_next_stage_message(producer, topic_out, vol_id, ortho_file, mission_params, normalize_ai_backend_fn):
    message = {
        "vol_id": vol_id,
        "ortho_path": ortho_file,
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