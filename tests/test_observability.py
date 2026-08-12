import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Request, Response

from prometheus_client import generate_latest

from shared import observability


def _metric_value(metric, *labels: str) -> float:
    return float(metric.labels(*labels)._value.get())


def test_metrics_configuration_is_strict(monkeypatch):
    monkeypatch.setenv("DRONEAI_METRICS_ENABLED", "invalid")
    with pytest.raises(RuntimeError, match="must be true or false"):
        observability.metrics_enabled()

    monkeypatch.setenv("DRONEAI_METRICS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_METRICS_PORT", "80")
    with pytest.raises(RuntimeError, match="between 1024 and 65535"):
        observability.metrics_port()


def test_http_middleware_uses_route_templates_and_propagates_request_id():
    from importlib import import_module

    api_observability = import_module("app4-dashboard.api.observability")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/missions/customer-controlled-value",
        "raw_path": b"/missions/customer-controlled-value",
        "query_string": b"",
        "headers": [(b"x-request-id", b"request-42")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    request = Request(scope)
    before = _metric_value(
        observability.HTTP_REQUESTS,
        "GET",
        "/missions/{vol_id}",
        "204",
    )

    async def call_next(_request):
        scope["route"] = SimpleNamespace(path="/missions/{vol_id}")
        return Response(status_code=204)

    response = asyncio.run(
        api_observability.operational_metrics_middleware(request, call_next)
    )

    assert response.headers["X-Request-ID"] == "request-42"
    assert (
        _metric_value(
            observability.HTTP_REQUESTS,
            "GET",
            "/missions/{vol_id}",
            "204",
        )
        == before + 1
    )
    exposition = generate_latest().decode("utf-8")
    assert "customer-controlled-value" not in exposition


def test_http_middleware_bounds_client_controlled_methods():
    from importlib import import_module

    api_observability = import_module("app4-dashboard.api.observability")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "CUSTOM-CUSTOMER-METHOD",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    request = Request(scope)
    before = _metric_value(
        observability.HTTP_REQUESTS,
        "OTHER",
        "/health",
        "200",
    )

    async def call_next(_request):
        scope["route"] = SimpleNamespace(path="/health")
        return Response(status_code=200)

    asyncio.run(api_observability.operational_metrics_middleware(request, call_next))

    assert (
        _metric_value(
            observability.HTTP_REQUESTS,
            "OTHER",
            "/health",
            "200",
        )
        == before + 1
    )


def test_operational_queue_metrics_are_aggregate_and_non_scientific():
    observability.observe_outbox_queue(
        {"pending": 3, "dead": 1},
        oldest_unpublished_age_seconds=45.5,
    )
    observability.observe_stage_queue(
        {("queued", "gpu-standard"): (2, 4)},
        oldest_queued_age_seconds=12,
    )

    assert _metric_value(observability.OUTBOX_EVENTS, "pending") == 3
    assert observability.OUTBOX_OLDEST_UNPUBLISHED_AGE._value.get() == 45.5
    assert (
        _metric_value(
            observability.STAGE_RESOURCE_UNITS,
            "queued",
            "gpu-standard",
        )
        == 4
    )
    exposition = generate_latest().decode("utf-8").lower()
    assert "sharpness" not in exposition
    assert "accuracy" not in exposition


def test_s3_client_proxy_records_failures_without_swallowing_them():
    from shared.storage import _ObservedS3Client

    class FailingClient:
        @staticmethod
        def put_object(**_kwargs):
            raise TimeoutError("storage unavailable")

    before = _metric_value(
        observability.S3_OPERATION_FAILURES,
        "put_object",
        "TimeoutError",
    )
    client = _ObservedS3Client(FailingClient())

    with pytest.raises(TimeoutError, match="storage unavailable"):
        client.put_object(Bucket="bucket", Key="key", Body=b"value")

    assert (
        _metric_value(
            observability.S3_OPERATION_FAILURES,
            "put_object",
            "TimeoutError",
        )
        == before + 1
    )
