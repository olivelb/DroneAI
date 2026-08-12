"""Composition of dashboard HTTP security and observability middleware."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import rate_limit, security
from .observability import operational_metrics_middleware


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
