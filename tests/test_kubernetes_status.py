from __future__ import annotations

import importlib


kubernetes_status = importlib.import_module(
    "app4-dashboard.api.kubernetes_status"
)


def test_compose_runtime_reports_local_services_as_ready(monkeypatch):
    monkeypatch.setenv("DRONEAI_RUNTIME_MODE", "compose")

    payload = kubernetes_status.get_pod_states()

    assert payload["available"] is True
    assert payload["error"] is None
    assert {pod["name"] for pod in payload["pods"]} == set(
        kubernetes_status.POD_NAMES
    )
    assert all(pod["phase"] == "running" for pod in payload["pods"])
    assert all(pod["ready"] == "1/1" for pod in payload["pods"])


def test_missing_kubernetes_credentials_keep_fallback(monkeypatch):
    monkeypatch.delenv("DRONEAI_RUNTIME_MODE", raising=False)

    payload = kubernetes_status.get_pod_states()

    assert payload["available"] is False
    assert payload["error"] == "service account credentials unavailable"
    assert all(pod["phase"] == "unknown" for pod in payload["pods"])
