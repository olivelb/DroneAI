"""Policy-driven, retryable mission retention for the elected control worker."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from shared import storage
from shared.database import (
    Mission,
    MissionArtifact,
    OrganizationSaasPolicy,
    get_session,
)
from shared.organization_saas import (
    RETENTION_TERMINAL_STATUSES,
    append_usage_event,
)
from shared.observability import observe_control_loop, observe_reconciliation
from shared.tenancy import MissionObjectNamespace

logger = logging.getLogger("droneai.retention")
RETRYABLE_RETENTION_STATUSES = ("deleting", "deletion_failed")


@dataclass(frozen=True)
class RetentionCandidate:
    mission_id: int
    organization_id: str
    vol_id: str
    prefix: str


def _locked_deleting_mission(
    session: Session,
    candidate: RetentionCandidate,
) -> Mission | None:
    return cast(
        Mission | None,
        session.query(Mission)
        .filter(
            Mission.id == candidate.mission_id,
            Mission.organization_id == candidate.organization_id,
            Mission.status == "deleting",
        )
        .with_for_update()
        .one_or_none(),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def claim_retention_candidates(
    session: Session,
    *,
    now: datetime,
    retry_seconds: int,
    limit: int = 100,
) -> list[RetentionCandidate]:
    rows = (
        session.query(Mission, OrganizationSaasPolicy)
        .outerjoin(
            OrganizationSaasPolicy,
            OrganizationSaasPolicy.organization_id == Mission.organization_id,
        )
        .filter(
            or_(
                and_(
                    OrganizationSaasPolicy.retention_days.isnot(None),
                    Mission.status.in_(RETENTION_TERMINAL_STATUSES),
                ),
                Mission.status.in_(RETRYABLE_RETENTION_STATUSES),
            ),
        )
        .order_by(Mission.updated_at, Mission.id)
        .with_for_update(
            of=Mission,
            skip_locked=True,
        )
        .limit(limit * 5)
        .all()
    )
    candidates: list[RetentionCandidate] = []
    for mission, policy in rows:
        updated_at = _aware(cast(datetime, mission.updated_at))
        if mission.status in RETRYABLE_RETENTION_STATUSES:
            eligible = updated_at <= now - timedelta(seconds=retry_seconds)
        else:
            if policy is None or policy.retention_days is None:
                continue
            eligible = updated_at <= now - timedelta(
                days=cast(int, policy.retention_days),
            )
        if not eligible:
            continue
        namespace = MissionObjectNamespace.from_binding(
            cast(str, mission.organization_id),
            cast(str, mission.vol_id),
            cast(str, mission.workspace_prefix),
        )
        mission.status = "deleting"
        mission.current_step = "RETENTION_DELETING"
        mission.error_message = None
        candidates.append(
            RetentionCandidate(
                mission_id=cast(int, mission.id),
                organization_id=cast(str, mission.organization_id),
                vol_id=cast(str, mission.vol_id),
                prefix=namespace.prefix(),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def _complete_retention(
    candidate: RetentionCandidate,
    *,
    objects_deleted: int,
) -> bool:
    with get_session() as session:
        mission = _locked_deleting_mission(session, candidate)
        if mission is None:
            return False
        artifact_bytes = int(
            session.query(func.sum(MissionArtifact.size_bytes))
            .filter(
                MissionArtifact.mission_id == candidate.mission_id,
            )
            .scalar()
            or 0
        )
        append_usage_event(
            session,
            organization_id=candidate.organization_id,
            action="retention_deleted",
            resource_type="mission",
            resource_id=candidate.vol_id,
            actor_subject="system:retention",
            quantity=-artifact_bytes,
            unit="bytes",
            idempotency_key=f"retention-deleted:mission:{candidate.mission_id}",
            details={"objects_deleted": objects_deleted},
        )
        session.query(Mission).filter(
            Mission.id == candidate.mission_id,
        ).delete(synchronize_session=False)
    return True


def _fail_retention(candidate: RetentionCandidate, error: Exception) -> None:
    with get_session() as session:
        mission = _locked_deleting_mission(session, candidate)
        if mission is None:
            return
        mission.status = "deletion_failed"
        mission.current_step = "RETENTION_FAILED"
        mission.error_message = f"Retention object deletion failed: {error}"[:4000]
        append_usage_event(
            session,
            organization_id=candidate.organization_id,
            action="retention_failed",
            resource_type="mission",
            resource_id=candidate.vol_id,
            actor_subject="system:retention",
            details={"error": str(error)[:1000]},
        )


def retention_cleanup_once(
    *,
    now: datetime | None = None,
    retry_seconds: int = 3_600,
    delete_prefix: Callable[[str], int] = storage.delete_prefix,
) -> int:
    current_time = now or datetime.now(UTC)
    with get_session() as session:
        candidates = claim_retention_candidates(
            session,
            now=current_time,
            retry_seconds=retry_seconds,
        )
    completed = 0
    for candidate in candidates:
        try:
            deleted = delete_prefix(candidate.prefix)
            completed += int(
                _complete_retention(
                    candidate,
                    objects_deleted=deleted,
                )
            )
        except Exception as error:
            logger.exception(
                "Retention failed for organization %s mission %s",
                candidate.organization_id,
                candidate.vol_id,
            )
            _fail_retention(candidate, error)
    return completed


def run_retention_cleanup(stop_event: Event) -> None:
    try:
        interval = int(os.getenv("DRONEAI_RETENTION_CLEANUP_SECONDS", "900"))
        retry_seconds = int(
            os.getenv("DRONEAI_RETENTION_FAILURE_RETRY_SECONDS", "3600")
        )
    except ValueError:
        logger.exception("Invalid retention cleanup configuration; cleanup disabled")
        return
    if interval < 60 or retry_seconds < 60:
        logger.error("Retention cleanup and retry intervals must be at least 60s")
        return
    while not stop_event.is_set():
        try:
            completed = retention_cleanup_once(retry_seconds=retry_seconds)
            observe_reconciliation("retention", "deleted", completed)
            observe_control_loop("retention", succeeded=True)
        except Exception:
            observe_control_loop("retention", succeeded=False)
            logger.exception("Retention cleanup pass failed")
        stop_event.wait(interval)
