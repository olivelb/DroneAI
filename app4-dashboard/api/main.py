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

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from .schemas import MissionParams
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


@app.delete("/mission/{vol_id}")
def delete_mission(vol_id: str):
    """Delete a mission: remove all S3 files and database records."""
    # Delete S3 objects under the mission prefix
    s3_prefix = f"missions/{vol_id}/"
    deleted_count = 0
    try:
        deleted_count = storage.delete_prefix(s3_prefix)
    except Exception as exc:
        print(f"S3 delete error for {vol_id}: {exc}")

    # Delete DB records (Mission cascades to logs + detections)
    db_deleted = False
    try:
        with get_session() as session:
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
            if mission:
                session.delete(mission)
                db_deleted = True
    except Exception as exc:
        return {"status": "error", "message": f"S3 cleaned ({deleted_count} objects) but DB delete failed: {exc}"}

    return {
        "status": "success",
        "message": f"Mission {vol_id} deleted.",
        "s3_objects_deleted": deleted_count,
        "db_deleted": db_deleted,
    }


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
    # Work drives config is passed via WORK_DRIVES env (JSON array from Helm)
    import json as _json
    drives_raw = os.environ.get("WORK_DRIVES", "")
    try:
        drives = _json.loads(drives_raw) if drives_raw else []
    except Exception:
        drives = []
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "metadata": PARAMETER_METADATA,
        "work_drives": drives,
        "work_drive_default": os.environ.get("WORK_DRIVE_DEFAULT", ""),
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


@app.get("/preview/{s3_key:path}")
def preview_image(s3_key: str, max_size: int = 4096, colormap: str = ""):
    """Return a PNG preview of a GeoTIFF (or any image) stored in S3.

    Resizes large images so the longest side ≤ *max_size* (capped at 8192).
    Use ``colormap=depth`` to apply a blue → red gradient (for height maps).
    """
    from fastapi.responses import StreamingResponse
    from PIL import Image
    import io as _io
    import struct as _struct
    import array as _array

    Image.MAX_IMAGE_PIXELS = 500_000_000  # allow large orthos
    max_size = min(max(256, max_size), 8192)

    if not storage.file_exists(s3_key):
        return {"error": "File not found"}

    try:
        stream, length, _ = storage.get_object_stream(s3_key)
        raw = stream.read()
        stream.close()

        img = Image.open(_io.BytesIO(raw))

        # --- Depth colormap (blue → red) for single-channel data ---
        if colormap == "depth" and img.mode in ("I;16", "I", "F", "L"):
            # Extract raw float/int pixel values
            if img.mode == "F":
                pixels = list(img.getdata())
            elif img.mode == "I":
                pixels = list(img.getdata())
            elif img.mode == "I;16":
                data = img.tobytes()
                pixels = list(_struct.unpack(f"<{len(data)//2}H", data))
            else:  # L
                pixels = list(img.getdata())

            lo = min(pixels)
            hi = max(pixels)
            rng = float(hi - lo) if hi != lo else 1.0

            # Blue(0) → Cyan → Green → Yellow → Red(1)
            def depth_color(t: float) -> tuple:
                if t < 0.25:
                    s = t / 0.25
                    return (0, int(s * 255), 255)              # blue → cyan
                elif t < 0.5:
                    s = (t - 0.25) / 0.25
                    return (0, 255, int((1 - s) * 255))        # cyan → green
                elif t < 0.75:
                    s = (t - 0.5) / 0.25
                    return (int(s * 255), 255, 0)              # green → yellow
                else:
                    s = (t - 0.75) / 0.25
                    return (255, int((1 - s) * 255), 0)        # yellow → red

            w, h = img.size
            rgb = _array.array("B", [0] * (w * h * 3))
            for i, p in enumerate(pixels):
                t = (p - lo) / rng
                r, g, b = depth_color(t)
                rgb[i * 3] = r
                rgb[i * 3 + 1] = g
                rgb[i * 3 + 2] = b
            img = Image.frombytes("RGB", (w, h), bytes(rgb))

        # --- Standard mode conversions ---
        elif img.mode in ("P", "CMYK"):
            img = img.convert("RGB")
        elif img.mode == "I;16":
            data = img.tobytes()
            pixels = _struct.unpack(f"<{len(data)//2}H", data)
            lo, hi = min(pixels), max(pixels)
            rng = hi - lo if hi != lo else 1
            norm = bytes(int((p - lo) / rng * 255) for p in pixels)
            img = Image.frombytes("L", img.size, norm).convert("RGB")
        elif img.mode in ("I", "F"):
            # 32-bit int/float → normalize to 8-bit grayscale
            pixels = list(img.getdata())
            lo, hi = min(pixels), max(pixels)
            rng = float(hi - lo) if hi != lo else 1.0
            norm = bytes(int((p - lo) / rng * 255) for p in pixels)
            img = Image.frombytes("L", img.size, norm).convert("RGB")
        elif img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")

        # Resize if needed
        w, h = img.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = _io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        return StreamingResponse(buf, media_type="image/png", headers={
            "Cache-Control": "public, max-age=3600",
        })
    except Exception as exc:
        return {"error": f"Preview generation failed: {exc}"}


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


