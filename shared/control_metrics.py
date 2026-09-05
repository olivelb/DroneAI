"""Low-cardinality measurements of durable lifecycle backlog."""
from datetime import UTC, datetime
from prometheus_client import Gauge
from sqlalchemy import func
from sqlalchemy.orm import Session
from shared.database import Mission, MissionStageRun

DRAINING_AGE = Gauge("droneai_retention_oldest_draining_age_seconds", "Age of the oldest mission waiting for compute drainage.")
CLEANUP_PENDING = Gauge("droneai_stage_cleanup_pending", "Stage cleanups requested but not yet confirmed.")
CLEANUP_AGE = Gauge("droneai_stage_cleanup_oldest_pending_age_seconds", "Age of the oldest unconfirmed stage cleanup.")
EXPIRED_RESERVATIONS = Gauge("droneai_stage_reconciliation_expired_leases", "Expired stage reconciliation leases waiting for recovery.")


def observe_lifecycle_backlog(session: Session) -> None:
    now = datetime.now(UTC)
    oldest = session.query(func.min(Mission.updated_at)).filter(Mission.current_step == "RETENTION_DRAINING").scalar()
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    DRAINING_AGE.set(max(0, (now - oldest).total_seconds()) if oldest else 0)
    requested = MissionStageRun.provenance["cleanup_requested_at"].as_string()
    confirmed = MissionStageRun.provenance["cleanup_confirmed_at"].as_string()
    count, first = session.query(func.count(MissionStageRun.id), func.min(requested)).filter(requested.isnot(None), confirmed.is_(None)).one()
    CLEANUP_PENDING.set(count)
    CLEANUP_AGE.set(max(0, (now - datetime.fromisoformat(first)).total_seconds()) if first else 0)
    lease = MissionStageRun.provenance["reconcile_lease_until"].as_string()
    EXPIRED_RESERVATIONS.set(session.query(func.count(MissionStageRun.id)).filter(lease < now.isoformat()).scalar() or 0)
