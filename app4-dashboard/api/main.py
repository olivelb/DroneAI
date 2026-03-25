import os
import json
import asyncio
import sys
import threading
import time
import ssl
import math
import struct
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
    FUSION_BYTES_PER_PIXEL,
    FUSION_CHUNK_BYTES_PER_PIXEL,
    PARAMETER_METADATA,
    PIPELINE_DEFAULTS,
    RECOMMENDED_IMAGE_SIZES,
    TARGET_CACHED_IMAGES,
    merge_pipeline_params,
)

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
status_history = deque(maxlen=300)
mission_state_lock = threading.Lock()
mission_states: dict[str, dict] = {}

TERMINAL_STATUSES = {"success", "error"}

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
    sam_prompt: str = "car"
    classes: list[str] = Field(default_factory=lambda: ["car"])
    colmap_params: dict[str, Any] = Field(default_factory=dict)


class EstimateRequest(BaseModel):
    input_dir: str
    pipeline: str = "modern"
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
            },
        )
        mission["services"][service] = payload
        mission["updated_at"] = now
        if log:
            mission["logs"].append({
                "service": service,
                "step": payload.get("step"),
                "status": payload.get("status"),
                "message": log,
                "ts": now,
            })
        mission["overall_status"] = compute_overall_status(mission["services"])


def serialize_mission_state(mission: dict) -> dict:
    services = {
        name: mission["services"][name]
        for name in SERVICE_ORDER
        if name in mission["services"]
    }
    for name, payload in mission["services"].items():
        if name not in services:
            services[name] = payload
    return {
        "vol_id": mission["vol_id"],
        "services": services,
        "logs": list(mission["logs"]),
        "updated_at": mission["updated_at"],
        "overall_status": mission["overall_status"],
    }


def get_status_summary() -> dict:
    with mission_state_lock:
        serialized = [serialize_mission_state(mission) for mission in mission_states.values()]

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


def parse_meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, raw_value = line.split(":", 1)
                value = raw_value.strip().split()[0]
                result[key] = int(value) * 1024
    except OSError:
        pass
    return result


def format_gib(value: float) -> float:
    return round(value / (1024 ** 3), 2)


def get_system_resources() -> dict[str, Any]:
    meminfo = parse_meminfo()
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    free = meminfo.get("MemFree", 0)
    return {
        "memory": {
            "total_bytes": total,
            "available_bytes": available,
            "free_bytes": free,
            "total_gib": format_gib(total),
            "available_gib": format_gib(available),
            "free_gib": format_gib(free),
        }
    }


