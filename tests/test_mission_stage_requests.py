from __future__ import annotations

import importlib

mission_stages = importlib.import_module(
    "app4-dashboard.api.routers.mission_stages"
)
security = importlib.import_module("app4-dashboard.api.security")


def test_retry_idempotency_key_is_partitioned_by_organization() -> None:
    tenant_a = security.Principal(
        subject="shared-subject",
        role="operator",
        organization_id="tenant-a",
    )
    tenant_b = security.Principal(
        subject="shared-subject",
        role="operator",
        organization_id="tenant-b",
    )

    first = mission_stages._request_key(tenant_a, "request-42")
    repeated = mission_stages._request_key(tenant_a, "request-42")
    other_tenant = mission_stages._request_key(tenant_b, "request-42")

    assert first == repeated
    assert first != other_tenant
    assert len(first) == 64
