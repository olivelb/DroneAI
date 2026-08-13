"""Deployment security policy and API-key RBAC for the dashboard API."""

from __future__ import annotations

import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from hashlib import sha256
from hmac import new as hmac_new
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, WebSocket, status
from starlette.concurrency import run_in_threadpool

from shared.database import get_session
from shared.deployment_mode import (
    bounded_stage_jobs_enabled,
    is_protected_environment,
)
from shared.identity import (
    AuthenticatedIdentity,
    authenticate_credential,
    credential_id_from_token,
    credential_pepper,
    database_authentication_enabled,
    validate_session_identity,
)
from shared.platform_identity import (
    PLATFORM_PRINCIPAL_ORGANIZATION,
    AuthenticatedPlatformIdentity,
    authenticate_platform_credential,
    platform_credential_id_from_token,
    validate_platform_session_identity,
)
from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    LOCAL_ORGANIZATION_ID,
    bind_organization_id,
    reset_organization_id,
    validate_organization_id,
)
from shared.rate_limiting import (
    DatabaseTokenBucketRateLimiter,
    RateLimiter,
    TokenBucketRateLimiter,
)

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
SESSION_COOKIE_NAME = "droneai_api_key"
LOCAL_SECRET_VALUES = {
    "minioadmin",
    "droneai-local",
    "postgresql://droneai:droneai-local@postgres.drone-ai.svc:5432/droneai",
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    organization_id: str = LEGACY_ORGANIZATION_ID
    member_id: str | None = None
    credential_id: str | None = None
    auth_version: int | None = None
    authentication_method: str = "static"
    realm: str = "tenant"


@dataclass(frozen=True)
class WebSocketAuthorization:
    principal: Principal
    token: str
    peer: str


def _principal_from_identity(identity: AuthenticatedIdentity) -> Principal:
    return Principal(
        subject=identity.subject,
        role=identity.role,
        organization_id=identity.organization_id,
        member_id=identity.member_id,
        credential_id=identity.credential_id,
        auth_version=identity.auth_version,
        authentication_method="database",
    )


def _principal_from_platform_identity(
    identity: AuthenticatedPlatformIdentity,
) -> Principal:
    return Principal(
        subject=identity.subject,
        role=identity.role,
        organization_id=PLATFORM_PRINCIPAL_ORGANIZATION,
        member_id=identity.member_id,
        credential_id=identity.credential_id,
        auth_version=identity.auth_version,
        authentication_method="platform-database",
        realm="platform",
    )


def is_production() -> bool:
    return bool(is_protected_environment())


def build_tile_rate_limiter() -> RateLimiter:
    backend = os.getenv("DRONEAI_TILE_RATE_LIMIT_BACKEND", "").strip().lower()
    if not backend or backend == "auto":
        backend = "database" if is_production() else "local"
    if is_production() and backend == "local":
        raise RuntimeError(
            "Production requires the database-backed tile rate limiter"
        )
    requests_per_minute = int(
        os.getenv("DRONEAI_TILE_RATE_LIMIT_PER_MINUTE", "600")
    )
    burst = int(os.getenv("DRONEAI_TILE_RATE_LIMIT_BURST", "120"))
    max_keys = int(os.getenv("DRONEAI_TILE_RATE_LIMIT_MAX_CLIENTS", "10000"))
    if backend == "database":
        return DatabaseTokenBucketRateLimiter(
            session_scope=get_session,
            requests_per_minute=requests_per_minute,
            burst=burst,
            max_keys=max_keys,
        )
    if backend == "local":
        return TokenBucketRateLimiter(
            requests_per_minute=requests_per_minute,
            burst=burst,
            max_keys=max_keys,
        )
    raise RuntimeError(
        "DRONEAI_TILE_RATE_LIMIT_BACKEND must be 'database' or 'local'"
    )


def build_identity_rate_limiter(*, scope: str) -> RateLimiter:
    """Build one pre-authentication limiter without authenticating a request."""

    if scope not in {"peer", "credential"}:
        raise ValueError("identity rate-limit scope must be peer or credential")
    backend = os.getenv(
        "DRONEAI_IDENTITY_RATE_LIMIT_BACKEND",
        "",
    ).strip().lower()
    if not backend or backend == "auto":
        backend = "database" if is_production() else "local"
    if is_production() and backend == "local":
        raise RuntimeError(
            "Production requires database-backed identity rate limiting"
        )
    prefix = f"DRONEAI_IDENTITY_{scope.upper()}_RATE_LIMIT"
    default_rate = "600" if scope == "peer" else "60"
    default_burst = "120" if scope == "peer" else "10"
    requests_per_minute = int(
        os.getenv(f"{prefix}_PER_MINUTE", default_rate)
    )
    burst = int(os.getenv(f"{prefix}_BURST", default_burst))
    max_keys = int(
        os.getenv("DRONEAI_IDENTITY_RATE_LIMIT_MAX_CLIENTS", "100000")
    )
    if backend == "database":
        return DatabaseTokenBucketRateLimiter(
            session_scope=get_session,
            requests_per_minute=requests_per_minute,
            burst=burst,
            max_keys=max_keys,
        )
    if backend == "local":
        return TokenBucketRateLimiter(
            requests_per_minute=requests_per_minute,
            burst=burst,
            max_keys=max_keys,
        )
    raise RuntimeError(
        "DRONEAI_IDENTITY_RATE_LIMIT_BACKEND must be 'database' or 'local'"
    )


tile_rate_limiter = build_tile_rate_limiter()
identity_peer_rate_limiter = build_identity_rate_limiter(scope="peer")
identity_credential_rate_limiter = build_identity_rate_limiter(
    scope="credential"
)


def static_bootstrap_allowed() -> bool:
    """Return whether transitional static credentials may authenticate."""

    raw = os.getenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP")
    if raw is None:
        return not is_production()
    normalized = raw.strip().lower()
    if normalized not in {"true", "false"}:
        raise RuntimeError(
            "DRONEAI_ALLOW_STATIC_BOOTSTRAP must be true or false"
        )
    return normalized == "true"


def static_bootstrap_credentials_active() -> bool:
    """Expose only whether the transitional registry is currently usable."""

    return static_bootstrap_allowed() and bool(
        os.getenv("DRONEAI_API_KEYS_JSON", "").strip()
    )


def configured_cors_origins() -> list[str]:
    origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "*").split(",") if value.strip()]
    return origins or ["*"]


