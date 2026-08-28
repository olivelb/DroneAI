"""Tenant-scoped Kafka keys for control and status events."""

from __future__ import annotations

from shared.tenancy import validate_organization_id


def tenant_mission_key(organization_id: str, vol_id: str) -> str:
    if not vol_id:
        raise ValueError("vol_id is required for a tenant mission key")
    return f"{validate_organization_id(organization_id)}:{vol_id}"
