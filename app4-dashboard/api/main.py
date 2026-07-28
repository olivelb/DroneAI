"""FastAPI composition root for the DroneAI dashboard API."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from shared.database import get_session
from shared.inbox_outbox import run_outbox_dispatcher

from . import security
from .kubernetes_status import fallback_pod_states, get_pod_states
from .messaging import get_producer, publish_outbox_event
from .mission_state import (
    build_colmap_resume_state,
    compute_overall_status,
    get_status_summary,
    is_mission_stale,
    serialize_mission,
    update_mission_state,
)
from .realtime import consume_status_events, status_hub
from .routers.auth import router as auth_router
from .routers.datasets import router as datasets_router
from .routers.missions import router as missions_router


logger = logging.getLogger("droneai.api")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()
    consumer_thread = threading.Thread(
        target=consume_status_events,
        args=(loop,),
        kwargs={"stop_event": stop_event},
        daemon=True,
    )
    outbox_thread = threading.Thread(
        target=run_outbox_dispatcher,
        args=(get_session,),
        kwargs={
            "publisher": publish_outbox_event,
            "stop_event": stop_event,
            "logger": logger,
        },
        daemon=True,
    )
    consumer_thread.start()
    outbox_thread.start()
    try:
        yield
    finally:
        stop_event.set()
        consumer_thread.join(timeout=2)
        outbox_thread.join(timeout=2)


def create_app() -> FastAPI:
    security.validate_production_configuration()
    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=security.configured_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )
    application.include_router(auth_router)
    application.include_router(missions_router)
    application.include_router(datasets_router)

    @application.get("/")
    def read_root():
        return {"status": "ok", "message": "Drone Pipeline API is alive"}

    @application.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        if not await security.authorize_websocket(websocket):
            return
        await status_hub.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            status_hub.disconnect(websocket)

    return application


app = create_app()

manager = status_hub
kafka_consumer_thread_func = consume_status_events
status_history = status_hub.history

__all__ = [
    "app",
    "build_colmap_resume_state",
    "compute_overall_status",
    "create_app",
    "fallback_pod_states",
    "get_pod_states",
    "get_status_summary",
    "is_mission_stale",
    "kafka_consumer_thread_func",
    "manager",
    "get_producer",
    "serialize_mission",
    "status_history",
    "update_mission_state",
]
