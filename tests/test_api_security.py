import importlib
import io
import json

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient


security = importlib.import_module("app4-dashboard.api.security")
datasets = importlib.import_module("app4-dashboard.api.routers.datasets")
api_main = importlib.import_module("app4-dashboard.api.main")


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


def test_dataset_upload_quota_and_extension_are_enforced(monkeypatch):
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_FILES", "2")
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_FILE_BYTES", "4")
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_BATCH_BYTES", "6")

    datasets.validate_uploads(
        [
            UploadFile(
                file=io.BytesIO(b"1234"),
                filename="DJI_0001.JPG",
                size=4,
            )
        ]
    )
    with pytest.raises(HTTPException) as extension_error:
        datasets.validate_uploads(
            [
                UploadFile(
                    file=io.BytesIO(b"x"),
                    filename="payload.exe",
                    size=1,
                )
            ]
        )
    assert extension_error.value.status_code == 415
    with pytest.raises(HTTPException) as quota_error:
        datasets.validate_uploads(
            [
                UploadFile(
                    file=io.BytesIO(b"1234"),
                    filename="one.jpg",
                    size=4,
                ),
                UploadFile(
                    file=io.BytesIO(b"1234"),
                    filename="two.jpg",
                    size=4,
                ),
            ]
        )
    assert quota_error.value.status_code == 413
