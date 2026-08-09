"""FastAPI composition root for the DroneAI dashboard API."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.database import get_session
from shared.inbox_outbox import run_outbox_dispatcher

from . import dataset_uploads, security
from .messaging import publish_outbox_event
from .rate_limit import RasterTileRateLimitMiddleware
from .realtime import consume_status_events, status_hub
from .stage_orchestrator import start_stage_orchestrator
from .routers.auth import router as auth_router
from .routers.datasets import router as datasets_router
from .routers.maps import router as maps_router
from .routers.missions import router as missions_router
from .routers.operations import router as operations_router

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
    upload_cleanup_thread = threading.Thread(
        target=dataset_uploads.run_upload_cleanup,
        args=(stop_event,),
        daemon=True,
    )
    consumer_thread.start()
    outbox_thread.start()
    upload_cleanup_thread.start()
    stage_orchestrator_thread = start_stage_orchestrator(stop_event)
    try:
        yield
    finally:
        stop_event.set()
        consumer_thread.join(timeout=2)
        outbox_thread.join(timeout=2)
        upload_cleanup_thread.join(timeout=2)
        if stage_orchestrator_thread is not None:
            stage_orchestrator_thread.join(timeout=2)


def create_app() -> FastAPI:
    security.validate_production_configuration()
    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=security.configured_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )
    application.add_middleware(RasterTileRateLimitMiddleware)

    @application.middleware("http")
    async def cookie_csrf_guard(request: Request, call_next):
        try:
            security.enforce_cookie_csrf(request)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
        return await call_next(request)

    application.include_router(auth_router)
    application.include_router(missions_router)
    application.include_router(datasets_router)
    application.include_router(maps_router)
    application.include_router(operations_router)

    @application.get("/")
    def read_root():
        return {"status": "ok", "message": "Drone Pipeline API is alive"}

    @application.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        principal = await security.authorize_websocket(websocket)
        if principal is None:
            return
        await status_hub.connect(websocket, principal.subject)
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
