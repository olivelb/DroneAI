"""Keep internal exceptions behind the HTTP boundary with log correlation."""
from __future__ import annotations

import logging
from typing import cast
from uuid import uuid4

from fastapi import Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger("droneai.http.errors")


async def api_error_response(request: Request, error: Exception) -> Response:
    if isinstance(error, HTTPException) and error.status_code < 500:
        return cast(Response, await http_exception_handler(request, error))
    status_code = error.status_code if isinstance(error, HTTPException) else 500
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    logger.error(
        "API exception request_id=%s status=%d", request_id, status_code,
        exc_info=(type(error), error, error.__traceback__),
    )
    headers = {"X-Request-ID": request_id}
    if isinstance(error, HTTPException) and error.headers:
        for name, value in error.headers.items():
            if name.lower() == "retry-after":
                headers["Retry-After"] = value
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Unable to process request", "request_id": request_id},
        headers=headers,
    )
