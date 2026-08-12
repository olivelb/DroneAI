"""Rate limiting middleware for expensive raster tile rendering."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from shared.database import get_session
from shared.identity import credential_id_from_token
from shared.identity_capabilities import capability_id_from_token
from shared.organization_saas import RequestQuotaDecision, consume_request_quota
from shared.platform_identity import platform_credential_id_from_token

from . import security

logger = logging.getLogger("droneai.rate_limit")

BODY_CREDENTIAL_PATHS = {
    ("POST", "/auth/session"),
    ("POST", "/auth/capabilities/redeem"),
}


def _unverified_request_token(request: Request) -> str | None:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return str(api_key).strip()
    authorization = str(request.headers.get("authorization", ""))
    scheme, _, bearer = authorization.partition(" ")
    if scheme.lower() == "bearer" and bearer.strip():
        return bearer.strip()
    cookie = request.cookies.get(security.SESSION_COOKIE_NAME)
    return str(cookie).strip() if cookie else None


def _public_credential_identity(token: str | None) -> str | None:
    if not token:
        return None
    for realm, parser in (
        ("tenant", credential_id_from_token),
        ("platform", platform_credential_id_from_token),
        ("capability", capability_id_from_token),
    ):
        public_id = parser(token)
        if public_id is not None:
            return f"{realm}:{public_id}"
    return None


def consume_identity_rate_limit(
    request: Request,
    token: str | None = None,
) -> tuple[float | None, int]:
    """Consume peer-wide and public-credential buckets without a DB lookup."""

    peer = request.client.host if request.client else "unknown"
    retry_after = security.identity_peer_rate_limiter.consume(
        f"identity:peer:{peer}"
    )
    if retry_after is not None:
        return (
            retry_after,
            security.identity_peer_rate_limiter.requests_per_minute,
        )
    public_identity = _public_credential_identity(
        token if token is not None else _unverified_request_token(request)
    )
    if public_identity is None:
        return None, security.identity_peer_rate_limiter.requests_per_minute
    retry_after = security.identity_credential_rate_limiter.consume(
        f"identity:credential:{public_identity}"
    )
    return (
        retry_after,
        security.identity_credential_rate_limiter.requests_per_minute,
    )


def enforce_identity_rate_limit(
    request: Request,
    token: str | None = None,
) -> None:
    """Fail closed before an identity credential causes database work."""

    try:
        retry_after, limit = consume_identity_rate_limit(request, token)
    except Exception as error:
        logger.exception("Identity rate limiter unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity rate limiter unavailable",
        ) from error
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Identity request rate limit exceeded",
            headers={
                "Retry-After": str(max(1, math.ceil(retry_after))),
                "X-RateLimit-Scope": "identity",
                "X-RateLimit-Limit": str(limit),
            },
        )


class IdentityRateLimitMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """Protect identity routes before their authentication dependencies run."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        identity_path = request.url.path.startswith(("/auth", "/platform"))
        skip = (request.method, request.url.path) in BODY_CREDENTIAL_PATHS or (
            request.method == "DELETE" and request.url.path == "/auth/session"
        )
        if not identity_path or skip:
            return await call_next(request)
        try:
            await run_in_threadpool(enforce_identity_rate_limit, request)
        except HTTPException as error:
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers=error.headers,
            )
        return await call_next(request)


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
