"""Tenant-visible commercial policy and usage observability."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, TypedDict, cast

from fastapi import APIRouter, Depends, Query

from shared.database import OrganizationUsageEvent, get_session
from shared.organization_saas import (
    get_policy,
    organization_usage,
    policy_values,
)

from ..security import Principal, bind_tenant_context, require_admin

router = APIRouter(
    prefix="/operations/organization",
    tags=["operations"],
    dependencies=[Depends(require_admin), Depends(bind_tenant_context)],
)


class PolicyResponse(TypedDict):
    configured: bool
    version: int
    storage_limit_bytes: int | None
    concurrent_stage_runs_limit: int | None
    request_rate_per_minute: int | None
    request_burst: int | None
    retention_days: int | None
    updated_at: datetime | None


class UsageResponse(TypedDict):
    storage_bytes: int
    active_stage_runs: int
    active_stage_resource_units: int
    retention_eligible_missions: int


class CapacityResponse(TypedDict):
    organization_id: str
    policy: PolicyResponse
    usage: UsageResponse


class UsageEventResponse(TypedDict):
    event_id: str
    action: str
    resource_type: str
    resource_id: str
    quantity: int | None
    unit: str | None
    actor_subject: str
    details: dict[str, Any]
    created_at: datetime


@router.get("/capacity")
def read_organization_capacity(
    principal: Annotated[Principal, Depends(require_admin)],
) -> CapacityResponse:
    with get_session() as session:
        record = get_policy(session, principal.organization_id)
        policy = policy_values(record)
        usage = organization_usage(session, principal.organization_id)
        return {
            "organization_id": principal.organization_id,
            "policy": {
                "configured": record is not None,
                "version": int(record.version) if record is not None else 0,
                "storage_limit_bytes": policy.storage_limit_bytes,
                "concurrent_stage_runs_limit": (
                    policy.concurrent_stage_runs_limit
                ),
                "request_rate_per_minute": policy.request_rate_per_minute,
                "request_burst": policy.request_burst,
                "retention_days": policy.retention_days,
                "updated_at": (
                    cast(datetime, record.updated_at)
                    if record is not None
                    else None
                ),
            },
            "usage": {
                "storage_bytes": usage.storage_bytes,
                "active_stage_runs": usage.active_stage_runs,
                "active_stage_resource_units": (
                    usage.active_stage_resource_units
                ),
                "retention_eligible_missions": (
                    usage.retention_eligible_missions
                ),
            },
        }


@router.get("/usage-events")
def list_organization_usage_events(
    principal: Annotated[Principal, Depends(require_admin)],
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[UsageEventResponse]:
    with get_session() as session:
        records = session.query(OrganizationUsageEvent).filter(
            OrganizationUsageEvent.organization_id == principal.organization_id,
        ).order_by(
            OrganizationUsageEvent.created_at.desc(),
            OrganizationUsageEvent.id.desc(),
        ).limit(limit).all()
        return [
            {
                "event_id": cast(str, item.event_id),
                "action": cast(str, item.action),
                "resource_type": cast(str, item.resource_type),
                "resource_id": cast(str, item.resource_id),
                "quantity": cast(int | None, item.quantity),
                "unit": cast(str | None, item.unit),
                "actor_subject": cast(str, item.actor_subject),
                "details": cast(dict[str, Any], item.details or {}),
                "created_at": cast(datetime, item.created_at),
            }
            for item in records
        ]
