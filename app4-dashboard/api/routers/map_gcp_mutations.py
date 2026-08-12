"""Optimistically locked ground-control point and observation mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

from fastapi import APIRouter, HTTPException

from shared.database import GcpObservation, GcpPoint, GcpSet, get_session

from ..gcp_audit import record_gcp_audit
from ..gcp_route_support import (
    GcpSessionDependency,
    OperatorPrincipal,
    OwnerSubjectQuery,
    authorized_mission,
)
from ..gcp_schemas import GcpObservationUpdate, GcpPointUpdate
from ..gcp_workspace import (
    observation_json,
    point_json,
    read_image_dimensions,
    update_point_coordinates,
    validate_observation_pixels,
)
from ..map_support import JsonObject, RouteSession

router = APIRouter()


class GcpPointMutationRecord(Protocol):
    version: int
    gcp_set: GcpSet
    geometry: Any
    source_x: float
    source_y: float
    source_z: float
    altitude_m: float
    role: str
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    image_accuracy_px: float
    updated_at: datetime


class GcpObservationMutationRecord(Protocol):
    version: int
    status: str
    pixel_x: float | None
    pixel_y: float | None
    updated_by: str
    updated_at: datetime


@router.patch("/{vol_id}/gcps/points/{point_id}")
def update_ground_control_point(
    vol_id: str,
    point_id: str,
    request: GcpPointUpdate,
    principal: OperatorPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = authorized_mission(
        session,
        vol_id,
        principal,
        owner_subject,
        "gcp_point_update",
    )
    stored_point = cast(
        GcpPoint | None,
        session.query(GcpPoint)
        .filter(
            GcpPoint.mission_id == mission.id,
            GcpPoint.point_id == point_id,
        )
        .with_for_update()
        .first(),
    )
    if stored_point is None:
        raise HTTPException(status_code=404, detail="GCP point not found")
    point = cast(GcpPointMutationRecord, stored_point)
    if point.version != request.version:
        raise HTTPException(
            status_code=409,
            detail={"message": "GCP point changed", "current_version": point.version},
        )
    gcp_set = point.gcp_set
    before_state = point_json(session, stored_point)
    changes = request.model_dump(exclude_unset=True, exclude={"version"})
    longitude = changes.pop("longitude", None)
    latitude = changes.pop("latitude", None)
    altitude_m = changes.pop("altitude_m", None)
    if longitude is not None and latitude is not None and altitude_m is not None:
        update_point_coordinates(
            stored_point,
            gcp_set,
            longitude=longitude,
            latitude=latitude,
            altitude_m=altitude_m,
        )
    for field, value in changes.items():
        setattr(point, field, value)
    point.version += 1
    point.updated_at = datetime.now(UTC)
    session.flush()
    after_state = point_json(session, stored_point)
    record_gcp_audit(
        session,
        gcp_set,
        actor_subject=principal.subject,
        action="point_updated",
        before_state=before_state,
        after_state=after_state,
        point=stored_point,
    )
    session.flush()
    return after_state


@router.patch("/{vol_id}/gcps/observations/{observation_id}")
def update_ground_control_observation(
    vol_id: str,
    observation_id: str,
    request: GcpObservationUpdate,
    principal: OperatorPrincipal,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = authorized_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject,
            "gcp_observation_update",
        )
        stored_observation = cast(
            GcpObservation | None,
            typed_session.query(GcpObservation)
            .join(GcpPoint)
            .filter(
                GcpPoint.mission_id == mission.id,
                GcpObservation.observation_id == observation_id,
            )
            .with_for_update()
            .first(),
        )
        if stored_observation is None:
            raise HTTPException(status_code=404, detail="GCP observation not found")
        observation = cast(GcpObservationMutationRecord, stored_observation)
        if observation.version != request.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "GCP observation changed",
                    "current_version": observation.version,
                },
            )
        before_state = observation_json(stored_observation)
        if request.status == "marked":
            if request.pixel_x is None or request.pixel_y is None:
                raise HTTPException(
                    status_code=422,
                    detail="Marked GCP pixels are required",
                )
            width = stored_observation.image_width_px
            height = stored_observation.image_height_px
            if width is None or height is None:
                if not stored_observation.image_s3_key:
                    raise HTTPException(
                        status_code=422,
                        detail="The source image is unavailable for pixel-bound validation",
                    )
                try:
                    width, height = read_image_dimensions(
                        stored_observation.image_s3_key
                    )
                except (OSError, ValueError) as error:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unable to validate source image dimensions: {error}",
                    ) from error
                stored_observation.image_width_px = width
                stored_observation.image_height_px = height
            try:
                validate_observation_pixels(
                    request.pixel_x,
                    request.pixel_y,
                    int(width),
                    int(height),
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        observation.status = request.status
        observation.pixel_x = request.pixel_x
        observation.pixel_y = request.pixel_y
        observation.updated_by = principal.subject
        observation.version += 1
        observation.updated_at = datetime.now(UTC)
        typed_session.flush()
        after_state = observation_json(stored_observation)
        record_gcp_audit(
            typed_session,
            stored_observation.point.gcp_set,
            actor_subject=principal.subject,
            action="observation_updated",
            before_state=before_state,
            after_state=after_state,
            point=stored_observation.point,
            observation=stored_observation,
        )
        typed_session.flush()
        return after_state
