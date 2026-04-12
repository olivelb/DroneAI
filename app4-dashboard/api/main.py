import os
import json
import asyncio
import sys
import threading
import time
import ssl
import math
from datetime import datetime
import urllib.error
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from confluent_kafka import Producer, Consumer
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import DEFAULT_WORKSPACE_DIR, KAFKA_BROKER, SERVICE_ORDER, TOPIC_CONTROL, TOPIC_MISSION, TOPIC_STATUS
from shared.pipeline_params import (
    PARAMETER_METADATA,
    PIPELINE_DEFAULTS,
)

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
status_history = deque(maxlen=300)
mission_state_lock = threading.Lock()
mission_states: dict[str, dict] = {}
mission_workspace_dirs: dict[str, str] = {}

TERMINAL_STATUSES = {"success", "error"}
MISSION_PROCESSING_STALE_SECONDS = float(os.getenv("MISSION_PROCESSING_STALE_SECONDS", "120"))


def make_host_path(path: str) -> str:
    if path.startswith("/host"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return "/host" + path


def resolve_workspace_dir(workspace_value: str | None, vol_id: str) -> str:
    workspace_root = os.path.normpath(workspace_value or DEFAULT_WORKSPACE_DIR)
    if os.path.basename(workspace_root) == vol_id:
        return workspace_root
    return os.path.join(workspace_root, vol_id)


def load_workspace_mission_state(vol_id: str, workspace_dir: str | None = None) -> dict | None:
    candidate_roots: list[str] = []
    if workspace_dir:
        candidate_roots.append(workspace_dir)

    with mission_state_lock:
        tracked_root = mission_workspace_dirs.get(vol_id)
    if tracked_root:
        candidate_roots.append(tracked_root)

    candidate_roots.append(DEFAULT_WORKSPACE_DIR)

    seen: set[str] = set()
    for root in candidate_roots:
        normalized = os.path.normpath(root)
        if normalized in seen:
            continue
        seen.add(normalized)

        state_path = os.path.join(make_host_path(resolve_workspace_dir(normalized, vol_id)), "mission_state.json")
        if not os.path.exists(state_path):
            continue
        try:
            with open(state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

    return None


def snapshot_mission_state(mission: dict) -> dict:
    return {
        "vol_id": mission["vol_id"],
        "services": dict(mission["services"]),
        "logs": list(mission["logs"]),
        "updated_at": mission["updated_at"],
        "overall_status": mission["overall_status"],
        "workspace_dir": mission.get("workspace_dir"),
    }


def parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return None
    return None


def is_processing_stale(mission_updated_at: float | None, workspace_state: dict | None) -> bool:
    timestamps = [parse_timestamp(mission_updated_at)]
    if workspace_state is not None:
        timestamps.append(parse_timestamp(workspace_state.get("updated_at")))

    valid_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    if not valid_timestamps:
        return False

    latest_update = max(valid_timestamps)
    return (time.time() - latest_update) > MISSION_PROCESSING_STALE_SECONDS

class MissionParams(BaseModel):
    vol_id: str
    input_dir: str
    workspace_dir: str = DEFAULT_WORKSPACE_DIR
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


def compute_overall_status(services: dict) -> str:
    if not services:
        return "idle"

    statuses = [payload.get("status", "processing") for payload in services.values()]
    if any(status == "error" for status in statuses):
        return "error"
    if services and all(status == "success" for status in statuses if status):
        seen = set(services.keys())
        if all(service in seen for service in SERVICE_ORDER):
            return "success"
    return "processing"


def resolve_mission_overall_status(mission: dict, workspace_state: dict | None) -> str:
    services = mission.get("services", {})
    workspace_status = workspace_state.get("status") if workspace_state else None
    has_processing_service = any(payload.get("status") == "processing" for payload in services.values())

    if any(payload.get("status") == "error" for payload in services.values()):
        return "error"

    if workspace_status in TERMINAL_STATUSES and not has_processing_service:
        return workspace_status

    computed = compute_overall_status(services)
    if computed != "processing":
        return computed

    if has_processing_service and is_processing_stale(mission.get("updated_at"), workspace_state):
        if workspace_status in TERMINAL_STATUSES:
            return workspace_status
        return "error"

    return computed


def update_mission_state(payload: dict):
    vol_id = payload.get("vol_id")
    if not vol_id:
        return

    now = time.time()
    service = payload.get("service") or "UNKNOWN"
    log = payload.get("log")

    with mission_state_lock:
        mission = mission_states.setdefault(
            vol_id,
            {
                "vol_id": vol_id,
                "services": {},
                "logs": deque(maxlen=200),
                "updated_at": now,
                "overall_status": "idle",
                "workspace_dir": mission_workspace_dirs.get(vol_id),
            },
        )
        mission["services"][service] = payload
        mission["updated_at"] = now
        if mission.get("workspace_dir") is None:
            mission["workspace_dir"] = mission_workspace_dirs.get(vol_id)
        if log:
            mission["logs"].append({
                "service": service,
                "step": payload.get("step"),
                "status": payload.get("status"),
                "message": log,
                "ts": now,
            })
        mission["overall_status"] = compute_overall_status(mission["services"])


def build_colmap_resume_state(services: dict, workspace_state: dict | None, mission_updated_at: float | None = None) -> dict:
    colmap_service = services.get("COLMAP", {})
    colmap_status = colmap_service.get("status") or workspace_state.get("status") if workspace_state else None
    stale_processing = colmap_service.get("status") == "processing" and is_processing_stale(mission_updated_at, workspace_state)
    downstream_processing = [
        service_name
        for service_name, payload in services.items()
        if service_name != "COLMAP" and payload.get("status") == "processing"
    ]

    if colmap_service.get("status") == "processing" and not stale_processing:
        return {
            "available": False,
            "state": "running",
            "reason": "COLMAP is currently running. Resume is only relevant after an interruption.",
            "downstream_processing": downstream_processing,
        }
    if stale_processing and workspace_state is not None:
        return {
            "available": True,
            "state": "checkpointed",
            "reason": "The last live COLMAP status update is stale. The saved workspace checkpoint can be used to resume the mission.",
            "downstream_processing": downstream_processing,
        }
    if stale_processing:
        return {
            "available": False,
            "state": "stale",
            "reason": "The last live COLMAP status update is stale and no saved workspace checkpoint is available.",
            "downstream_processing": downstream_processing,
        }
    if colmap_status == "success" or (workspace_state and workspace_state.get("step") == "DONE"):
        return {
            "available": False,
            "state": "completed",
            "reason": "COLMAP has already completed for this mission. Downstream processing can continue without restarting COLMAP." if downstream_processing else "COLMAP has already completed for this mission.",
            "downstream_processing": downstream_processing,
        }
    if colmap_status == "error":
        return {
            "available": True,
            "state": "resumable",
            "reason": "COLMAP stopped with an error. A resume action can restart from the saved workspace checkpoint.",
            "downstream_processing": downstream_processing,
        }
    if workspace_state is not None:
        return {
            "available": True,
            "state": "checkpointed",
            "reason": "A saved COLMAP workspace checkpoint exists. If the worker is no longer running, the mission can restart from that checkpoint.",
            "downstream_processing": downstream_processing,
        }
    return {
        "available": False,
        "state": "unavailable",
        "reason": "No saved COLMAP workspace checkpoint has been found yet.",
        "downstream_processing": downstream_processing,
    }


def serialize_mission_state(mission: dict) -> dict:
    services = {
        name: mission["services"][name]
        for name in SERVICE_ORDER
        if name in mission["services"]
    }
    for name, payload in mission["services"].items():
        if name not in services:
            services[name] = payload
    workspace_state = load_workspace_mission_state(mission["vol_id"], mission.get("workspace_dir"))
    colmap_resume = build_colmap_resume_state(services, workspace_state, mission.get("updated_at"))
    overall_status = resolve_mission_overall_status(mission, workspace_state)

    return {
        "vol_id": mission["vol_id"],
        "workspace_dir": mission.get("workspace_dir"),
        "workspace_state": workspace_state,
        "colmap_resume": colmap_resume,
        "services": services,
        "logs": list(mission["logs"]),
        "updated_at": mission["updated_at"],
        "overall_status": overall_status,
    }


def get_status_summary() -> dict:
    with mission_state_lock:
        mission_snapshots = [snapshot_mission_state(mission) for mission in mission_states.values()]

    serialized = [serialize_mission_state(mission) for mission in mission_snapshots]

    serialized.sort(key=lambda item: item["updated_at"], reverse=True)
    active = next((item for item in serialized if item["overall_status"] == "processing"), None)
    if active is None and serialized:
        active = serialized[0]

    return {
        "active_vol_id": active["vol_id"] if active else None,
        "missions": serialized,
    }


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
    namespace = os.getenv("POD_NAMESPACE", "kafka")
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

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    loop = asyncio.get_running_loop()
    threading.Thread(target=kafka_consumer_thread_func, args=(loop,), daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/mission/cancel")
async def cancel_mission(vol_id: str):
    msg = {"vol_id": vol_id, "command": "cancel"}
    producer.produce(TOPIC_CONTROL, key=vol_id, value=json.dumps(msg))
    producer.flush()
    return {"status": "success", "message": f"Cancel command sent for {vol_id}"}

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Drone Pipeline API is alive"}


@app.get("/status/summary")
def status_summary():
    return get_status_summary()


@app.get("/mission/state")
def mission_workspace_state(vol_id: str, workspace_dir: str | None = None):
    return {
        "vol_id": vol_id,
        "workspace_state": load_workspace_mission_state(vol_id, workspace_dir),
    }


@app.post("/mission/resume")
async def resume_mission(vol_id: str, workspace_dir: str | None = None):
    with mission_state_lock:
        mission = mission_states.get(vol_id)
        services = dict(mission.get("services", {})) if mission else {}
        tracked_workspace_dir = workspace_dir or mission_workspace_dirs.get(vol_id)

    workspace_state = load_workspace_mission_state(vol_id, tracked_workspace_dir)
    colmap_resume = build_colmap_resume_state(services, workspace_state)
    if not colmap_resume["available"]:
        return {
            "status": "error",
            "message": colmap_resume["reason"],
            "colmap_resume": colmap_resume,
        }

    if workspace_state is None:
        return {
            "status": "error",
            "message": f"No mission_state.json found for {vol_id}.",
            "colmap_resume": colmap_resume,
        }

    mission_payload = dict(workspace_state.get("mission") or {})
    if not mission_payload:
        return {
            "status": "error",
            "message": f"Saved workspace state for {vol_id} does not contain the original mission payload.",
            "colmap_resume": colmap_resume,
        }

    mission_payload["vol_id"] = vol_id
    if tracked_workspace_dir:
        mission_payload["workspace_dir"] = tracked_workspace_dir

    with mission_state_lock:
        if mission_payload.get("workspace_dir"):
            mission_workspace_dirs[vol_id] = mission_payload["workspace_dir"]

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
def browse_path(path: str = "/"):
    if not os.path.exists(path):
        return {"error": "Path does not exist"}
    
    try:
        items = []
        for item in os.listdir(path):
            try:
                full_path = os.path.join(path, item)
                is_dir = os.path.isdir(full_path)
                items.append({
                    "name": item,
                    "path": full_path,
                    "is_dir": is_dir,
                    "image_count": len([f for f in os.listdir(full_path) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]) if is_dir else 0
                })
            except PermissionError:
                continue
        return sorted(items, key=lambda x: (not x["is_dir"], x["name"]))
    except PermissionError:
        return []
    except Exception as e:
        return {"error": str(e)}

@app.get("/datasets")
def list_datasets(base_path: str = os.getenv("INPUT_DIR", "/host/mnt/j/workspace")):
    if not os.path.exists(base_path):
        return []
    # Liste uniquement les dossiers contenant au moins une image
    results = []
    for d in os.listdir(base_path):
        full_path = os.path.join(base_path, d)
        if os.path.isdir(full_path):
            images = [f for f in os.listdir(full_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if images:
                results.append({"name": d, "path": full_path, "image_count": len(images)})
    return results

@app.post("/mission")
async def start_mission(params: MissionParams):
    with mission_state_lock:
        mission_workspace_dirs[params.vol_id] = params.workspace_dir
        existing = mission_states.get(params.vol_id)
        if existing is not None:
            existing["workspace_dir"] = params.workspace_dir
    msg = params.dict()
    producer.produce(TOPIC_MISSION, key=params.vol_id, value=json.dumps(msg))
    producer.flush()
    return {"status": "success", "vol_id": params.vol_id}

@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)
