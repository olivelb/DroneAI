"""Append-only audit recording and serialization for the GCP workspace."""

from __future__ import annotations

from typing import Any, Literal, cast

from shared.database import GcpAuditEvent, GcpObservation, GcpPoint, GcpSet

from .map_support import JsonObject, RouteSession

GcpAuditAction = Literal[
    "imported",
    "point_updated",
    "observation_updated",
    "candidates_refreshed",
    "bundle_materialized",
]


def record_gcp_audit(
    session: RouteSession,
    gcp_set: GcpSet,
    *,
    actor_subject: str,
    action: GcpAuditAction,
    before_state: JsonObject | None,
    after_state: JsonObject | None,
    point: GcpPoint | None = None,
    observation: GcpObservation | None = None,
) -> None:
    """Append one audit event in the same transaction as its mutation."""

    stored_set = cast(Any, gcp_set)
    stored_point = cast(Any, point)
    stored_observation = cast(Any, observation)
    session.add(
        GcpAuditEvent(
            mission_id=stored_set.mission_id,
            gcp_set_id=stored_set.id,
            gcp_point_id=stored_point.id if point is not None else None,
            gcp_observation_id=(stored_observation.id if observation is not None else None),
            actor_subject=actor_subject,
            action=action,
            before_state=before_state,
            after_state=after_state,
        )
    )


def audit_event_json(event: GcpAuditEvent) -> JsonObject:
    record = cast(Any, event)
    return {
        "event_id": record.event_id,
        "action": record.action,
        "actor_subject": record.actor_subject,
        "point_id": record.point.point_id if record.point is not None else None,
        "observation_id": (record.observation.observation_id if record.observation is not None else None),
        "before_state": record.before_state,
        "after_state": record.after_state,
        "created_at": record.created_at.isoformat(),
    }
