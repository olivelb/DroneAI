"""Organization-level commercial policy, metering, and quota decisions.

These controls deliberately do not import or derive from scientific quality
profiles. They describe customer capacity and lifecycle policy only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.database import (
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    MissionStageRun,
    OrganizationRequestBucket,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)

ACTIVE_UPLOAD_STATUSES = ("initializing", "uploading", "finalizing", "failed")
ACTIVE_STAGE_STATUSES = ("queued", "running")
MANUAL_DELETION_STEP = "DELETION_REQUESTED"
MANUAL_DELETION_FAILED_STEP = "MANUAL_DELETION_FAILED"
RETENTION_TERMINAL_STATUSES = (
    "success",
    "completed",
    "error",
    "cancelled",
    "stale",
)


@dataclass(frozen=True)
class PolicyValues:
    storage_limit_bytes: int | None = None
    concurrent_stage_runs_limit: int | None = None
    request_rate_per_minute: int | None = None
    request_burst: int | None = None
    retention_days: int | None = None

    def __post_init__(self) -> None:
        positive = (
            self.storage_limit_bytes,
            self.concurrent_stage_runs_limit,
            self.request_rate_per_minute,
            self.request_burst,
            self.retention_days,
        )
        if any(
            value is not None
            and (isinstance(value, bool) or value < 1)
            for value in positive
        ):
            raise ValueError("SaaS policy limits must be positive or null")
        if (self.request_rate_per_minute is None) != (
            self.request_burst is None
        ):
            raise ValueError("Request rate and burst must be configured together")


@dataclass(frozen=True)
class OrganizationUsage:
    storage_bytes: int
    active_stage_runs: int
    active_stage_resource_units: int
    retention_eligible_missions: int


@dataclass(frozen=True)
class RequestQuotaDecision:
    requests_per_minute: int | None
    retry_after_seconds: float | None


class StorageQuotaExceeded(RuntimeError):
    def __init__(
        self,
        *,
        current_bytes: int,
        requested_bytes: int,
        limit_bytes: int,
    ) -> None:
        self.current_bytes = current_bytes
        self.requested_bytes = requested_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            "Organization storage quota exceeded: "
            f"{current_bytes} + {requested_bytes} > {limit_bytes} bytes"
        )


def policy_values(record: OrganizationSaasPolicy | None) -> PolicyValues:
    if record is None:
        return PolicyValues()
    return PolicyValues(
        storage_limit_bytes=cast(int | None, record.storage_limit_bytes),
        concurrent_stage_runs_limit=cast(
            int | None,
            record.concurrent_stage_runs_limit,
        ),
        request_rate_per_minute=cast(
            int | None,
            record.request_rate_per_minute,
        ),
        request_burst=cast(int | None, record.request_burst),
        retention_days=cast(int | None, record.retention_days),
    )


def get_policy(
    session: Session,
    organization_id: str,
    *,
    lock: bool = False,
) -> OrganizationSaasPolicy | None:
    query = session.query(OrganizationSaasPolicy).filter(
        OrganizationSaasPolicy.organization_id == organization_id,
    )
    if lock:
        query = query.with_for_update()
    return cast(OrganizationSaasPolicy | None, query.one_or_none())


def append_usage_event(
    session: Session,
    *,
    organization_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    actor_subject: str,
    quantity: int | None = None,
    unit: str | None = None,
    idempotency_key: str | None = None,
    details: dict[str, Any] | None = None,
) -> OrganizationUsageEvent:
    if idempotency_key is not None:
        existing = session.query(OrganizationUsageEvent).filter(
            OrganizationUsageEvent.idempotency_key == idempotency_key,
        ).one_or_none()
        if existing is not None:
            return cast(OrganizationUsageEvent, existing)
    event = OrganizationUsageEvent(
        event_id=str(uuid4()),
        organization_id=organization_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        quantity=quantity,
        unit=unit,
        actor_subject=actor_subject,
        idempotency_key=idempotency_key,
        details=details or {},
    )
    session.add(event)
    return event


def set_policy(
    session: Session,
    *,
    organization_id: str,
    values: PolicyValues,
    actor_subject: str,
) -> OrganizationSaasPolicy:
    actor_subject = actor_subject.strip()
    if not actor_subject or len(actor_subject) > 256:
        raise ValueError("Policy actor subject must contain 1 to 256 characters")
    record = get_policy(session, organization_id, lock=True)
    before = asdict(policy_values(record))
    if record is None:
        record = OrganizationSaasPolicy(
            organization_id=organization_id,
            version=1,
            created_by=actor_subject,
            updated_by=actor_subject,
        )
        session.add(record)
    else:
        record.version = cast(int, record.version) + 1
        record.updated_by = actor_subject
    for field_name, value in asdict(values).items():
        setattr(record, field_name, value)
    session.flush()
    version = cast(int, record.version)
    append_usage_event(
        session,
        organization_id=organization_id,
        action="policy_updated",
        resource_type="organization_policy",
        resource_id=organization_id,
        actor_subject=actor_subject,
        quantity=version,
        unit="policy_version",
        idempotency_key=f"organization-policy:{organization_id}:{version}",
        details={"before": before, "after": asdict(values)},
    )
    return record


def _sum_query(query: Any) -> int:
    value = query.scalar()
    return int(value or 0)


def storage_usage_bytes(session: Session, organization_id: str) -> int:
    dataset_bytes = _sum_query(
        session.query(func.sum(Dataset.total_bytes)).filter(
            Dataset.organization_id == organization_id,
            Dataset.status != "deleted",
        )
    )
    reserved_upload_bytes = _sum_query(
        session.query(func.sum(DatasetUploadSession.total_bytes)).filter(
            DatasetUploadSession.organization_id == organization_id,
            DatasetUploadSession.status.in_(ACTIVE_UPLOAD_STATUSES),
        )
    )
    artifact_bytes = _sum_query(
        session.query(func.sum(MissionArtifact.size_bytes))
        .join(Mission, Mission.id == MissionArtifact.mission_id)
        .filter(Mission.organization_id == organization_id)
    )
    return dataset_bytes + reserved_upload_bytes + artifact_bytes


def check_storage_reservation(
    session: Session,
    *,
    organization_id: str,
    requested_bytes: int,
) -> int:
    if requested_bytes < 0:
        raise ValueError("Storage reservation cannot be negative")
    policy = get_policy(session, organization_id, lock=True)
    current = storage_usage_bytes(session, organization_id)
    limit = policy_values(policy).storage_limit_bytes
    if limit is not None and current + requested_bytes > limit:
        raise StorageQuotaExceeded(
            current_bytes=current,
            requested_bytes=requested_bytes,
            limit_bytes=limit,
        )
    return current


def record_storage_reservation(
    session: Session,
    *,
    organization_id: str,
    upload_session_id: str,
    requested_bytes: int,
    actor_subject: str,
) -> None:
    append_usage_event(
        session,
        organization_id=organization_id,
        action="storage_reserved",
        resource_type="dataset_upload",
        resource_id=upload_session_id,
        actor_subject=actor_subject,
        quantity=requested_bytes,
        unit="bytes",
        idempotency_key=f"storage-reserved:upload:{upload_session_id}",
        details={
            "usage_after_bytes": storage_usage_bytes(session, organization_id),
        },
    )


def reserve_stage_output_storage(
    session: Session,
    *,
    organization_id: str,
    stage_run_id: str,
    artifact_id: str,
    output_bytes: int,
    actor_subject: str,
) -> None:
    """Atomically reserve a stage output before it enters the durable graph."""

    current = check_storage_reservation(
        session,
        organization_id=organization_id,
        requested_bytes=output_bytes,
    )
    append_usage_event(
        session,
        organization_id=organization_id,
        action="storage_reserved",
        resource_type="stage_artifact",
        resource_id=artifact_id,
        actor_subject=actor_subject,
        quantity=output_bytes,
        unit="bytes",
        idempotency_key=f"storage-reserved:stage-artifact:{artifact_id}",
        details={
            "stage_run_id": stage_run_id,
            "usage_after_bytes": current + output_bytes,
        },
    )


def record_storage_release(
    session: Session,
    *,
    organization_id: str,
    resource_type: str,
    resource_id: str,
    released_bytes: int,
    actor_subject: str,
    idempotency_key: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "usage_after_bytes": storage_usage_bytes(session, organization_id),
        **(details or {}),
    }
    append_usage_event(
        session,
        organization_id=organization_id,
        action="storage_released",
        resource_type=resource_type,
        resource_id=resource_id,
        actor_subject=actor_subject,
        quantity=-released_bytes,
        unit="bytes",
        idempotency_key=idempotency_key,
        details=payload,
    )


def stage_run_limits(
    session: Session,
    organization_ids: set[str],
    *,
    platform_limit: int,
) -> dict[str, int]:
    if not organization_ids:
        return {}
    records = session.query(OrganizationSaasPolicy).filter(
        OrganizationSaasPolicy.organization_id.in_(organization_ids),
        OrganizationSaasPolicy.concurrent_stage_runs_limit.isnot(None),
    ).all()
    return {
        cast(str, record.organization_id): min(
            platform_limit,
            cast(int, record.concurrent_stage_runs_limit),
        )
        for record in records
    }


def _resource_units(run: MissionStageRun) -> int:
    provenance = cast(dict[str, Any], run.provenance or {})
    value = provenance.get("resource_units", 1)
    return value if isinstance(value, int) and value >= 1 else 1


def organization_usage(
    session: Session,
    organization_id: str,
    *,
    now: datetime | None = None,
) -> OrganizationUsage:
    rows = session.query(MissionStageRun).join(
        Mission,
        Mission.id == MissionStageRun.mission_id,
    ).filter(
        Mission.organization_id == organization_id,
        MissionStageRun.executor == "kubernetes-job",
        MissionStageRun.status.in_(ACTIVE_STAGE_STATUSES),
    ).all()
    policy = policy_values(get_policy(session, organization_id))
    eligible = 0
    if policy.retention_days is not None:
        cutoff = (now or datetime.now(UTC)) - timedelta(
            days=policy.retention_days,
        )
        eligible = int(
            session.query(Mission.id).filter(
                Mission.organization_id == organization_id,
                Mission.status.in_(RETENTION_TERMINAL_STATUSES),
                Mission.updated_at <= cutoff,
            ).count()
        )
    return OrganizationUsage(
        storage_bytes=storage_usage_bytes(session, organization_id),
        active_stage_runs=len(rows),
        active_stage_resource_units=sum(_resource_units(item) for item in rows),
        retention_eligible_missions=eligible,
    )


def consume_request_quota(
    session: Session,
    *,
    organization_id: str,
    actor_subject: str,
    now: datetime | None = None,
) -> RequestQuotaDecision:
    current_time = now or datetime.now(UTC)
    policy = get_policy(session, organization_id, lock=True)
    values = policy_values(policy)
    rate = values.request_rate_per_minute
    burst = values.request_burst
    if rate is None or burst is None:
        return RequestQuotaDecision(None, None)
    bucket = session.get(OrganizationRequestBucket, organization_id)
    if bucket is None:
        bucket = OrganizationRequestBucket(
            organization_id=organization_id,
            tokens=float(burst - 1),
            updated_at=current_time,
        )
        session.add(bucket)
        return RequestQuotaDecision(rate, None)
    updated_at = cast(datetime, bucket.updated_at)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    refill_rate = rate / 60.0
    tokens = min(
        float(burst),
        float(bucket.tokens)
        + max(0.0, (current_time - updated_at).total_seconds()) * refill_rate,
    )
    bucket.updated_at = current_time
    if tokens >= 1.0:
        bucket.tokens = tokens - 1.0
        return RequestQuotaDecision(rate, None)
    bucket.tokens = tokens
    retry_after = (1.0 - tokens) / refill_rate
    minute = int(current_time.timestamp() // 60)
    append_usage_event(
        session,
        organization_id=organization_id,
        action="request_throttled",
        resource_type="api_request_bucket",
        resource_id=organization_id,
        actor_subject=actor_subject,
        quantity=1,
        unit="request",
        idempotency_key=f"request-throttled:{organization_id}:{minute}",
        details={"retry_after_seconds": retry_after, "rate_per_minute": rate},
    )
    return RequestQuotaDecision(rate, retry_after)
