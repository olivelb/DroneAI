"""FastAPI composition root for the DroneAI dashboard API."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from . import security
from .control_runtime import embedded_control_loops_enabled, start_control_loops
from .health import database_is_ready, readiness_payload
from .http_middleware import DashboardApplication, configure_http_middleware
from .realtime import consume_status_events, serve_status_connection, status_hub
from .routers.datasets import router as datasets_router
from .routers.identity import router as identity_router
from .routers.maps import router as maps_router
from .routers.missions import router as missions_router
from .routers.operations import router as operations_router
from .routers.organization_saas import router as organization_saas_router
from .routers.platform import router as platform_router
from shared.observability import start_metrics_server


@asynccontextmanager
async def lifespan(application: FastAPI):
    metrics_server = start_metrics_server()
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
        start_control_loops(stop_event) if embedded_control_loops_enabled() else None
    )
    application.state.control_supervisor = control_supervisor
    try:
        yield
    finally:
        stop_event.set()
        consumer_thread.join(timeout=2)
        if control_supervisor is not None:
            control_supervisor.stop()
        if metrics_server is not None:
            metrics_server.stop()


def create_app() -> FastAPI:
    security.validate_production_configuration()
    application = DashboardApplication(lifespan=lifespan)
    configure_http_middleware(application)

    application.include_router(identity_router)
    application.include_router(missions_router)
    application.include_router(datasets_router)
    application.include_router(maps_router)
    application.include_router(operations_router)
    application.include_router(organization_saas_router)
    application.include_router(platform_router)

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
                content=readiness_payload("unavailable"),
            )
        return readiness_payload("ok")

    @application.websocket("/ws/status")
    async def websocket_endpoint(websocket: WebSocket):
        authorization = await security.authorize_websocket(websocket)
        if authorization is None:
            return
        await serve_status_connection(websocket, authorization)

    return application


app = create_app()

manager = status_hub
kafka_consumer_thread_func = consume_status_events
status_history = status_hub.history
