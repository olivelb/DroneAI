"""Composition of dashboard HTTP security and observability middleware."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

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


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies before FastAPI parses route models or forms."""

    def __init__(self, app: Any, *, max_body_bytes: int) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            if declared_length > self.max_body_bytes:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self.max_body_bytes:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        request._body = bytes(body)
        return await call_next(request)


def configure_http_middleware(application: FastAPI) -> None:
    application.add_middleware(
        CORSMiddleware,
        allow_origins=security.configured_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
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
