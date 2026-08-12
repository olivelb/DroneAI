"""FastAPI composition root for the DroneAI dashboard API."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import security
from .control_runtime import embedded_control_loops_enabled, start_control_loops
from .health import database_is_ready
from .rate_limit import RasterTileRateLimitMiddleware
from .realtime import consume_status_events, status_hub
from .routers.datasets import router as datasets_router
from .routers.identity import router as identity_router
from .routers.maps import router as maps_router
from .routers.missions import router as missions_router
from .routers.operations import router as operations_router

@asynccontextmanager
async def lifespan(application: FastAPI):
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()
    consumer_thread = threading.Thread(
        target=consume_status_events,
        args=(loop,),
        kwargs={"stop_event": stop_event},
        daemon=True,
    )
    consumer_thread.start()
    control_supervisor = (
        start_control_loops(stop_event)
        if embedded_control_loops_enabled()
        else None
    )
    application.state.control_supervisor = control_supervisor
    try:
        yield
    finally:
        stop_event.set()
        consumer_thread.join(timeout=2)
        if control_supervisor is not None:
            control_supervisor.stop()


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

    application.include_router(identity_router)
    application.include_router(missions_router)
    application.include_router(datasets_router)
    application.include_router(maps_router)
    application.include_router(operations_router)

    @application.get("/")
    def read_root():
        return {"status": "ok", "message": "Drone Pipeline API is alive"}

    @application.get("/live", include_in_schema=False)
    def read_liveness():
        return {"status": "ok"}

    @application.get("/ready", include_in_schema=False)
    def read_readiness():
        if not database_is_ready():
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable"},
            )
        return {"status": "ok"}

    @application.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        principal = await security.authorize_websocket(websocket)
        if principal is None:
            return
        await status_hub.connect(
            websocket,
            f"{principal.organization_id}:{principal.subject}",
        )
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
