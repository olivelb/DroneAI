"""Composition of dashboard HTTP security and observability middleware."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import rate_limit, security
from .observability import operational_metrics_middleware


def configured_http_max_body_bytes() -> int:
    try:
        value = int(os.getenv("DRONEAI_HTTP_MAX_BODY_BYTES", "2097152"))
    except ValueError as error:
        raise RuntimeError("DRONEAI_HTTP_MAX_BODY_BYTES must be an integer") from error
    if not 4096 <= value <= 16 * 1024 * 1024:
        raise RuntimeError(
            "DRONEAI_HTTP_MAX_BODY_BYTES must be between 4096 and 16777216"
        )
    return value


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Streaming ASGI body limit without Starlette private request state."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )(scope, receive, send)
                return
            if declared_length < 0:
                await JSONResponse(
                    status_code=400, content={"detail": "Invalid Content-Length"}
                )(scope, receive, send)
                return
            if declared_length > self.max_body_bytes:
                await JSONResponse(
                    status_code=413, content={"detail": "Request body too large"}
                )(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await JSONResponse(
                status_code=413, content={"detail": "Request body too large"}
            )(scope, receive, send)


def configure_http_middleware(application: FastAPI) -> None:
    application.add_middleware(rate_limit.RasterTileRateLimitMiddleware)
    application.add_middleware(rate_limit.OrganizationRequestQuotaMiddleware)
    application.add_middleware(rate_limit.IdentityRateLimitMiddleware)

    async def cookie_csrf_guard(request: Request, call_next: Any) -> Any:
        try:
            security.enforce_cookie_csrf(request)
        except HTTPException as error:
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
            )
        return await call_next(request)

    application.middleware("http")(cookie_csrf_guard)
    application.middleware("http")(operational_metrics_middleware)
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=configured_http_max_body_bytes(),
    )


class DashboardApplication(FastAPI):  # type: ignore[misc]
    """Keep CORS outside even the ASGI server error and early rejection layers."""

    def build_middleware_stack(self) -> ASGIApp:
        return CORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=security.configured_cors_origins(),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-API-Key",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID", "Retry-After"],
        )
