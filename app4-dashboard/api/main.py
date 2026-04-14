import os
import json
import asyncio
import sys
import threading
import time
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from collections import deque
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File as FastAPIFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from confluent_kafka import Producer, Consumer
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, SERVICE_ORDER, TOPIC_CONTROL, TOPIC_MISSION, TOPIC_STATUS
from shared.pipeline_params import (
    PARAMETER_METADATA,
    PIPELINE_DEFAULTS,
)
from shared import storage
from shared.database import (
    get_session,
    get_or_create_mission,
    Mission,
    MissionLog,
)

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
status_history = deque(maxlen=300)

TERMINAL_STATUSES = {"success", "error"}
MISSION_PROCESSING_STALE_SECONDS = float(os.getenv("MISSION_PROCESSING_STALE_SECONDS", "120"))


# ---------------------------------------------------------------------------
# Mission state helpers (DB-backed, replaces in-memory dicts)
# ---------------------------------------------------------------------------


def update_mission_state(payload: dict):
    """Persist a Kafka status message to the DB."""
    vol_id = payload.get("vol_id")
    if not vol_id:
        return

    service = payload.get("service") or "UNKNOWN"
    step = payload.get("step")
    progress = payload.get("progress", 0)
    status = payload.get("status", "processing")
    log_msg = payload.get("log")
    details = payload.get("details")

    try:
        with get_session() as session:
            mission = get_or_create_mission(session, vol_id)

            # Update per-service state
            states = dict(mission.service_states or {})
            states[service] = payload
            mission.service_states = states

            # Update mission-level progress
            mission.current_step = step
            mission.progress = progress
            if status in TERMINAL_STATUSES:
                mission.status = status
            elif mission.status not in TERMINAL_STATUSES:
                mission.status = "processing"
            mission.updated_at = datetime.now(timezone.utc)

            if status == "error" and log_msg:
                mission.error_message = log_msg

            # Store command/copy details in resume_info
            if details:
                event = details.get("event")
                resume_info = dict(mission.resume_info or {})
                if event in ("command_started", "command_finished", "command_failed", "command_cancelled"):
                    resume_info["last_command_event"] = details
                elif event == "copy_progress":
                    resume_info["copy_progress"] = details
                mission.resume_info = resume_info

            # Persist log entry
            mission_log = MissionLog(
                mission_id=mission.id,
                vol_id=vol_id,
                service=service,
                step=step,
                status=status,
                progress=progress,
                message=log_msg,
                details=details,
            )
            session.add(mission_log)
    except Exception as exc:
        print(f"Failed to persist status to DB for {vol_id}: {exc}")


def compute_overall_status(services: dict) -> str:
    if not services:
        return "idle"

    statuses = [
        (payload.get("status", "processing") if isinstance(payload, dict) else "processing")
        for payload in services.values()
    ]
    if any(status == "error" for status in statuses):
        return "error"
    if services and all(status == "success" for status in statuses if status):
        seen = set(services.keys())
        if all(service in seen for service in SERVICE_ORDER):
            return "success"
    return "processing"


def is_mission_stale(mission: Mission) -> bool:
    if mission.updated_at is None:
        return False
    elapsed = (datetime.now(timezone.utc) - mission.updated_at).total_seconds()
    return elapsed > MISSION_PROCESSING_STALE_SECONDS