@app.delete("/datasets/{name}")
def delete_dataset(name: str):
    """Delete a dataset and all its files from S3."""
    import re
    safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "", name.strip())
    if not safe_name or safe_name != name.strip():
        return {"status": "error", "message": "Invalid dataset name"}
    prefix = f"datasets/{safe_name}/"
    try:
        deleted = storage.delete_prefix(prefix)
        return {"status": "success", "message": f"Dataset '{safe_name}' deleted.", "objects_deleted": deleted}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/files/{s3_key:path}")
def get_file(s3_key: str):
    """Redirect to a presigned URL using the public S3 endpoint."""
    if not storage.file_exists(s3_key):
        return {"error": "File not found"}
    url = storage.get_presigned_url(s3_key)
    return RedirectResponse(url=url, status_code=302)


# ---------------------------------------------------------------------------
# Dataset upload – single-file endpoint for reliability with large datasets
# ---------------------------------------------------------------------------

import re as _re
from fastapi import Query


@app.post("/datasets/upload-file")
async def upload_single_file(
    dataset_name: str = Query(...),
    file: UploadFile = FastAPIFile(...),
):
    """Upload a single file to S3 under datasets/{dataset_name}/.

    The frontend calls this endpoint once per file, enabling per-file
    progress tracking and avoiding giant multipart requests that break
    with large drone datasets.
    """
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", dataset_name.strip())
    if not safe_name:
        return {"error": "Invalid dataset name"}, 400

    filename = Path(file.filename or "file").name  # prevent path traversal
    s3_key = f"datasets/{safe_name}/{filename}"
    try:
        # Stream directly to S3 using the file-like object (avoids loading
        # the entire file into memory).
        storage.put_object(s3_key, file.file)
        return {"name": filename, "s3_key": s3_key, "status": "ok"}
    except Exception as exc:
        print(f"[upload] ERROR uploading {filename} to {s3_key}: {exc}")
        return {"name": filename, "status": "error", "error": str(exc)}


@app.post("/datasets/upload")
async def upload_dataset_batch(
    dataset_name: str = Query(...),
    files: list[UploadFile] = FastAPIFile(...),
):
    """Batch upload (kept for backward compatibility but prefer /upload-file)."""
    safe_name = _re.sub(r"[^a-zA-Z0-9_\-]", "_", dataset_name.strip())
    if not safe_name:
        return {"error": "Invalid dataset name"}

    upload_id = uuid.uuid4().hex[:12]
    total = len(files)
    result = {
        "upload_id": upload_id,
        "dataset": safe_name,
        "total": total,
        "completed": 0,
        "failed": 0,
        "status": "uploading",
        "files": [],
    }

    for i, f in enumerate(files):
        filename = Path(f.filename or f"file_{i}").name
        s3_key = f"datasets/{safe_name}/{filename}"
        try:
            storage.put_object(s3_key, f.file)
            result["completed"] += 1
            result["files"].append({"name": filename, "s3_key": s3_key, "status": "ok"})
        except Exception as exc:
            print(f"[upload] ERROR uploading {filename}: {exc}")
            result["failed"] += 1
            result["files"].append({"name": filename, "status": "error", "error": str(exc)})

    result["status"] = "done" if result["failed"] == 0 else "partial"
    return result


@app.post("/mission")
async def start_mission(params: MissionParams):
    mission_payload = params.model_dump()
    try:
        with get_session() as session:
            get_or_create_mission(
                session,
                params.vol_id,
                status="pending",
                pipeline=params.pipeline,
                input_dataset=params.input_dataset,
                workspace_prefix=f"missions/{params.vol_id}",
                params=mission_payload,
            )
    except Exception as exc:
        print(f"Failed to create mission in DB: {exc}")

    producer.produce(TOPIC_MISSION, key=params.vol_id, value=json.dumps(mission_payload))
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