def _configured_keys() -> list[tuple[str, Principal]]:
    if not static_bootstrap_credentials_active():
        return []
    raw = os.getenv("DRONEAI_API_KEYS_JSON", "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("DRONEAI_API_KEYS_JSON must be valid JSON") from error
    if not isinstance(payload, list):
        raise RuntimeError("DRONEAI_API_KEYS_JSON must be a JSON array")
    result: list[tuple[str, Principal]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise RuntimeError(f"DRONEAI_API_KEYS_JSON[{index}] must be an object")
        key = str(item.get("key") or "")
        subject = str(item.get("subject") or "")
        role = str(item.get("role") or "")
        raw_organization = str(
            item.get("organization_id") or LEGACY_ORGANIZATION_ID
        )
        try:
            organization_id = validate_organization_id(raw_organization)
        except ValueError as error:
            raise RuntimeError(
                f"DRONEAI_API_KEYS_JSON[{index}] has an invalid organization_id"
            ) from error
        if len(key) < 32 or not subject or role not in ROLE_RANK:
            raise RuntimeError(f"DRONEAI_API_KEYS_JSON[{index}] has an invalid key, subject, or role")
        result.append(
            (
                key,
                Principal(
                    subject=subject,
                    role=role,
                    organization_id=organization_id,
                ),
            )
        )
    return result


def authentication_enabled() -> bool:
    explicit = os.getenv("DRONEAI_AUTH_DISABLED")
    if explicit is not None:
        disabled = explicit.strip().lower() in {"1", "true", "yes"}
        if disabled and is_production():
            raise RuntimeError("DRONEAI_AUTH_DISABLED cannot be enabled in production")
        return not disabled
    return (
        is_production()
        or bool(_configured_keys())
        or database_authentication_enabled(production=is_production())
    )


def validate_production_configuration() -> None:
    if not is_production():
        return
    bounded_stage_jobs_enabled()
    if "*" in configured_cors_origins():
        raise RuntimeError("CORS_ORIGINS must list trusted origins in production")
    if not database_authentication_enabled(production=True):
        raise RuntimeError(
            "DRONEAI_DATABASE_AUTH_ENABLED must be enabled in production"
        )
    if os.getenv("DRONEAI_RLS_REQUIRED", "").strip().lower() != "true":
        raise RuntimeError("DRONEAI_RLS_REQUIRED must be enabled in production")
    if (
        os.getenv(
            "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
            "",
        )
        .strip()
        .lower()
        != "true"
    ):
        raise RuntimeError(
            "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED must be enabled "
            "in production"
        )
    static_bootstrap_configured = bool(
        os.getenv("DRONEAI_API_KEYS_JSON", "").strip()
    )
    allow_static_bootstrap = static_bootstrap_allowed()
    if static_bootstrap_configured and not allow_static_bootstrap:
        raise RuntimeError(
            "Static bootstrap credentials are disabled in production; remove "
            "DRONEAI_API_KEYS_JSON or temporarily set "
            "DRONEAI_ALLOW_STATIC_BOOTSTRAP=true during adoption"
        )
    configured_keys = _configured_keys()
    if any(
        principal.organization_id == LEGACY_ORGANIZATION_ID
        for _key, principal in configured_keys
    ):
        raise RuntimeError(
            "Every production API key requires an explicit organization_id"
        )
    if len(os.getenv("DRONEAI_SESSION_SECRET", "")) < 32:
        raise RuntimeError("DRONEAI_SESSION_SECRET must contain at least 32 characters in production")
    credential_pepper()
    for name in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if not value or value in LOCAL_SECRET_VALUES:
            raise RuntimeError(f"{name} must be supplied by a production secret")


def _extract_token(
    authorization: str | None,
    api_key: str | None,
    session_cookie: str | None = None,
) -> str | None:
    if api_key:
        return api_key.strip()
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return session_cookie.strip() if session_cookie else None


def _encode_base64(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_base64(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session_token(
    principal: Principal,
    max_age_seconds: int,
) -> str:
    secret = os.getenv("DRONEAI_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("DRONEAI_SESSION_SECRET must contain at least 32 characters")
    payload = {
        "subject": principal.subject,
        "role": principal.role,
        "organization_id": principal.organization_id,
        "authentication_method": principal.authentication_method,
        "realm": principal.realm,
        "expires_at": int(time.time()) + max_age_seconds,
    }
    if principal.member_id is not None:
        payload.update(
            {
                "member_id": principal.member_id,
                "credential_id": principal.credential_id,
                "auth_version": principal.auth_version,
            }
        )
    encoded = _encode_base64(
        json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _encode_base64(
        hmac_new(
            secret.encode("utf-8"),
            encoded.encode("ascii"),
            sha256,
        ).digest()
    )
    return f"{encoded}.{signature}"


def _authenticate_session_token(token: str) -> Principal | None:
    secret = os.getenv("DRONEAI_SESSION_SECRET", "")
    if len(secret) < 32:
        return None
    encoded, separator, signature = token.partition(".")
    if not separator or not encoded or not signature:
        return None
    expected = _encode_base64(
        hmac_new(
            secret.encode("utf-8"),
            encoded.encode("ascii"),
            sha256,
        ).digest()
    )
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_decode_base64(encoded))
        expires_at = int(payload["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if expires_at <= int(time.time()):
        return None
    durable_fields = ("member_id", "credential_id", "auth_version")
    durable_values = [payload.get(field) for field in durable_fields]
    if any(value is not None for value in durable_values):
        if any(value is None for value in durable_values):
            return None
        try:
            member_id = str(payload["member_id"])
            credential_id = str(payload["credential_id"])
            auth_version = int(payload["auth_version"])
        except (TypeError, ValueError):
            return None
        realm = str(payload.get("realm", "tenant"))
        if realm == "tenant":
            with get_session(
                authentication_credential_id=credential_id
            ) as session:
                identity = validate_session_identity(
                    session,
                    member_id=member_id,
                    credential_id=credential_id,
                    auth_version=auth_version,
                )
            return (
                _principal_from_identity(identity)
                if identity is not None
                else None
            )
        if realm == "platform":
            with get_session(platform_credential_id=credential_id) as session:
                platform_identity = validate_platform_session_identity(
                    session,
                    member_id=member_id,
                    credential_id=credential_id,
                    auth_version=auth_version,
                )
            return (
                _principal_from_platform_identity(platform_identity)
                if platform_identity is not None
                else None
            )
        return None
    try:
        subject = str(payload["subject"])
        role = str(payload["role"])
        organization_id = validate_organization_id(str(payload["organization_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not subject or role not in ROLE_RANK:
        return None
    authentication_method = str(payload.get("authentication_method", "static"))
    principal = Principal(
        subject=subject,
        role=role,
        organization_id=organization_id,
        authentication_method="static-session",
        realm="tenant",
    )
    if authentication_method == "local":
        return principal if not authentication_enabled() and not is_production() else None
    if authentication_method not in {"static", "static-session"}:
        return None
    if any(
        configured.subject == principal.subject
        and configured.role == principal.role
        and configured.organization_id == principal.organization_id
        for _key, configured in _configured_keys()
    ):
        return principal
    return None


def _session_public_credential_identity(token: str) -> str | None:
    """Read a signed session's public credential ID without database work."""

    secret = os.getenv("DRONEAI_SESSION_SECRET", "")
    if len(secret) < 32:
        return None
    encoded, separator, signature = token.partition(".")
    if not separator or not encoded or not signature:
        return None
    expected = _encode_base64(
        hmac_new(
            secret.encode("utf-8"),
            encoded.encode("ascii"),
            sha256,
        ).digest()
    )
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_decode_base64(encoded))
        credential_id = str(payload["credential_id"])
        realm = str(payload.get("realm", "tenant"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if realm != "tenant" or not credential_id:
        return None
    return f"tenant:{credential_id}"


def authenticate_api_key(token: str | None) -> Principal | None:
    if not authentication_enabled():
        return Principal(
            subject="local-development",
            role="admin",
            organization_id=LOCAL_ORGANIZATION_ID,
            authentication_method="local",
        )
    if not token:
        return None
    for configured_key, principal in _configured_keys():
        if secrets.compare_digest(token, configured_key):
            return principal
    if database_authentication_enabled(production=is_production()):
        credential_id = credential_id_from_token(token)
        if credential_id is not None:
            with get_session(
                authentication_credential_id=credential_id
            ) as session:
                identity = authenticate_credential(session, token)
            if identity is not None:
                return _principal_from_identity(identity)
        platform_credential_id = platform_credential_id_from_token(token)
        if platform_credential_id is not None:
            with get_session(
                platform_credential_id=platform_credential_id,
            ) as session:
                platform_identity = authenticate_platform_credential(
                    session,
                    token,
                )
            if platform_identity is not None:
                return _principal_from_platform_identity(platform_identity)
    return None


def authenticate_token(token: str | None) -> Principal | None:
    principal = authenticate_api_key(token)
    if principal is not None or not token:
        return principal
    return _authenticate_session_token(token)


def authenticate_request(request: Request) -> Principal | None:
    """Resolve the authenticated rate-limit identity without trusting proxy headers."""
    return authenticate_token(
        _extract_token(
            request.headers.get("authorization"),
            request.headers.get("x-api-key"),
            request.cookies.get(SESSION_COOKIE_NAME),
        )
    )


def _require_request_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    droneai_api_key: str | None = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
) -> Principal:
    principal = authenticate_token(
        _extract_token(authorization, x_api_key, droneai_api_key)
    )
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_role(minimum_role: str) -> Callable[..., Principal]:
    if minimum_role not in ROLE_RANK:
        raise ValueError("unknown API role")

    def dependency(
        principal: Annotated[
            Principal,
            Depends(_require_request_principal),
        ],
    ) -> Principal:
        if principal.realm != "tenant":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant identity required",
            )
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{minimum_role} role required",
            )
        return principal

    return dependency


require_authenticated = require_role("viewer")
require_operator = require_role("operator")
require_admin = require_role("admin")


def require_platform_support(
    principal: Annotated[
        Principal,
        Depends(_require_request_principal),
    ],
) -> Principal:
    if principal.realm != "platform" or principal.role != "support":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform support identity required",
        )
    return principal


async def bind_tenant_context(
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> AsyncIterator[None]:
    """Bind the authenticated organization around all route database work."""

    token = bind_organization_id(principal.organization_id)
    try:
        yield
    finally:
        reset_organization_id(token)


def enforce_cookie_csrf(request: Request) -> None:
    """Require a trusted Origin for state changes authenticated by cookie."""

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if SESSION_COOKIE_NAME not in request.cookies:
        return
    origin = request.headers.get("origin", "").rstrip("/")
    trusted = {configured.rstrip("/") for configured in configured_cors_origins() if configured != "*"}
    # Development without a configured Origin remains usable; production
    # configuration validation already forbids the wildcard.
    if not is_production() and not trusted:
        return
    if (is_production() and not origin) or (origin and origin not in trusted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Untrusted request origin",
        )


async def authorize_websocket(
    websocket: WebSocket,
) -> WebSocketAuthorization | None:
    origin = websocket.headers.get("origin", "").rstrip("/")
    trusted = {
        configured.rstrip("/")
        for configured in configured_cors_origins()
        if configured != "*"
    }
    if (is_production() and (not origin or origin not in trusted)) or (
        origin and trusted and origin not in trusted
    ):
        await websocket.close(code=4403, reason="Untrusted request origin")
        return None

    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if token is None and not is_production():
        token = websocket.query_params.get("access_token")
    peer = websocket.client.host if websocket.client else "unknown"
    try:
        retry_after = await run_in_threadpool(
            identity_peer_rate_limiter.consume,
            f"identity:peer:{peer}",
        )
        if retry_after is None and token:
            credential_id = credential_id_from_token(token)
            public_identity = (
                f"tenant:{credential_id}"
                if credential_id is not None
                else _session_public_credential_identity(token)
            )
            if public_identity is not None:
                retry_after = await run_in_threadpool(
                    identity_credential_rate_limiter.consume,
                    f"identity:credential:{public_identity}",
                )
    except Exception:
        await websocket.close(code=1013, reason="Identity rate limiter unavailable")
        return None
    if retry_after is not None:
        await websocket.close(code=4429, reason="Identity rate limit exceeded")
        return None

    principal = await run_in_threadpool(authenticate_token, token)
    if principal is not None and principal.realm == "tenant" and token:
        return WebSocketAuthorization(principal=principal, token=token, peer=peer)
    await websocket.close(code=4401, reason="Authentication required")
    return None


def websocket_authorization_status(
    authorization: WebSocketAuthorization,
) -> str:
    """Revalidate durable identity state for a long-lived connection."""

    principal = authenticate_token(authorization.token)
    if principal is None:
        return "unauthenticated"
    expected = authorization.principal
    if (
        principal.realm,
        principal.organization_id,
        principal.subject,
        principal.role,
        principal.member_id,
        principal.credential_id,
        principal.auth_version,
    ) != (
        expected.realm,
        expected.organization_id,
        expected.subject,
        expected.role,
        expected.member_id,
        expected.credential_id,
        expected.auth_version,
    ):
        return "forbidden"
    return "valid"


def upload_limits() -> dict[str, int]:
    values = {
        "max_files": os.getenv("DRONEAI_UPLOAD_MAX_FILES", "2500"),
        "max_file_bytes": os.getenv(
            "DRONEAI_UPLOAD_MAX_FILE_BYTES",
            str(2 * 1024 * 1024 * 1024),
        ),
        "max_batch_bytes": os.getenv(
            "DRONEAI_UPLOAD_MAX_BATCH_BYTES",
            str(50 * 1024 * 1024 * 1024),
        ),
    }
    try:
        parsed = {name: int(value) for name, value in values.items()}
    except (TypeError, ValueError) as error:
        raise RuntimeError("upload limits must be integers") from error
    if any(value <= 0 for value in parsed.values()):
        raise RuntimeError("upload limits must be positive")
    return parsed
