"""HTTP observability boundary for the dashboard API."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from fastapi import Request, Response

from shared.observability import observe_http_request

logger = logging.getLogger("droneai.http")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)
request_correlation_id: ContextVar[str | None] = ContextVar(
    "request_correlation_id",
    default=None,
)


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "").strip()
    if _REQUEST_ID.fullmatch(candidate):
        return cast(str, candidate)
    return str(uuid4())


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


async def operational_metrics_middleware(
    request: Request,
    call_next: Any,
) -> Response:
    """Measure one HTTP request without labeling customer-controlled paths."""

    started = perf_counter()
    request_id = _request_id(request)
    request.state.request_id = request_id
    token = request_correlation_id.set(request_id)
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = _route_template(request)
        duration = max(0.0, perf_counter() - started)
        method = request.method if request.method in _HTTP_METHODS else "OTHER"
        try:
            observe_http_request(
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration,
            )
            if status_code >= 500:
                logger.warning(
                    "HTTP request failed request_id=%s method=%s route=%s status=%d duration_seconds=%.3f",
                    request_id,
                    method,
                    route,
                    status_code,
                    duration,
                )
        except Exception:
            logger.exception(
                "HTTP operational telemetry failed request_id=%s",
                request_id,
            )
        finally:
            request_correlation_id.reset(token)
