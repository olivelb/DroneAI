"""Rate limiting middleware for expensive raster tile rendering."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Awaitable, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from shared.database import get_session
from shared.organization_saas import RequestQuotaDecision, consume_request_quota

from . import security

logger = logging.getLogger("droneai.rate_limit")


def organization_request_quotas_enabled() -> bool:
    raw = os.getenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "false",
    ).strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError(
            "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED must be true or false"
        )
    return raw == "true"


def rate_limit_identity(request: Request) -> str:
    """Prefer the authenticated subject over ingress-dependent source addresses."""
    principal = security.authenticate_request(request)
    if principal is not None:
        return (
            f"organization:{principal.organization_id}:"
            f"subject:{principal.subject}"
        )
    peer = request.client.host if request.client else "unknown"
    return f"peer:{peer}"


class RasterTileRateLimitMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        is_tile_request = (
            request.method == "GET"
            and request.url.path.startswith("/maps/")
            and "/tiles/" in request.url.path
            and request.url.path.endswith(".png")
        )
        if not is_tile_request:
            return await call_next(request)

        retry_after = await run_in_threadpool(
            security.tile_rate_limiter.consume,
            rate_limit_identity(request),
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Raster tile rate limit exceeded"},
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
        response = await call_next(request)
        response.headers.setdefault(
            "X-RateLimit-Limit",
            str(security.tile_rate_limiter.requests_per_minute),
        )
        return response


class OrganizationRequestQuotaMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """Enforce an optional commercial request budget across API replicas."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not organization_request_quotas_enabled():
            return await call_next(request)
        if request.method == "OPTIONS" or request.url.path in {
            "/",
            "/live",
            "/ready",
        }:
            return await call_next(request)
        principal = security.authenticate_request(request)
        if principal is None or principal.realm != "tenant":
            return await call_next(request)
        try:
            decision = await run_in_threadpool(
                lambda: _consume_organization_request(
                    principal.organization_id,
                    principal.subject,
                )
            )
        except Exception:
            logger.exception(
                "Organization request quota failed for %s",
                principal.organization_id,
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Organization request quota unavailable"},
            )
        if decision.retry_after_seconds is not None:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Organization request quota exceeded"},
                headers={
                    "Retry-After": str(
                        max(1, math.ceil(decision.retry_after_seconds))
                    ),
                    "X-RateLimit-Scope": "organization",
                    "X-RateLimit-Limit": str(decision.requests_per_minute),
                },
            )
        response = await call_next(request)
        if decision.requests_per_minute is not None:
            response.headers.setdefault("X-RateLimit-Scope", "organization")
            response.headers.setdefault(
                "X-RateLimit-Limit",
                str(decision.requests_per_minute),
            )
        return response


def _consume_organization_request(
    organization_id: str,
    actor_subject: str,
) -> RequestQuotaDecision:
    with get_session(organization_id=organization_id) as session:
        return consume_request_quota(
            session,
            organization_id=organization_id,
            actor_subject=actor_subject,
        )
