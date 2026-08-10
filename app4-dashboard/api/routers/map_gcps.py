"""Ground-control import, map display and image marking routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError

from shared.database import GcpObservation, GcpPoint, GcpSet, get_session
from shared.gcp_candidates import PositionedImage, rank_image_candidates
from shared.gcp_import import import_gcp_bytes

from ..gcp_schemas import GcpObservationUpdate, GcpPointUpdate
from ..gcp_workspace import (
    MAX_GCP_UPLOAD_BYTES,
    load_mission_image_positions,
    observation_json,
    persist_imported_set,
    point_json,
    safe_upload_name,
    set_json,
    source_checksum,
    update_point_coordinates,
)
from ..map_support import JsonObject, RouteSession, get_mission
from ..security import Principal, require_authenticated, require_operator

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


def _image_key(dataset_prefix: str | None, image_name: str) -> str | None:
    if not dataset_prefix:
        return None
    return f"{dataset_prefix.rstrip('/')}/{image_name.lstrip('/')}"


@router.post("/{vol_id}/gcps/import", status_code=status.HTTP_201_CREATED)
async def import_ground_control(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    upload: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    source_crs: Annotated[str | None, Form(max_length=128)] = None,
    default_role: Annotated[str, Form()] = "adjustment",
    horizontal_accuracy_m: Annotated[float, Form(gt=0)] = 0.02,
    vertical_accuracy_m: Annotated[float, Form(gt=0)] = 0.03,
    image_accuracy_px: Annotated[float, Form(gt=0)] = 1.0,
    candidate_radius_m: Annotated[float, Form(gt=0, le=10_000)] = 250.0,
    max_candidates: Annotated[int, Form(ge=1, le=100)] = 20,
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    filename = safe_upload_name(upload.filename)
    payload = await upload.read(MAX_GCP_UPLOAD_BYTES + 1)
    if len(payload) > MAX_GCP_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="GCP upload exceeds 5 MiB")
    try:
        imported = import_gcp_bytes(
            payload,
            filename,
            source_crs=source_crs,
            default_role=default_role,
            horizontal_accuracy_m=horizontal_accuracy_m,
            vertical_accuracy_m=vertical_accuracy_m,
            image_accuracy_px=image_accuracy_px,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="gcp_import",
        )
        existing = typed_session.query(GcpSet).filter(
            GcpSet.mission_id == mission.id,
            GcpSet.name == name.strip(),
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="A GCP set with this name already exists")
        try:
            gcp_set, stored_points = persist_imported_set(
                typed_session,
                mission_id=mission.id,
                vol_id=vol_id,
                name=name.strip(),
                filename=filename,
                checksum=source_checksum(payload),
                imported=imported,
                actor_subject=principal.subject,
            )
            positions = load_mission_image_positions(vol_id)
            positioned_by_name = (
                {item.image_name: item for item in positions.images} if positions else {}
            )
            observation_count = 0
            for imported_point in imported.points:
                point = stored_points[imported_point.external_id]
                observation_specs: list[
                    tuple[
                        str,
                        str,
                        float | None,
                        float | None,
                        float | None,
                        PositionedImage | None,
                    ]
                ]
                if imported_point.observations:
                    observation_specs = [
                        (
                            item.image_name,
                            "marked",
                            item.pixel_x,
                            item.pixel_y,
                            None,
                            positioned_by_name.get(item.image_name),
                        )
                        for item in imported_point.observations
                    ]
                elif positions:
                    observation_specs = [
                        (
                            candidate.image.image_name,
                            "candidate",
                            None,
                            None,
                            candidate.distance_m,
                            candidate.image,
                        )
                        for candidate in rank_image_candidates(
                            longitude=imported_point.longitude,
                            latitude=imported_point.latitude,
                            images=positions.images,
                            projected_crs=positions.projected_crs,
                            radius_m=candidate_radius_m,
                            limit=max_candidates,
                        )
                    ]
                else:
                    observation_specs = []
                for (
                    image_name,
                    observation_status,
                    pixel_x,
                    pixel_y,
                    distance_m,
                    positioned,
                ) in observation_specs:
                    typed_session.add(
                        GcpObservation(
                            gcp_point_id=point.id,
                            image_name=image_name,
                            image_s3_key=_image_key(mission.input_dataset, image_name),
                            status=observation_status,
                            pixel_x=pixel_x,
                            pixel_y=pixel_y,
                            candidate_distance_m=distance_m,
                            image_longitude=(positioned.longitude if positioned else None),
                            image_latitude=(positioned.latitude if positioned else None),
                            created_by=principal.subject,
                            updated_by=principal.subject,
                        )
                    )
                    observation_count += 1
            typed_session.flush()
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail="GCP import conflicts with stored data") from error
        return {
            "gcp_set": set_json(typed_session, gcp_set, include_points=True),
            "candidate_generation": {
                "available": positions is not None,
                "method": "exif-distance" if positions else None,
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
                "observation_count": observation_count,
                "message": (
                    None
                    if positions
                    else "Image positions are not published yet; candidates can be refreshed after preflight"
                ),
            },
        }


@router.get("/{vol_id}/gcps")
def list_ground_control(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="gcp_list",
        )
        sets = cast(
            list[GcpSet],
            typed_session.query(GcpSet)
            .filter(GcpSet.mission_id == mission.id)
            .order_by(GcpSet.created_at.desc())
            .all(),
        )
        features = [
            point_json(typed_session, point)
            for gcp_set in sets
            for point in cast(list[GcpPoint], gcp_set.points)
        ]
        return {
            "type": "FeatureCollection",
            "features": features,
            "gcp_sets": [set_json(typed_session, item, include_points=False) for item in sets],
        }


@router.get("/{vol_id}/gcps/{set_id}")
def ground_control_detail(
    vol_id: str,
    set_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="gcp_detail",
        )
        gcp_set = cast(
            GcpSet | None,
            typed_session.query(GcpSet).filter(
                GcpSet.mission_id == mission.id,
                GcpSet.set_id == set_id,
            ).first(),
        )
        if gcp_set is None:
            raise HTTPException(status_code=404, detail="GCP set not found")
        return set_json(typed_session, gcp_set, include_points=True)


@router.patch("/{vol_id}/gcps/points/{point_id}")
def update_ground_control_point(
    vol_id: str,
    point_id: str,
    request: GcpPointUpdate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="gcp_point_update",
        )
        stored_point = cast(
            GcpPoint | None,
            typed_session.query(GcpPoint).filter(
                GcpPoint.mission_id == mission.id,
                GcpPoint.point_id == point_id,
            ).with_for_update().first(),
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
        typed_session.flush()
        return point_json(typed_session, stored_point)


@router.patch("/{vol_id}/gcps/observations/{observation_id}")
def update_ground_control_observation(
    vol_id: str,
    observation_id: str,
    request: GcpObservationUpdate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action="gcp_observation_update",
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
        observation.status = request.status
        observation.pixel_x = request.pixel_x
        observation.pixel_y = request.pixel_y
        observation.updated_by = principal.subject
        observation.version += 1
        observation.updated_at = datetime.now(UTC)
        typed_session.flush()
        return observation_json(stored_observation)