def build_colmap_resume_state(mission: Mission) -> dict:
    services = mission.service_states or {}
    colmap_service = services.get("COLMAP", {})
    colmap_status = colmap_service.get("status") if isinstance(colmap_service, dict) else None
    stale = colmap_status == "processing" and is_mission_stale(mission)

    downstream = [
        name
        for name, svc in services.items()
        if name != "COLMAP" and isinstance(svc, dict) and svc.get("status") == "processing"
    ]

    if colmap_status == "processing" and not stale:
        return {
            "available": False,
            "state": "running",
            "reason": "COLMAP is currently running. Resume is only relevant after an interruption.",
            "downstream_processing": downstream,
        }
    if stale:
        has_params = mission.params is not None
        return {
            "available": has_params,
            "state": "checkpointed" if has_params else "stale",
            "reason": "The last COLMAP status update is stale. The mission can be resumed." if has_params else "COLMAP is stale and no saved params found.",
            "downstream_processing": downstream,
        }
    if colmap_status == "success":
        return {
            "available": False,
            "state": "completed",
            "reason": "COLMAP has already completed for this mission." + (" Downstream processing can continue." if downstream else ""),
            "downstream_processing": downstream,
        }
    if colmap_status == "error":
        has_params = mission.params is not None
        return {
            "available": has_params,
            "state": "resumable" if has_params else "unavailable",
            "reason": "COLMAP stopped with an error. A resume action can restart from the last checkpoint." if has_params else "COLMAP errored but no saved mission parameters found.",
            "downstream_processing": downstream,
        }
    return {
        "available": False,
        "state": "unavailable",
        "reason": "No COLMAP state found yet.",
        "downstream_processing": downstream,
    }


def serialize_mission(mission: Mission) -> dict:
    services = mission.service_states or {}
    overall = compute_overall_status(services)
    if mission.status in TERMINAL_STATUSES:
        overall = mission.status

    # If processing and stale, mark as error
    if overall == "processing" and is_mission_stale(mission):
        overall = "error"

    colmap_resume = build_colmap_resume_state(mission)

    # Build workspace_state-compatible dict for frontend backward compatibility
    workspace_state = {
        "vol_id": mission.vol_id,
        "status": mission.status,
        "step": mission.current_step,
        "progress": mission.progress,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
        "started_at": mission.created_at.isoformat() if mission.created_at else None,
        "last_log": mission.error_message,
        "resume_info": mission.resume_info,
        "mission": mission.params,
        "current_command": (mission.resume_info or {}).get("last_command_event"),
        "copy_progress": (mission.resume_info or {}).get("copy_progress"),
    }

    return {
        "vol_id": mission.vol_id,
        "workspace_dir": f"missions/{mission.vol_id}",
        "workspace_state": workspace_state,
        "colmap_resume": colmap_resume,
        "services": services,
        "logs": [],
        "updated_at": mission.updated_at.timestamp() if mission.updated_at else time.time(),
        "overall_status": overall,
    }


def get_status_summary() -> dict:
    try:
        with get_session() as session:
            db_missions = (
                session.query(Mission)
                .order_by(Mission.updated_at.desc())
                .limit(50)
                .all()
            )
            serialized = [serialize_mission(m) for m in db_missions]
    except Exception as exc:
        print(f"Failed to query missions from DB: {exc}")
        serialized = []

    serialized.sort(key=lambda item: item["updated_at"], reverse=True)
    active = next((item for item in serialized if item["overall_status"] == "processing"), None)
    if active is None and serialized:
        active = serialized[0]

    return {
        "active_vol_id": active["vol_id"] if active else None,
        "missions": serialized,
    }


# ---------------------------------------------------------------------------
# Pod states (K8s API — unchanged except default namespace)
# ---------------------------------------------------------------------------


