import os
import json
import asyncio
import threading
import time
import ssl
import urllib.error
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from confluent_kafka import Producer, Consumer
from pydantic import BaseModel

# Configuration Kafka
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.kafka.svc.cluster.local:9092")
TOPIC_MISSION = "vols-bruts"
TOPIC_STATUS = "pipeline-status"
TOPIC_CONTROL = "pipeline-control"

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
status_history = deque(maxlen=300)
mission_state_lock = threading.Lock()
mission_states: dict[str, dict] = {}

SERVICE_ORDER = ["COLMAP", "TILER", "IA"]
TERMINAL_STATUSES = {"success", "error"}

class MissionParams(BaseModel):
    vol_id: str
    input_dir: str
    workspace_dir: str = os.getenv("WORKSPACE_DIR", "/mnt/j/workspace")
    epsg: str = "EPSG:4326"
    camera_model: str = "PINHOLE"
    pipeline: str = "modern"
    tile_size: int = 1024
    ai_confidence: float = 0.5
    classes: list[str] = ["car"]

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
        {"name": "kafka-broker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
        {"name": "colmap-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
        {"name": "ia-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
        {"name": "processing-worker", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
        {"name": "dashboard-api", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
        {"name": "dashboard-frontend", "phase": "unknown", "ready": None, "restarts": None, "reason": "unavailable"},
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
            ready_count = sum(1 for entry in container_statuses if entry.get("ready"))
            total_count = len(container_statuses)
            restarts = sum(entry.get("restartCount", 0) for entry in container_statuses)
            waiting_reason = None
            for entry in container_statuses:
                state = entry.get("state", {})
                if "waiting" in state:
                    waiting_reason = state["waiting"].get("reason")
                    break
            pods.append({
                "name": item.get("metadata", {}).get("name", "unknown"),
                "phase": status.get("phase", "unknown").lower(),
                "ready": f"{ready_count}/{total_count}" if total_count else None,
                "restarts": restarts,
                "reason": waiting_reason or status.get("reason"),
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
