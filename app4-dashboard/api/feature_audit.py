"""Audited reversible mutations for persisted map features."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from shared.database import MapFeature, MapFeatureAuditEvent

from .map_support import JsonObject, RouteSession, map_feature_geojson

FeatureAction = Literal[
    "created",
    "updated",
    "reviewed",
    "unreviewed",
    "tombstoned",
    "restored",
]


def feature_snapshot(
    session: RouteSession,
    feature: MapFeature,
) -> JsonObject:
    return map_feature_geojson(session, feature)


def record_feature_audit(
    session: RouteSession,
    feature: MapFeature,
    *,
    actor_subject: str,
    action: FeatureAction,
    before_state: JsonObject | None,
    after_state: JsonObject | None,
    reason: str = "",
) -> None:
    session.add(
        MapFeatureAuditEvent(
            mission_id=feature.mission_id,
            feature_id=feature.id,
            actor_subject=actor_subject,
            action=action,
            reason=reason.strip() or None,
            before_state=before_state,
            after_state=after_state,
        )
    )


def apply_feature_lifecycle_action(
    feature: MapFeature,
    *,
    action: Literal["review", "unreview", "delete", "restore"],
    actor_subject: str,
    reason: str,
) -> FeatureAction:
    now = datetime.now(UTC)
    record = cast(Any, feature)
    if action == "review":
        record.reviewed_at = now
        record.reviewed_by = actor_subject
        audit_action: FeatureAction = "reviewed"
    elif action == "unreview":
        record.reviewed_at = None
        record.reviewed_by = None
        audit_action = "unreviewed"
    elif action == "delete":
        record.deleted_at = now
        record.deleted_by = actor_subject
        record.deletion_reason = reason.strip() or "operator correction"
        audit_action = "tombstoned"
    else:
        record.deleted_at = None
        record.deleted_by = None
        record.deletion_reason = None
        audit_action = "restored"
    record.version += 1
    record.updated_at = now
    return audit_action


def feature_lifecycle_change_needed(feature: MapFeature, action: str) -> bool:
    if action == "review":
        return feature.reviewed_at is None and feature.deleted_at is None
    if action == "unreview":
        return feature.reviewed_at is not None and feature.deleted_at is None
    if action == "delete":
        return feature.deleted_at is None
    return feature.deleted_at is not None
