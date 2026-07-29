"""Deployment security policy and API-key RBAC for the dashboard API."""

from __future__ import annotations

import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from hmac import new as hmac_new
from typing import Any

from fastapi import Cookie, Header, HTTPException, Request, WebSocket, status

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


def deployment_environment() -> str:
    return os.getenv("DRONEAI_ENV", "development").strip().lower()


def is_production() -> bool:
    return deployment_environment() in {"production", "staging"}


def configured_cors_origins() -> list[str]:
    origins = [
        value.strip()
        for value in os.getenv("CORS_ORIGINS", "*").split(",")
        if value.strip()
    ]
    return origins or ["*"]


def _configured_keys() -> list[tuple[str, Principal]]:
    raw = os.getenv("DRONEAI_API_KEYS_JSON", "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "DRONEAI_API_KEYS_JSON must be valid JSON"
        ) from error
    if not isinstance(payload, list):
        raise RuntimeError("DRONEAI_API_KEYS_JSON must be a JSON array")
    result = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"DRONEAI_API_KEYS_JSON[{index}] must be an object"
            )
        key = str(item.get("key") or "")
        subject = str(item.get("subject") or "")
        role = str(item.get("role") or "")
        if len(key) < 32 or not subject or role not in ROLE_RANK:
            raise RuntimeError(
                f"DRONEAI_API_KEYS_JSON[{index}] has an invalid key, "
                "subject, or role"
            )
        result.append((key, Principal(subject=subject, role=role)))
    return result


def authentication_enabled() -> bool:
    explicit = os.getenv("DRONEAI_AUTH_DISABLED")
    if explicit is not None:
        disabled = explicit.strip().lower() in {"1", "true", "yes"}
        if disabled and is_production():
            raise RuntimeError(
                "DRONEAI_AUTH_DISABLED cannot be enabled in production"
            )
        return not disabled
    return is_production() or bool(_configured_keys())


def validate_production_configuration() -> None:
    if not is_production():
        return
    if "*" in configured_cors_origins():
        raise RuntimeError(
            "CORS_ORIGINS must list trusted origins in production"
        )
    if not _configured_keys():
        raise RuntimeError(
            "DRONEAI_API_KEYS_JSON is required in production"
        )
    if len(os.getenv("DRONEAI_SESSION_SECRET", "")) < 32:
        raise RuntimeError(
            "DRONEAI_SESSION_SECRET must contain at least 32 characters "
            "in production"
        )
    for name in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if not value or value in LOCAL_SECRET_VALUES:
            raise RuntimeError(
                f"{name} must be supplied by a production secret"
            )


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
        raise RuntimeError(
            "DRONEAI_SESSION_SECRET must contain at least 32 characters"
        )
    payload = {
        "subject": principal.subject,
        "role": principal.role,
        "expires_at": int(time.time()) + max_age_seconds,
    }
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
        subject = str(payload["subject"])
        role = str(payload["role"])
        expires_at = int(payload["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not subject or role not in ROLE_RANK or expires_at <= int(time.time()):
        return None
    return Principal(subject=subject, role=role)


def authenticate_api_key(token: str | None) -> Principal | None:
    if not authentication_enabled():
        return Principal(subject="local-development", role="admin")
    if not token:
        return None
    for configured_key, principal in _configured_keys():
        if secrets.compare_digest(token, configured_key):
            return principal
    return None


def authenticate_token(token: str | None) -> Principal | None:
    principal = authenticate_api_key(token)
    if principal is not None or not token:
        return principal
    return _authenticate_session_token(token)


def require_role(minimum_role: str):
    if minimum_role not in ROLE_RANK:
        raise ValueError("unknown API role")

    def dependency(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        droneai_api_key: str | None = Cookie(
            default=None,
            alias=SESSION_COOKIE_NAME,
        ),
    ) -> Principal:
        principal = authenticate_token(
            _extract_token(
                authorization,
                x_api_key,
                droneai_api_key,
            )
        )
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API credential",
                headers={"WWW-Authenticate": "Bearer"},
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


def enforce_cookie_csrf(request: Request) -> None:
    """Require a trusted Origin for state changes authenticated by cookie."""

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if SESSION_COOKIE_NAME not in request.cookies:
        return
    origin = request.headers.get("origin", "").rstrip("/")
    trusted = {
        configured.rstrip("/")
        for configured in configured_cors_origins()
        if configured != "*"
    }
    # Development without a configured Origin remains usable; production
    # configuration validation already forbids the wildcard.
    if not is_production() and not trusted:
        return
    if (is_production() and not origin) or (origin and origin not in trusted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Untrusted request origin",
        )


async def authorize_websocket(websocket: WebSocket) -> bool:
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if token is None and not is_production():
        token = websocket.query_params.get("access_token")
    if authenticate_token(token) is not None:
        return True
    await websocket.close(code=4401, reason="Authentication required")
    return False


def upload_limits() -> dict[str, int]:
    values: dict[str, Any] = {
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