def fallback_pod_states() -> list[dict]:
    return [
        {"name": "kafka-broker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
        {"name": "colmap-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
        {"name": "ia-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
        {"name": "processing-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
        {"name": "dashboard-api", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
        {"name": "dashboard-frontend", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable", "last_terminated_reason": None, "last_terminated_exit_code": None, "oom_killed": False, "memory_limit": None, "memory_request": None},
    ]


def get_pod_states() -> dict:
    namespace = os.getenv("POD_NAMESPACE", "drone-ai")
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    api_host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    api_port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")

    if not os.path.exists(token_path) or not os.path.exists(ca_path):
        return {"available": False, "pods": fallback_pod_states(), "error": "service account credentials unavailable"}

    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()

        ssl_context = ssl.create_default_context(cafile=ca_path)
        url = f"https://{api_host}:{api_port}/api/v1/namespaces/{namespace}/pods"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, context=ssl_context, timeout=5) as response:
            payload = json.load(response)

        pods = []
        for item in payload.get("items", []):
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            container_specs = item.get("spec", {}).get("containers", [])
            ready_count = sum(1 for entry in container_statuses if entry.get("ready"))
            total_count = len(container_statuses)
            restarts = sum(entry.get("restartCount", 0) for entry in container_statuses)
            waiting_reason = None
            last_terminated_reason = None
            last_terminated_exit_code = None
            oom_killed = False
            for entry in container_statuses:
                state = entry.get("state", {})
                if "waiting" in state:
                    waiting_reason = state["waiting"].get("reason")
                last_state = entry.get("lastState", {})
                terminated = last_state.get("terminated") or state.get("terminated")
                if terminated and last_terminated_reason is None:
                    last_terminated_reason = terminated.get("reason")
                    last_terminated_exit_code = terminated.get("exitCode")
                    oom_killed = terminated.get("reason") == "OOMKilled" or terminated.get("exitCode") == 137
            memory_limit = None
            memory_request = None
            if container_specs:
                resources_spec = container_specs[0].get("resources", {})
                limits = resources_spec.get("limits", {})
                requests = resources_spec.get("requests", {})
                memory_limit = limits.get("memory")
                memory_request = requests.get("memory")
            pods.append({
                "name": item.get("metadata", {}).get("name", "unknown"),
                "phase": status.get("phase", "unknown").lower(),
                "ready": f"{ready_count}/{total_count}" if total_count else None,
                "restarts": restarts,
                "reason": waiting_reason or status.get("reason"),
                "last_terminated_reason": last_terminated_reason,
                "last_terminated_exit_code": last_terminated_exit_code,
                "oom_killed": oom_killed,
                "memory_limit": memory_limit,
                "memory_request": memory_request,
            })

        pods.sort(key=lambda pod: pod["name"])
        return {"available": True, "pods": pods, "error": None}
    except urllib.error.HTTPError as exc:
        return {"available": False, "pods": fallback_pod_states(), "error": f"kubernetes API HTTP {exc.code}"}
    except Exception as exc:
        return {"available": False, "pods": fallback_pod_states(), "error": str(exc)}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MissionParams(BaseModel):
    vol_id: str
    input_dataset: str  # S3 prefix, e.g. "datasets/banyuls_beach"
    epsg: str = "EPSG:4326"
    camera_model: str = "PINHOLE"
    pipeline: str = "modern"
    tile_size: int = 1024
    ai_confidence: float = 0.5
    ai_backend: str = "yolo"
    ai_model_variant: str = "yolo26l"
    sam_prompt: str = "car"
    classes: list[str] = Field(default_factory=lambda: ["car"])
    colmap_params: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        for message in status_history:
            await websocket.send_text(message)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass

    async def broadcast(self, message: str):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            try:
                self.active_connections.remove(d)
            except ValueError:
                pass

manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Kafka consumer thread
# ---------------------------------------------------------------------------


def kafka_consumer_thread_func(loop):
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'dashboard-api',
        'auto.offset.reset': 'latest'
    })
    consumer.subscribe([TOPIC_STATUS])

    while True:
        try:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"Kafka status consumer error: {msg.error()}")
                continue

            payload = msg.value().decode('utf-8')
            status_history.append(payload)
            try:
                update_mission_state(json.loads(payload))
            except json.JSONDecodeError:
                pass
            print(f"STATUS {payload}")
            future = asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
            future.result(timeout=5)
        except Exception as exc:
            print(f"Kafka status consumer loop error: {exc}")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    loop = asyncio.get_running_loop()
    threading.Thread(target=kafka_consumer_thread_func, args=(loop,), daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Drone Pipeline API is alive"}


@app.get("/status/summary")
def status_summary():
    return get_status_summary()


@app.get("/mission/state")
def mission_state(vol_id: str):
    try:
        with get_session() as session:
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
            if not mission:
                return {"vol_id": vol_id, "workspace_state": None}
            return {
                "vol_id": vol_id,
                "workspace_state": serialize_mission(mission).get("workspace_state"),
            }
    except Exception as exc:
        return {"vol_id": vol_id, "workspace_state": None, "error": str(exc)}


@app.post("/mission/cancel")
async def cancel_mission(vol_id: str):
    msg = {"vol_id": vol_id, "command": "cancel"}
    producer.produce(TOPIC_CONTROL, key=vol_id, value=json.dumps(msg))
    producer.flush()
    return {"status": "success", "message": f"Cancel command sent for {vol_id}"}


@app.post("/mission/resume")
async def resume_mission(vol_id: str):
    try:
        with get_session() as session:
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
            if not mission:
                return {"status": "error", "message": f"Mission {vol_id} not found."}

            colmap_resume = build_colmap_resume_state(mission)
            if not colmap_resume["available"]:
                return {
                    "status": "error",
                    "message": colmap_resume["reason"],
                    "colmap_resume": colmap_resume,
                }

            if not mission.params:
                return {
                    "status": "error",
                    "message": f"Saved state for {vol_id} does not contain the original mission payload.",
                    "colmap_resume": colmap_resume,
                }

            # Resend original mission params to Kafka
            mission_payload = dict(mission.params)
            mission_payload["vol_id"] = vol_id

            # Reset mission status
            mission.status = "processing"
            mission.current_step = "RESUMING"
            mission.error_message = None
            mission.updated_at = datetime.now(timezone.utc)

    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    producer.produce(TOPIC_MISSION, key=vol_id, value=json.dumps(mission_payload))
    producer.flush()
    return {
        "status": "success",
        "message": f"Resume command sent for {vol_id}.",
        "colmap_resume": colmap_resume,
    }


@app.get("/pods")
def pod_statuses():
    return get_pod_states()


@app.get("/mission/parameters")
def mission_parameters():
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "metadata": PARAMETER_METADATA,
    }


@app.get("/browse")
def browse_path(prefix: str = "datasets/"):
    """List S3 objects and prefixes under the given prefix."""
    try:
        items = []
        all_keys = storage.list_objects(prefix, delimiter="/")
        for key in all_keys:
            if key.endswith("/") and key != prefix:
                name = key.rstrip("/").split("/")[-1]
                children = storage.list_objects(key)
                img_count = sum(
                    1 for k in children
                    if k.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
                )
                items.append({
                    "name": name,
                    "path": key.rstrip("/"),
                    "is_dir": True,
                    "image_count": img_count,
                })
            elif not key.endswith("/"):
                name = key.split("/")[-1]
                items.append({
                    "name": name,
                    "path": key,
                    "is_dir": False,
                    "image_count": 0,
                })
        return sorted(items, key=lambda x: (not x["is_dir"], x["name"]))
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/datasets")
def list_datasets():
    """List top-level dataset folders in S3."""
    try:
        prefixes = storage.list_objects("datasets/", delimiter="/")
        results = []
        for p in prefixes:
            if not p.endswith("/"):
                continue
            name = p.rstrip("/").split("/")[-1]
            children = storage.list_objects(p)
            img_count = sum(
                1 for k in children
                if k.lower().endswith(('.jpg', '.jpeg', '.png'))
            )
            if img_count > 0:
                results.append({"name": name, "path": p.rstrip("/"), "image_count": img_count})
        return results
    except Exception as exc:
        return []


@app.get("/files/{s3_key:path}")
def get_file(s3_key: str):
    """Redirect to a presigned URL using the public S3 endpoint."""
    if not storage.file_exists(s3_key):
        return {"error": "File not found"}
    url = storage.get_presigned_url(s3_key)
    return RedirectResponse(url=url, status_code=302)


# ---------------------------------------------------------------------------
# Dataset upload
# ---------------------------------------------------------------------------

# In-memory upload progress tracking (keyed by upload_id)
_upload_progress: dict[str, dict] = {}
_upload_progress_subscribers: dict[str, list[WebSocket]] = {}


@app.post("/datasets/upload")
async def upload_dataset(
    dataset_name: str,
    files: list[UploadFile] = FastAPIFile(...),
):
    """Upload one or more image files to S3 under datasets/{dataset_name}/.

    Returns an upload_id that can be used to track progress via the
    ``/ws/upload/{upload_id}`` WebSocket endpoint.
    """
    import re

    # Sanitise dataset name to prevent path traversal
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", dataset_name.strip())
    if not safe_name:
        return {"error": "Invalid dataset name"}

    upload_id = uuid.uuid4().hex[:12]
    total = len(files)
    _upload_progress[upload_id] = {
        "upload_id": upload_id,
        "dataset": safe_name,
        "total": total,
        "completed": 0,
        "failed": 0,
        "status": "uploading",
        "files": [],
    }

    async def _notify(prog: dict):
        subs = _upload_progress_subscribers.get(upload_id, [])
        dead = []
        for ws in subs:
            try:
                await ws.send_text(json.dumps(prog))
            except Exception:
                dead.append(ws)
        for d in dead:
            try:
                subs.remove(d)
            except ValueError:
                pass

    failed_files = []
    for i, f in enumerate(files):
        filename = Path(f.filename or f"file_{i}").name  # prevent path traversal
        s3_key = f"datasets/{safe_name}/{filename}"
        try:
            contents = await f.read()
            storage.put_object(s3_key, contents)
            _upload_progress[upload_id]["completed"] += 1
            _upload_progress[upload_id]["files"].append({"name": filename, "s3_key": s3_key, "status": "ok"})
        except Exception as exc:
            _upload_progress[upload_id]["failed"] += 1
            _upload_progress[upload_id]["files"].append({"name": filename, "status": "error", "error": str(exc)})
            failed_files.append(filename)
        await _notify(_upload_progress[upload_id])

    _upload_progress[upload_id]["status"] = "done" if not failed_files else "partial"
    await _notify(_upload_progress[upload_id])

    return _upload_progress[upload_id]


@app.get("/datasets/upload/{upload_id}")
def upload_status(upload_id: str):
    """Poll upload progress (alternative to WebSocket)."""
    prog = _upload_progress.get(upload_id)
    if prog is None:
        return {"error": "Upload not found"}
    return prog


@app.websocket("/ws/upload/{upload_id}")
async def upload_progress_ws(websocket: WebSocket, upload_id: str):
    """WebSocket endpoint to receive real-time upload progress."""
    await websocket.accept()
    subs = _upload_progress_subscribers.setdefault(upload_id, [])
    subs.append(websocket)
    try:
        # Send current state immediately
        prog = _upload_progress.get(upload_id)
        if prog:
            await websocket.send_text(json.dumps(prog))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            subs.remove(websocket)
        except ValueError:
            pass


@app.post("/mission")
async def start_mission(params: MissionParams):
    try:
        with get_session() as session:
            mission = get_or_create_mission(
                session,
                params.vol_id,
                status="pending",
                pipeline=params.pipeline,
                input_dataset=params.input_dataset,
                workspace_prefix=f"missions/{params.vol_id}",
                params=params.dict(),
            )
    except Exception as exc:
        print(f"Failed to create mission in DB: {exc}")

    msg = params.dict()
    producer.produce(TOPIC_MISSION, key=params.vol_id, value=json.dumps(msg))
    producer.flush()
    return {"status": "success", "vol_id": params.vol_id}


@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
