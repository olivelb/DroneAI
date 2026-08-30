import importlib
import json

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from shared.tenancy import current_organization_id

security = importlib.import_module("app4-dashboard.api.security")
dataset_uploads = importlib.import_module("app4-dashboard.api.dataset_uploads")
api_main = importlib.import_module("app4-dashboard.api.main")
rate_limit = importlib.import_module("app4-dashboard.api.rate_limit")
http_middleware = importlib.import_module("app4-dashboard.api.http_middleware")
auth_routes = importlib.import_module("app4-dashboard.api.routers.auth")


def _keys():
    return json.dumps(
        [
            {
                "key": "viewer-secret-key-with-at-least-32-bytes",
                "subject": "quality",
                "role": "viewer",
                "organization_id": "acme-survey",
            },
            {
                "key": "admin-secret-key-with-at-least-32-bytes!",
                "subject": "operations",
                "role": "admin",
                "organization_id": "acme-survey",
            },
        ]
    )


def _enable_production_rls(monkeypatch):
    monkeypatch.setenv("DRONEAI_RLS_REQUIRED", "true")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP", "true")


def test_api_key_rbac_accepts_admin_and_rejects_viewer_for_writes(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())

    admin_principal = security.authenticate_token("admin-secret-key-with-at-least-32-bytes!")
    assert admin_principal is not None
    admin = security.require_admin(admin_principal)
    assert admin.subject == "operations"
    assert admin.organization_id == "acme-survey"
    with pytest.raises(HTTPException) as error:
        viewer = security.authenticate_token("viewer-secret-key-with-at-least-32-bytes")
        assert viewer is not None
        security.require_operator(viewer)
    assert error.value.status_code == 403


def test_authenticated_router_binds_and_resets_tenant_context(monkeypatch):
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    application = FastAPI()

    @application.get(
        "/tenant-context",
        dependencies=[Depends(security.bind_tenant_context)],
    )
    def tenant_context():
        return {"organization_id": current_organization_id()}

    response = TestClient(application).get(
        "/tenant-context",
        headers={"X-API-Key": "viewer-secret-key-with-at-least-32-bytes"},
    )

    assert response.json() == {"organization_id": "acme-survey"}
    assert current_organization_id() is None


