"""Audited, reversible mutations for manual and persisted AI features."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func

from shared.database import MapFeature, MapFeatureAuditEvent, get_session

from ..feature_audit import (
    apply_feature_lifecycle_action,
    feature_lifecycle_change_needed,
    feature_snapshot,
    record_feature_audit,
)
from ..map_schemas import MapFeatureBulkMutation, MapFeatureCreate, MapFeatureUpdate
from ..map_support import (
    JsonObject,
    MissionRecord,
    MapFeatureMutationRecord,
    RouteSession,
    get_mission,
    map_feature_geojson,
)
from ..security import Principal, require_authenticated, require_operator

router = APIRouter()


@contextmanager
def _mutation_context(
    vol_id: str,
    principal: Principal,
    owner_subject: str | None,
    action: str,
) -> Iterator[tuple[RouteSession, MissionRecord]]:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action=action,
        )
        yield typed_session, mission


def _locked_editable_feature(
    session: RouteSession,
    vol_id: str,
    feature_id: str,
    *,
    include_deleted: bool = False,
) -> MapFeature | None:
    query = session.query(MapFeature).filter(
        MapFeature.vol_id == vol_id,
        MapFeature.feature_id == feature_id,
        MapFeature.source.in_(("manual", "ai")),
    )
    if not include_deleted:
        query = query.filter(MapFeature.deleted_at.is_(None))
    return cast(MapFeature | None, query.with_for_update().first())


@router.post("/{vol_id}/features", status_code=status.HTTP_201_CREATED)
def create_map_feature(
    vol_id: str,
    request: MapFeatureCreate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with _mutation_context(
        vol_id, principal, owner_subject, "feature_create"
    ) as (typed_session, mission):
        feature = MapFeature(
            mission_id=mission.id,
            vol_id=vol_id,
            source="manual",
            geometry=func.ST_SetSRID(
                func.ST_GeomFromGeoJSON(json.dumps(request.geometry)), 4326
            ),
            name=request.name.strip(),
            description=request.description.strip(),
            color=request.color,
            tags=request.tags,
            properties=request.properties,
            created_by=principal.subject,
        )
        typed_session.add(feature)
        typed_session.flush()
        created = map_feature_geojson(typed_session, feature)
        record_feature_audit(
            typed_session, feature,
            actor_subject=principal.subject, action="created",
            before_state=None, after_state=created,
        )
        return created


@router.patch("/{vol_id}/features/{feature_id}")
def update_map_feature(
    vol_id: str,
    feature_id: str,
    request: MapFeatureUpdate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with _mutation_context(
        vol_id, principal, owner_subject, "feature_update"
    ) as (typed_session, _mission):
        feature = cast(
            MapFeatureMutationRecord | None,
            _locked_editable_feature(typed_session, vol_id, feature_id),
        )
        if feature is None:
            raise HTTPException(status_code=404, detail="Feature not found")
        if feature.version != request.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Feature was changed by another user",
                    "current_version": feature.version,
                },
            )
        stored_feature = cast(MapFeature, feature)
        before_state = feature_snapshot(typed_session, stored_feature)
        changes = request.model_dump(exclude_unset=True)
        changes.pop("version", None)
        if "geometry" in changes:
            feature.geometry = func.ST_SetSRID(
                func.ST_GeomFromGeoJSON(json.dumps(changes.pop("geometry"))), 4326
            )
        for field, value in changes.items():
            setattr(feature, field, value.strip() if isinstance(value, str) else value)
        feature.version += 1
        feature.updated_at = datetime.now(UTC)
        typed_session.flush()
        after_state = feature_snapshot(typed_session, stored_feature)
        record_feature_audit(
            typed_session, stored_feature,
            actor_subject=principal.subject, action="updated",
            before_state=before_state, after_state=after_state,
        )
        return after_state


@router.delete("/{vol_id}/features/{feature_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_map_feature(
    vol_id: str,
    feature_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    reason: Annotated[str, Query(max_length=2000)] = "",
) -> Response:
    with _mutation_context(
        vol_id, principal, owner_subject, "feature_delete"
    ) as (typed_session, _mission):
        feature = _locked_editable_feature(
            typed_session, vol_id, feature_id,
        )
        if feature is None:
            raise HTTPException(status_code=404, detail="Feature not found")
        before_state = feature_snapshot(typed_session, feature)
        audit_action = apply_feature_lifecycle_action(
            feature, action="delete",
            actor_subject=principal.subject, reason=reason,
        )
        typed_session.flush()
        record_feature_audit(
            typed_session, feature,
            actor_subject=principal.subject, action=audit_action,
            before_state=before_state,
            after_state=feature_snapshot(typed_session, feature), reason=reason,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{vol_id}/features/bulk")
def mutate_map_features_bulk(
    vol_id: str,
    request: MapFeatureBulkMutation,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with _mutation_context(
        vol_id, principal, owner_subject, f"feature_bulk_{request.action}"
    ) as (typed_session, _mission):
        features = cast(
            list[MapFeature],
            typed_session.query(MapFeature).filter(
                MapFeature.vol_id == vol_id,
                MapFeature.feature_id.in_(request.feature_ids),
                MapFeature.source.in_(("manual", "ai")),
            ).with_for_update().all(),
        )
        if {cast(str, feature.feature_id) for feature in features} != set(request.feature_ids):
            raise HTTPException(status_code=404, detail="One or more features were not found")
        changed: list[JsonObject] = []
        for feature in features:
            public_id = cast(str, feature.feature_id)
            expected = request.expected_versions.get(public_id)
            if expected is not None and int(feature.version) != expected:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Feature was changed by another user",
                        "feature_id": public_id,
                        "current_version": feature.version,
                    },
                )
            if not feature_lifecycle_change_needed(feature, request.action):
                continue
            before_state = feature_snapshot(typed_session, feature)
            audit_action = apply_feature_lifecycle_action(
                feature, action=request.action,
                actor_subject=principal.subject, reason=request.reason,
            )
            typed_session.flush()
            after_state = feature_snapshot(typed_session, feature)
            record_feature_audit(
                typed_session, feature,
                actor_subject=principal.subject, action=audit_action,
                before_state=before_state, after_state=after_state,
                reason=request.reason,
            )
            changed.append(after_state)
        return {
            "action": request.action,
            "requested_count": len(request.feature_ids),
            "changed_count": len(changed),
            "features": changed,
        }


@router.get("/{vol_id}/features/{feature_id}/audit")
def map_feature_audit(
    vol_id: str,
    feature_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session, vol_id, principal,
            owner_subject=owner_subject, action="feature_audit",
        )
        feature = typed_session.query(MapFeature).filter(
            MapFeature.mission_id == mission.id,
            MapFeature.feature_id == feature_id,
        ).first()
        if feature is None:
            raise HTTPException(status_code=404, detail="Feature not found")
        events = cast(
            list[MapFeatureAuditEvent],
            typed_session.query(MapFeatureAuditEvent).filter(
                MapFeatureAuditEvent.feature_id == feature.id
            ).order_by(MapFeatureAuditEvent.created_at.desc()).limit(limit).all(),
        )
        return {
            "feature_id": feature_id,
            "events": [
                {
                    "event_id": event.event_id,
                    "action": event.action,
                    "actor_subject": event.actor_subject,
                    "reason": event.reason,
                    "before_state": event.before_state,
                    "after_state": event.after_state,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
        }
