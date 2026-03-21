import os
import json
import asyncio
import threading
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