def test_http_only_session_authenticates_http_and_can_be_cleared(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP", "true")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("DRONEAI_SESSION_MAX_AGE_SECONDS", "3600")
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )
    monkeypatch.setenv(
        "DRONEAI_CREDENTIAL_PEPPER",
        "credential-pepper-with-at-least-32-bytes",
    )

    client = TestClient(api_main.app)
    response = client.post(
        "/auth/session",
        json={"api_key": "admin-secret-key-with-at-least-32-bytes!"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["organization_id"] == "acme-survey"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "admin-secret-key" not in response.text
    session_token = response.cookies[security.SESSION_COOKIE_NAME]
    assert "admin-secret-key" not in session_token

    # TestClient does not send Secure cookies over its default HTTP origin.
    principal = security.authenticate_token(session_token)
    assert principal is not None
    assert principal.subject == "operations"
    assert principal.organization_id == "acme-survey"
    assert security.authenticate_token(f"{session_token}tampered") is None
    monkeypatch.delenv("DRONEAI_API_KEYS_JSON")
    assert security.authenticate_token(session_token) is None

    response = client.delete("/auth/session")
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_production_configuration_rejects_wildcard_and_local_secrets(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    _enable_production_rls(monkeypatch)
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )
    monkeypatch.setenv(
        "DRONEAI_CREDENTIAL_PEPPER",
        "credential-pepper-with-at-least-32-bytes",
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
    _enable_production_rls(monkeypatch)
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.delenv("DRONEAI_SESSION_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="DRONEAI_SESSION_SECRET"):
        security.validate_production_configuration()


def test_production_configuration_requires_rls(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.delenv("DRONEAI_RLS_REQUIRED", raising=False)

    with pytest.raises(RuntimeError, match="DRONEAI_RLS_REQUIRED"):
        security.validate_production_configuration()


def test_production_configuration_requires_organization_request_quotas(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_RLS_REQUIRED", "true")
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.delenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        raising=False,
    )

    with pytest.raises(
        RuntimeError,
        match="DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
    ):
        security.validate_production_configuration()


def test_production_configuration_rejects_fused_compute(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    with pytest.raises(RuntimeError, match="require bounded stage Jobs"):
        security.validate_production_configuration()




def test_production_configuration_requires_a_credential_pepper(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    _enable_production_rls(monkeypatch)
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.setenv(
        "DRONEAI_SESSION_SECRET",
        "session-signing-secret-with-at-least-32-bytes",
    )
    monkeypatch.delenv("DRONEAI_CREDENTIAL_PEPPER", raising=False)

    with pytest.raises(RuntimeError, match="DRONEAI_CREDENTIAL_PEPPER"):
        security.validate_production_configuration()


def test_production_configuration_requires_explicit_organizations(monkeypatch):
    legacy_keys = json.loads(_keys())
    for item in legacy_keys:
        item.pop("organization_id")
    monkeypatch.setenv("DRONEAI_ENV", "production")
    _enable_production_rls(monkeypatch)
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", json.dumps(legacy_keys))
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")

    with pytest.raises(RuntimeError, match="organization_id"):
        security.validate_production_configuration()


def test_production_rejects_static_bootstrap_without_explicit_adoption_flag(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_RLS_REQUIRED", "true")
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setenv("DRONEAI_DATABASE_AUTH_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_API_KEYS_JSON", _keys())
    monkeypatch.setenv("CORS_ORIGINS", "https://droneai.example.com")
    monkeypatch.delenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP", raising=False)

    assert security.authenticate_api_key("admin-secret-key-with-at-least-32-bytes!") is None
    with pytest.raises(RuntimeError, match="Static bootstrap credentials"):
        security.validate_production_configuration()


def test_static_bootstrap_flag_must_be_boolean(monkeypatch):
    monkeypatch.setenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP", "eventually")

    with pytest.raises(RuntimeError, match="must be true or false"):
        security.static_bootstrap_allowed()


def test_cookie_authenticated_mutation_requires_trusted_origin(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_AUTH_DISABLED", "false")
    monkeypatch.setenv("DRONEAI_ALLOW_STATIC_BOOTSTRAP", "true")
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


def test_session_key_ring_rotates_without_invalidating_previous_tokens(monkeypatch):
    monkeypatch.setenv("DRONEAI_SESSION_SECRET", "legacy-session-secret-" + "x" * 32)
    legacy_token = security.issue_session_token(
        security.Principal("operations", "admin"),
        3600,
    )
    legacy_without_kid = ".".join(legacy_token.split(".")[1:])
    monkeypatch.setenv(
        "DRONEAI_SESSION_SIGNING_KEYS_JSON",
        json.dumps(
            {
                "current": "v2",
                "keys": {
                    "v2": "current-session-secret-" + "c" * 32,
                    "v1": "legacy-session-secret-" + "x" * 32,
                },
            }
        ),
    )

    new_token = security.issue_session_token(
        security.Principal("operations", "admin"),
        3600,
    )

    assert new_token.startswith("v2.")
    assert security._verified_session_payload(new_token) is not None
    assert security._verified_session_payload(legacy_without_kid) is not None

    monkeypatch.setenv(
        "DRONEAI_SESSION_SIGNING_KEYS_JSON",
        json.dumps(
            {
                "current": "v2",
                "keys": {"v2": "current-session-secret-" + "c" * 32},
            }
        ),
    )
    assert security._verified_session_payload(legacy_without_kid) is None


def test_session_key_ring_rejects_invalid_current_key(monkeypatch):
    monkeypatch.setenv(
        "DRONEAI_SESSION_SIGNING_KEYS_JSON",
        json.dumps({"current": "missing", "keys": {"v1": "s" * 32}}),
    )

    with pytest.raises(RuntimeError, match="current kid is missing"):
        security.session_signing_keys()


def test_http_body_limit_rejects_before_route_parsing() -> None:
    application = FastAPI()
    application.add_middleware(
        http_middleware.RequestBodyLimitMiddleware,
        max_body_bytes=4096,
    )
    called: list[bool] = []

    @application.post("/bounded")
    async def bounded(_request: Request):
        called.append(True)
        return {"status": "ok"}

    response = TestClient(application).post("/bounded", content=b"x" * 4097)

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert called == []


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


def test_production_requires_shared_identity_rate_limiting(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv("DRONEAI_IDENTITY_RATE_LIMIT_BACKEND", "local")

    with pytest.raises(RuntimeError, match="database-backed identity"):
        security.build_identity_rate_limiter(scope="peer")

    monkeypatch.setenv("DRONEAI_IDENTITY_RATE_LIMIT_BACKEND", "auto")
    limiter = security.build_identity_rate_limiter(scope="credential")

    assert isinstance(limiter, security.DatabaseTokenBucketRateLimiter)


def test_identity_rate_limiter_uses_public_uuid_without_retaining_secret():
    credential_id = "7af8698a-c9d7-49b2-b1a9-bf42d754ad43"
    secret = "identity-secret-that-must-not-be-persisted"

    identity = rate_limit._public_credential_identity(f"dai.{credential_id}.{secret}")

    assert identity == f"tenant:{credential_id}"
    assert secret not in identity


def test_identity_middleware_limits_targeted_public_credential(monkeypatch):
    now = lambda: 100.0
    monkeypatch.setattr(
        security,
        "identity_peer_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=100,
            burst=10,
            clock=now,
        ),
    )
    monkeypatch.setattr(
        security,
        "identity_credential_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=1,
            burst=1,
            clock=now,
        ),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.IdentityRateLimitMiddleware)
    calls = []

    @application.get("/auth/credentials")
    def credentials():
        calls.append("called")
        return []

    token = "dai.7af8698a-c9d7-49b2-b1a9-bf42d754ad43.identity-secret-that-is-at-least-32-bytes"
    client = TestClient(application)

    assert (
        client.get(
            "/auth/credentials",
            headers={"X-API-Key": token},
        ).status_code
        == 200
    )
    limited = client.get(
        "/auth/credentials",
        headers={"X-API-Key": token},
    )

    assert limited.status_code == 429
    assert limited.headers["X-RateLimit-Scope"] == "identity"
    assert limited.headers["X-RateLimit-Limit"] == "1"
    assert calls == ["called"]


def test_identity_middleware_limits_token_bearing_non_identity_routes(monkeypatch):
    now = lambda: 100.0
    monkeypatch.setattr(
        security,
        "identity_peer_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=100,
            burst=10,
            clock=now,
        ),
    )
    monkeypatch.setattr(
        security,
        "identity_credential_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=1,
            burst=1,
            clock=now,
        ),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.IdentityRateLimitMiddleware)
    calls: list[str] = []

    @application.get("/maps/mission-1")
    def mission_map():
        calls.append("map")
        return {"status": "ok"}

    @application.get("/ready")
    def ready():
        calls.append("ready")
        return {"status": "ok"}

    token = "dai.7af8698a-c9d7-49b2-b1a9-bf42d754ad43.identity-secret-that-is-at-least-32-bytes"
    client = TestClient(application)

    assert client.get("/ready", headers={"X-API-Key": token}).status_code == 200
    assert client.get("/ready", headers={"X-API-Key": token}).status_code == 200
    assert (
        client.get(
            "/maps/mission-1",
            headers={"X-API-Key": token},
        ).status_code
        == 200
    )
    limited = client.get(
        "/maps/mission-1",
        headers={"X-API-Key": token},
    )

    assert limited.status_code == 429
    assert limited.headers["X-RateLimit-Scope"] == "identity"
    assert calls == ["ready", "ready", "map"]


def test_identity_peer_bucket_limits_rotating_public_identifiers(monkeypatch):
    now = lambda: 100.0
    monkeypatch.setattr(
        security,
        "identity_peer_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=2,
            burst=2,
            clock=now,
        ),
    )
    monkeypatch.setattr(
        security,
        "identity_credential_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=100,
            burst=100,
            clock=now,
        ),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.IdentityRateLimitMiddleware)

    @application.get("/auth/members")
    def members():
        return []

    client = TestClient(application)
    credential_ids = (
        "7af8698a-c9d7-49b2-b1a9-bf42d754ad43",
        "a0f544dd-3170-4f72-ab94-b39caeb8e144",
        "11161978-b152-4575-a1b5-d6b059d1df61",
    )
    statuses = [
        client.get(
            "/auth/members",
            headers={"X-API-Key": f"dai.{credential_id}.{'s' * 32}"},
        ).status_code
        for credential_id in credential_ids
    ]

    assert statuses == [200, 200, 429]


def test_session_json_credential_is_limited_before_authentication(monkeypatch):
    now = lambda: 100.0
    monkeypatch.setattr(
        security,
        "identity_peer_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=100,
            burst=10,
            clock=now,
        ),
    )
    monkeypatch.setattr(
        security,
        "identity_credential_rate_limiter",
        security.TokenBucketRateLimiter(
            requests_per_minute=1,
            burst=1,
            clock=now,
        ),
    )
    authentication_calls = []
    monkeypatch.setattr(
        security,
        "authenticate_api_key",
        lambda token: authentication_calls.append(token),
    )
    application = FastAPI()
    application.include_router(auth_routes.router)
    token = "dai.7af8698a-c9d7-49b2-b1a9-bf42d754ad43.identity-secret-that-is-at-least-32-bytes"
    client = TestClient(application)

    first = client.post("/auth/session", json={"api_key": token})
    second = client.post("/auth/session", json={"api_key": token})

    assert first.status_code == 401
    assert second.status_code == 429
    assert authentication_calls == [token]


def test_identity_middleware_fails_closed_when_backend_is_unavailable(
    monkeypatch,
):
    class UnavailableLimiter:
        requests_per_minute = 1

        @staticmethod
        def consume(_key):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        security,
        "identity_peer_rate_limiter",
        UnavailableLimiter(),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.IdentityRateLimitMiddleware)

    @application.get("/auth/members")
    def members():
        return []

    response = TestClient(application).get("/auth/members")

    assert response.status_code == 503
    assert response.json() == {"detail": "Identity rate limiter unavailable"}


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

    assert response.json() == {"key": "organization:legacy-unassigned:subject:operator-1"}


def test_organization_request_quota_middleware_is_tenant_wide(monkeypatch):
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        security,
        "authenticate_request",
        lambda _request: security.Principal(
            "operator-1",
            "operator",
            "tenant-a",
        ),
    )
    observed = []

    def deny(organization_id, actor_subject):
        observed.append((organization_id, actor_subject))
        return rate_limit.RequestQuotaDecision(60, 1.2)

    monkeypatch.setattr(rate_limit, "_consume_organization_request", deny)
    application = FastAPI()
    application.add_middleware(rate_limit.OrganizationRequestQuotaMiddleware)

    @application.get("/missions")
    def missions():
        return []

    response = TestClient(application).get("/missions")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "2"
    assert response.headers["X-RateLimit-Scope"] == "organization"
    assert observed == [("tenant-a", "operator-1")]


def test_request_authentication_is_reused_by_middleware_and_dependency(
    monkeypatch,
):
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    authentication_calls = []

    def authenticate(token):
        authentication_calls.append(token)
        return security.Principal("operator-1", "operator", "tenant-a")

    monkeypatch.setattr(security, "authenticate_token", authenticate)
    monkeypatch.setattr(
        rate_limit,
        "_consume_organization_request",
        lambda _organization_id, _actor_subject: rate_limit.RequestQuotaDecision(
            60,
            None,
        ),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.OrganizationRequestQuotaMiddleware)

    @application.get("/missions")
    def missions(
        _principal=Depends(security.require_authenticated),
    ):
        return []

    response = TestClient(application).get(
        "/missions",
        headers={"X-API-Key": "public-request-token"},
    )

    assert response.status_code == 200
    assert authentication_calls == ["public-request-token"]


def test_organization_request_quota_does_not_treat_platform_as_tenant(
    monkeypatch,
):
    monkeypatch.setenv(
        "DRONEAI_ORGANIZATION_REQUEST_QUOTAS_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        security,
        "authenticate_request",
        lambda _request: security.Principal(
            "support-1",
            "support",
            "platform-control",
            realm="platform",
        ),
    )
    monkeypatch.setattr(
        rate_limit,
        "_consume_organization_request",
        lambda *_args: pytest.fail("platform support has no tenant quota"),
    )
    application = FastAPI()
    application.add_middleware(rate_limit.OrganizationRequestQuotaMiddleware)

    @application.get("/platform/organizations")
    def organizations():
        return []

    response = TestClient(application).get("/platform/organizations")

    assert response.status_code == 200
