"""Rate limiting middleware for expensive raster tile rendering."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import security


class RasterTileRateLimitMiddleware(BaseHTTPMiddleware):
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

        client_key = request.client.host if request.client else "unknown"
        retry_after = security.tile_rate_limiter.consume(client_key)
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