def read_image_dimensions(path: str) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                return struct.unpack(">II", header[16:24])
            if header[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker_start = handle.read(1)
                    if not marker_start:
                        break
                    if marker_start != b"\xff":
                        continue
                    marker = handle.read(1)
                    while marker == b"\xff":
                        marker = handle.read(1)
                    if marker in {b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"}:
                        size = struct.unpack(">H", handle.read(2))[0]
                        data = handle.read(size - 2)
                        height, width = struct.unpack(">HH", data[1:5])
                        return width, height
                    if marker in {b"\xd8", b"\xd9"}:
                        continue
                    size_bytes = handle.read(2)
                    if len(size_bytes) != 2:
                        break
                    size = struct.unpack(">H", size_bytes)[0]
                    handle.seek(size - 2, os.SEEK_CUR)
            if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                chunk_type = header[12:16]
                if chunk_type == b"VP8X":
                    width = 1 + int.from_bytes(header[24:27], "little")
                    height = 1 + int.from_bytes(header[27:30], "little")
                    return width, height
    except OSError:
        return None
    return None


def scale_dimensions(width: int, height: int, max_image_size: int) -> tuple[int, int]:
    longest_side = max(width, height)
    if longest_side <= max_image_size:
        return width, height
    scale = max_image_size / float(longest_side)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def build_fusion_chunks(image_entries: list[dict[str, Any]], target_bytes: int) -> list[list[dict[str, Any]]]:
    if not image_entries:
        return []

    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    current_bytes = 0

    for entry in image_entries:
        estimated_total_bytes = entry["pixels"] * FUSION_CHUNK_BYTES_PER_PIXEL
        if current_chunk and current_bytes + estimated_total_bytes > target_bytes:
            chunks.append(current_chunk)
            current_chunk = []
            current_bytes = 0
        current_chunk.append(entry)
        current_bytes += estimated_total_bytes

    if current_chunk:
        chunks.append(current_chunk)

    return chunks

def estimate_for_input_dir(input_dir: str, pipeline: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    params = merge_pipeline_params(pipeline, overrides)
    max_image_size = max(1, int(float(params["fusion_max_image_size"])))
    cache_size_gib = max(0.5, float(params["fusion_cache_size"]))
    chunk_target_gib = max(4.0, float(params.get("fusion_chunk_target_memory_gib", "16")))
    cache_bytes = int(cache_size_gib * (1024 ** 3))
    chunk_target_bytes = int(chunk_target_gib * (1024 ** 3))

    image_entries = []
    if os.path.isdir(input_dir):
        for name in sorted(os.listdir(input_dir)):
            if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            full_path = os.path.join(input_dir, name)
            dims = read_image_dimensions(full_path)
            if not dims:
                continue
            width, height = dims
            scaled_width, scaled_height = scale_dimensions(width, height, max_image_size)
            pixels = scaled_width * scaled_height
            fusion_bytes = pixels * FUSION_BYTES_PER_PIXEL
            image_entries.append({
                "name": name,
                "width": width,
                "height": height,
                "scaled_width": scaled_width,
                "scaled_height": scaled_height,
                "pixels": pixels,
                "fusion_bytes": fusion_bytes,
            })

    image_entries.sort(key=lambda item: item["fusion_bytes"], reverse=True)
    chunk_plan = build_fusion_chunks(image_entries, chunk_target_bytes)
    total_bytes = sum(item["fusion_bytes"] for item in image_entries)
    images_fit = 0
    running = 0
    for item in image_entries:
        if running + item["fusion_bytes"] > cache_bytes:
            break
        running += item["fusion_bytes"]
        images_fit += 1

    resources = get_system_resources()["memory"]
    available_bytes = resources["available_bytes"]
    reserve_bytes = max(4 * (1024 ** 3), int(resources["total_bytes"] * 0.25))
    safe_cache_bytes = max(int(0.5 * (1024 ** 3)), int((available_bytes - reserve_bytes) * 0.7))
    recommended_cache_gib = max(0.5, round((safe_cache_bytes / (1024 ** 3)) * 2) / 2)

    def can_hold_target(test_size: int) -> bool:
        footprints = []
        for entry in image_entries:
            scaled_width, scaled_height = scale_dimensions(entry["width"], entry["height"], test_size)
            footprints.append(scaled_width * scaled_height * FUSION_BYTES_PER_PIXEL)
        footprints.sort(reverse=True)
        return sum(footprints[:TARGET_CACHED_IMAGES]) <= safe_cache_bytes * 0.85

    recommended_image_size = max_image_size
    if image_entries:
        candidates = [size for size in RECOMMENDED_IMAGE_SIZES if size <= max(max_image_size, RECOMMENDED_IMAGE_SIZES[-1])]
        viable = [size for size in candidates if can_hold_target(size)]
        if viable:
            recommended_image_size = viable[-1]
        else:
            recommended_image_size = candidates[0]

    return {
        "pipeline": pipeline,
        "params": params,
        "dataset": {
            "path": input_dir,
            "image_count": len(image_entries),
            "sampled_images": image_entries[:5],
        },
        "memory": {
            "formula": f"pixels x {FUSION_BYTES_PER_PIXEL} bytes (RGB uint8 + depth f32 + normal f32x3)",
            "cache_size_gib": cache_size_gib,
            "cache_size_bytes": cache_bytes,
            "total_fusion_bytes": total_bytes,
            "total_fusion_gib": format_gib(total_bytes),
            "largest_image_bytes": image_entries[0]["fusion_bytes"] if image_entries else 0,
            "largest_image_gib": format_gib(image_entries[0]["fusion_bytes"]) if image_entries else 0,
            "images_fit_in_cache": images_fit,
            "target_cached_images": TARGET_CACHED_IMAGES,
            "available_ram_gib": resources["available_gib"],
            "safe_cache_gib": round(safe_cache_bytes / (1024 ** 3), 2),
        },
        "chunking": {
            "target_memory_gib": chunk_target_gib,
            "estimated_formula": f"pixels x {FUSION_CHUNK_BYTES_PER_PIXEL} bytes (fusion cache + overhead heuristic)",
            "estimated_chunk_count": max(1, len(chunk_plan)),
            "largest_chunk_images": max((len(chunk) for chunk in chunk_plan), default=0),
        },
        "recommendation": {
            "fusion_cache_size": recommended_cache_gib,
            "fusion_max_image_size": recommended_image_size,
            "reason": f"Keeps roughly 70% of currently available RAM minus a 25% system reserve, chooses the highest tested image size that can keep the 8 heaviest scaled images inside that budget, and estimates fusion chunks against a target of {chunk_target_gib:.1f} GiB.",
        },
    }


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


@app.get("/pods")
def pod_statuses():
    return get_pod_states()


@app.get("/system/resources")
def system_resources():
    return get_system_resources()


@app.get("/mission/parameters")
def mission_parameters():
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "metadata": PARAMETER_METADATA,
    }


@app.post("/mission/estimate")
def mission_estimate(request: EstimateRequest):
    return estimate_for_input_dir(request.input_dir, request.pipeline, request.colmap_params)

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
