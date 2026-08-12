import importlib
import json

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

security = importlib.import_module("app4-dashboard.api.security")
dataset_uploads = importlib.import_module("app4-dashboard.api.dataset_uploads")
api_main = importlib.import_module("app4-dashboard.api.main")
rate_limit = importlib.import_module("app4-dashboard.api.rate_limit")


def _keys():
    return json.dumps(
        [
            {
                "key": "viewer-secret-key-with-at-least-32-bytes",
                "subject": "quality",
                "role": "viewer",
            },
            {
                "key": "admin-secret-key-with-at-least-32-bytes!",
                "subject": "operations",
                "role": "admin",
            },
        ]
    )


def test_api_key_rbac_accepts_admin_and_rejects_viewer_for_writes(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())

    admin = security.require_admin(
        authorization="Bearer admin-secret-key-with-at-least-32-bytes!",
        x_api_key=None,
    )
    assert admin.subject == "operations"
    with pytest.raises(HTTPException) as error:
        security.require_operator(
            authorization=None,
            x_api_key="viewer-secret-key-with-at-least-32-bytes",
        )
    assert error.value.status_code == 403


def test_http_only_session_authenticates_http_and_can_be_cleared(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("DRONEAI_SESSION_MAX_AGE_SECONDS", "3600")
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/auth/session",
        json={"api_key": "admin-secret-key-with-at-least-32-bytes!"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "admin-secret-key" not in response.text
    session_token = response.cookies[security.SESSION_COOKIE_NAME]
    assert "admin-secret-key" not in session_token

    # TestClient does not send Secure cookies over its default HTTP origin.
    principal = security.require_authenticated(
        authorization=None,
        x_api_key=None,
        droneai_api_key=session_token,
    )
    assert principal.subject == "operations"
    assert security.authenticate_token(f"{session_token}tampered") is None

    response = client.delete("/auth/session")
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_production_configuration_rejects_wildcard_and_local_secrets(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        security.validate_production_configuration()

    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.setenv("S3_ACCESS_KEY", "production-access")
    monkeypatch.setenv("S3_SECRET_KEY", "minioadmin")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://production.example.com/droneai",
    )
    with pytest.raises(RuntimeError, match="S3_SECRET_KEY"):
        security.validate_production_configuration()


def test_production_configuration_requires_a_session_secret(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.delenv("DRONEAI_SESSION_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="DRONEAI_SESSION_SECRET"):
        security.validate_production_configuration()


def test_cookie_authenticated_mutation_requires_trusted_origin(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    token = security.issue_session_token(
        security.Principal("operations", "admin"),
        3600,
    )
    client = TestClient(api_main.app)
    client.cookies.set(security.SESSION_COOKIE_NAME, token)

    response = client.post("/mission/cancel?vol_id=mission-1")

    assert response.status_code == 403
    assert response.json()["detail"] == "Untrusted request origin"


def test_dataset_upload_quota_and_extension_are_enforced(monkeypatch):
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_FILES", "2")
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_FILE_BYTES", "4")
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_BATCH_BYTES", "6")

    dataset_uploads._validate_request(
        dataset_uploads.UploadSessionRequest(
            dataset_name="valid",
            files=[{"name": "DJI_0001.JPG", "size": 4}],
        )
    )
    with pytest.raises(HTTPException) as extension_error:
        dataset_uploads._validate_request(
            dataset_uploads.UploadSessionRequest(
                dataset_name="invalid-extension",
                files=[{"name": "payload.exe", "size": 1}],
            )
        )
    assert extension_error.value.status_code == 415
    with pytest.raises(HTTPException) as quota_error:
        dataset_uploads._validate_request(
            dataset_uploads.UploadSessionRequest(
                dataset_name="over-quota",
                files=[
                    {"name": "one.jpg", "size": 4},
                    {"name": "two.jpg", "size": 4},
                ],
            )
        )
    assert quota_error.value.status_code == 413


def test_tile_rate_limiter_refills_deterministically():
    now = [100.0]
    limiter = security.TokenBucketRateLimiter(
        requests_per_minute=2,
        burst=2,
        clock=lambda: now[0],
    )

    assert limiter.consume("client") is None
    assert limiter.consume("client") is None
    assert limiter.consume("client") == pytest.approx(30.0)

    now[0] += 30.0
    assert limiter.consume("client") is None


def test_tile_rate_limiter_bounds_tracked_clients():
    limiter = security.TokenBucketRateLimiter(
        requests_per_minute=1,
        burst=1,
        max_keys=2,
        clock=lambda: 100.0,
    )

    assert limiter.consume("oldest") is None
    assert limiter.consume("newer") is None
    assert limiter.consume("third") is None

    # "oldest" was evicted, so it receives a fresh bucket. Without eviction,
    # the second request at the same instant would still be rate limited.
    assert limiter.consume("oldest") is None
    assert len(limiter._buckets) == 2


def test_production_rejects_process_local_tile_rate_limiting(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_TILE_RATE_LIMIT_BACKEND", "local")

    with pytest.raises(RuntimeError, match="database-backed"):
        security.build_tile_rate_limiter()


def test_production_auto_selects_database_tile_rate_limiting(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "staging")
    monkeypatch.setenv("DRONEAI_TILE_RATE_LIMIT_BACKEND", "auto")

    limiter = security.build_tile_rate_limiter()

    assert isinstance(limiter, security.DatabaseTokenBucketRateLimiter)


def test_raster_tile_middleware_returns_retry_after(monkeypatch):
    limiter = security.TokenBucketRateLimiter(
        requests_per_minute=1,
        burst=1,
        clock=lambda: 100.0,
    )
    monkeypatch.setattr(security, "tile_rate_limiter", limiter)
    application = FastAPI()
    application.add_middleware(rate_limit.RasterTileRateLimitMiddleware)

    @application.get("/maps/{mission}/tiles/{layer}/{z}/{x}/{y}.png")
    def tile():
        return {"status": "ok"}

    client = TestClient(application)
    first = client.get(
        "/maps/mission-1/tiles/ortho/1/2/3.png",
        headers={"X-Forwarded-For": "198.51.100.10"},
    )
    second = client.get(
        "/maps/mission-1/tiles/ortho/1/2/3.png",
        headers={"X-Forwarded-For": "203.0.113.20"},
    )

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "60"


def test_rate_limit_identity_uses_authenticated_subject(monkeypatch):
    monkeypatch.setattr(
        security,
        "authenticate_request",
        lambda _request: security.Principal("operator-1", "operator"),
    )
    application = FastAPI()

    @application.get("/identity")
    def identity(request: Request):
        return {"key": rate_limit.rate_limit_identity(request)}

    client = TestClient(application)
    response = client.get(
        "/identity",
        headers={"X-Forwarded-For": "203.0.113.20"},
    )

    assert response.json() == {"key": "subject:operator-1"}
