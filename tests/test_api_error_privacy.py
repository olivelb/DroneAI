import importlib
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

middleware = importlib.import_module("app4-dashboard.api.http_middleware")


@pytest.mark.parametrize("code", [500, 502, 503])
def test_internal_http_details_stay_in_correlated_logs(code, caplog):
    app = middleware.DashboardApplication()
    middleware.configure_http_middleware(app)

    @app.get("/failure")
    def fail():
        raise HTTPException(code, "private-s3-endpoint and internal exception", headers={"Retry-After": "3"})

    with caplog.at_level(logging.ERROR, logger="droneai.http.errors"):
        response = TestClient(app, raise_server_exceptions=False).get(
            "/failure", headers={"X-Request-ID": "privacy-test-123"},
        )
    assert response.status_code == code
    assert response.json() == {"detail": "Unable to process request", "request_id": "privacy-test-123"}
    assert response.headers["X-Request-ID"] == "privacy-test-123"
    assert response.headers["Retry-After"] == "3"
    assert "private-s3-endpoint" not in response.text
    assert "private-s3-endpoint" in caplog.text
    assert "privacy-test-123" in caplog.text


def test_unhandled_exception_has_generic_json_and_request_id(caplog):
    app = middleware.DashboardApplication()
    middleware.configure_http_middleware(app)

    @app.get("/failure")
    def fail():
        raise RuntimeError("private-database-password")

    with caplog.at_level(logging.ERROR, logger="droneai.http.errors"):
        response = TestClient(app, raise_server_exceptions=False).get("/failure")
    assert response.status_code == 500
    assert response.json()["detail"] == "Unable to process request"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "private-database-password" not in response.text
    assert "private-database-password" in caplog.text


def test_actionable_client_errors_are_preserved():
    app = middleware.DashboardApplication()
    middleware.configure_http_middleware(app)

    @app.get("/invalid")
    def fail():
        raise HTTPException(409, "Mission changed generation")

    response = TestClient(app).get("/invalid")
    assert response.status_code == 409
    assert response.json() == {"detail": "Mission changed generation"}
