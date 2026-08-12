"""Ground-control import, map display and image marking routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError

from shared.database import GcpAuditEvent, GcpObservation, GcpPoint, GcpSet, get_session
from shared.camera_projection import CameraProjectionIndex, rank_projected_image_candidates
from shared.gcp_candidates import (
    PositionedImage,
    rank_image_candidates,
    rank_new_image_candidates,
)
from shared.gcp_import import import_gcp_bytes
from shared.tenancy import MissionObjectNamespace

from ..gcp_schemas import GcpObservationUpdate, GcpPointUpdate
from ..gcp_audit import audit_event_json, record_gcp_audit
from ..gcp_workspace import (
    MAX_GCP_UPLOAD_BYTES,
    gcp_route_session,
    load_mission_image_positions,
    load_camera_projection_index,
    materialize_gcp_bundle,
    observation_json,
    persist_imported_set,
    point_longitude_latitude,
    point_json,
    read_image_dimensions,
    safe_upload_name,
    set_json,
    source_checksum,
    update_point_coordinates,
    validate_observation_pixels,
)
from ..map_support import JsonObject, MissionRecord, RouteSession, get_mission
from ..security import Principal, require_authenticated, require_operator

router = APIRouter()
OperatorPrincipal = Annotated[Principal, Depends(require_operator)]
ViewerPrincipal = Annotated[Principal, Depends(require_authenticated)]
GcpSessionDependency = Annotated[RouteSession, Depends(gcp_route_session)]
OwnerSubjectQuery = Annotated[str | None, Query(max_length=256)]


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


@dataclass(frozen=True)
class CandidateSpec:
    image_name: str
    method: str
    distance_m: float | None = None
    projected_pixel_x: float | None = None
    projected_pixel_y: float | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    positioned: PositionedImage | None = None


def _rank_candidate_specs(
    *,
    longitude: float,
    latitude: float,
    altitude_m: float,
    positions: Any,
    camera_index: CameraProjectionIndex | None,
    radius_m: float,
    limit: int,
    existing_image_names: set[str] | None = None,
) -> tuple[CandidateSpec, ...]:
    """Prefer registered-camera visibility and fill remaining slots by EXIF distance."""

    excluded = set(existing_image_names or set())
    positioned_by_name = {item.image_name: item for item in positions.images} if positions else {}
    specs: list[CandidateSpec] = []
    if camera_index is not None:
        for candidate in rank_projected_image_candidates(
            longitude=longitude,
            latitude=latitude,
            altitude_m=altitude_m,
            camera_index=camera_index,
            limit=limit,
            existing_image_names=excluded,
            border_margin_ratio=0.01,
        ):
            positioned = positioned_by_name.get(candidate.image_name)
            distance = None
            if positioned is not None:
                transformer_candidates = rank_image_candidates(
                    longitude=longitude,
                    latitude=latitude,
                    images=(positioned,),
                    projected_crs=positions.projected_crs,
                    radius_m=max(radius_m, 1.0e-6),
                    limit=1,
                )
                if transformer_candidates:
                    distance = transformer_candidates[0].distance_m
            specs.append(
                CandidateSpec(
                    image_name=candidate.image_name,
                    method="camera-projection",
                    distance_m=distance,
                    projected_pixel_x=candidate.pixel_x,
                    projected_pixel_y=candidate.pixel_y,
                    image_width_px=candidate.image_width_px,
                    image_height_px=candidate.image_height_px,
                    positioned=positioned,
                )
            )
            excluded.add(candidate.image_name)
    if positions is not None and len(specs) < limit:
        for exif_candidate in rank_new_image_candidates(
            longitude=longitude,
            latitude=latitude,
            images=positions.images,
            projected_crs=positions.projected_crs,
            radius_m=radius_m,
            limit=limit - len(specs),
            existing_image_names=excluded,
        ):
            specs.append(
                CandidateSpec(
                    image_name=exif_candidate.image.image_name,
                    method="exif-distance",
                    distance_m=exif_candidate.distance_m,
                    positioned=exif_candidate.image,
                )
            )
    return tuple(specs)


def _image_key(dataset_prefix: str | None, image_name: str) -> str | None:
    if not dataset_prefix:
        return None
    return f"{dataset_prefix.rstrip('/')}/{image_name.lstrip('/')}"


def _candidate_observation(
    point: GcpPoint,
    candidate: CandidateSpec,
    *,
    dataset_prefix: str | None,
    actor_subject: str,
    status: str = "candidate",
    pixel_x: float | None = None,
    pixel_y: float | None = None,
) -> GcpObservation:
    """Build one consistently-provenanced photo observation."""

    positioned = candidate.positioned
    return GcpObservation(
        gcp_point_id=point.id,
        image_name=candidate.image_name,
        image_s3_key=_image_key(dataset_prefix, candidate.image_name),
        status=status,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        candidate_distance_m=candidate.distance_m,
        candidate_method=candidate.method,
        projected_pixel_x=candidate.projected_pixel_x,
        projected_pixel_y=candidate.projected_pixel_y,
        image_width_px=candidate.image_width_px,
        image_height_px=candidate.image_height_px,
        image_longitude=(positioned.longitude if positioned else None),
        image_latitude=(positioned.latitude if positioned else None),
        created_by=actor_subject,
        updated_by=actor_subject,
    )


def _authorized_mission(
    session: RouteSession,
    vol_id: str,
    principal: Principal,
    owner_subject: str | None,
    action: str,
) -> MissionRecord:
    return get_mission(
        session,
        vol_id,
        principal,
        owner_subject=owner_subject,
        action=action,
    )


def _require_gcp_set(
    session: RouteSession,
    mission_id: int,
    set_id: str,
) -> GcpSet:
    gcp_set = cast(
        GcpSet | None,
        session.query(GcpSet)
        .filter(
            GcpSet.mission_id == mission_id,
            GcpSet.set_id == set_id,
        )
        .first(),
    )
    if gcp_set is None:
        raise HTTPException(status_code=404, detail="GCP set not found")
    return gcp_set


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
    column_profile: Annotated[str, Form(max_length=32)] = "auto",
    column_mapping: Annotated[str | None, Form(max_length=4096)] = None,
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    filename = safe_upload_name(upload.filename)
    payload = await upload.read(MAX_GCP_UPLOAD_BYTES + 1)
    if len(payload) > MAX_GCP_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="GCP upload exceeds 5 MiB")
    try:
        parsed_column_mapping = json.loads(column_mapping) if column_mapping else None
        if parsed_column_mapping is not None and not isinstance(parsed_column_mapping, dict):
            raise ValueError("column_mapping must be a JSON object")
        imported = import_gcp_bytes(
            payload,
            filename,
            source_crs=source_crs,
            default_role=default_role,
            horizontal_accuracy_m=horizontal_accuracy_m,
            vertical_accuracy_m=vertical_accuracy_m,
            image_accuracy_px=image_accuracy_px,
            column_profile=column_profile,
            column_mapping=parsed_column_mapping,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = _authorized_mission(typed_session, vol_id, principal, owner_subject, "gcp_import")
        existing = (
            typed_session.query(GcpSet)
            .filter(
                GcpSet.mission_id == mission.id,
                GcpSet.name == name.strip(),
            )
            .first()
        )
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
            namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                vol_id,
                mission.workspace_prefix,
            )
            positions = load_mission_image_positions(namespace)
            camera_index = load_camera_projection_index(namespace)
            positioned_by_name = {item.image_name: item for item in positions.images} if positions else {}
            cameras_by_name = {item.image_name: item for item in camera_index.cameras} if camera_index else {}
            observation_count = 0
            for imported_point in imported.points:
                point = stored_points[imported_point.external_id]
                observation_specs: list[tuple[CandidateSpec, str, float | None, float | None]]
                if imported_point.observations:
                    observation_specs = [
                        (
                            CandidateSpec(
                                image_name=item.image_name,
                                method="imported-observation",
                                image_width_px=(
                                    cameras_by_name[item.image_name].width
                                    if item.image_name in cameras_by_name
                                    else None
                                ),
                                image_height_px=(
                                    cameras_by_name[item.image_name].height
                                    if item.image_name in cameras_by_name
                                    else None
                                ),
                                positioned=positioned_by_name.get(item.image_name),
                            ),
                            "marked",
                            item.pixel_x,
                            item.pixel_y,
                        )
                        for item in imported_point.observations
                    ]
                elif positions or camera_index:
                    observation_specs = [
                        (candidate, "candidate", None, None)
                        for candidate in _rank_candidate_specs(
                            longitude=imported_point.longitude,
                            latitude=imported_point.latitude,
                            altitude_m=imported_point.altitude_m,
                            positions=positions,
                            camera_index=camera_index,
                            radius_m=candidate_radius_m,
                            limit=max_candidates,
                        )
                    ]
                else:
                    observation_specs = []
                for (
                    candidate,
                    observation_status,
                    pixel_x,
                    pixel_y,
                ) in observation_specs:
                    typed_session.add(
                        _candidate_observation(
                            point,
                            candidate,
                            dataset_prefix=mission.input_dataset,
                            actor_subject=principal.subject,
                            status=observation_status,
                            pixel_x=pixel_x,
                            pixel_y=pixel_y,
                        )
                    )
                    observation_count += 1
            typed_session.flush()
            record_gcp_audit(
                typed_session,
                gcp_set,
                actor_subject=principal.subject,
                action="imported",
                before_state=None,
                after_state={
                    "set_id": gcp_set.set_id,
                    "source_filename": filename,
                    "source_format": imported.source_format,
                    "source_sha256": source_checksum(payload),
                    "point_count": len(stored_points),
                    "observation_count": observation_count,
                },
            )
            typed_session.flush()
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail="GCP import conflicts with stored data") from error
        return {
            "gcp_set": set_json(typed_session, gcp_set, include_points=True),
            "candidate_generation": {
                "available": positions is not None or camera_index is not None,
                "method": (
                    "camera-projection+exif-distance"
                    if camera_index and positions
                    else "camera-projection"
                    if camera_index
                    else "exif-distance"
                    if positions
                    else None
                ),
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
                "observation_count": observation_count,
                "message": (
                    None
                    if positions or camera_index
                    else "Image positions are not published yet; candidates can be refreshed after preflight"
                ),
            },
        }


@router.get("/{vol_id}/gcps")
def list_ground_control(
    vol_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = _authorized_mission(session, vol_id, principal, owner_subject, "gcp_list")
    sets = cast(
        list[GcpSet],
        session.query(GcpSet).filter(GcpSet.mission_id == mission.id).order_by(GcpSet.created_at.desc()).all(),
    )
    features = [point_json(session, point) for gcp_set in sets for point in cast(list[GcpPoint], gcp_set.points)]
    return {
        "type": "FeatureCollection",
        "features": features,
        "gcp_sets": [set_json(session, item, include_points=False) for item in sets],
    }


@router.get("/{vol_id}/gcps/{set_id}")
def ground_control_detail(
    vol_id: str,
    set_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = _authorized_mission(session, vol_id, principal, owner_subject, "gcp_detail")
    gcp_set = _require_gcp_set(session, mission.id, set_id)
    return set_json(session, gcp_set, include_points=True)


@router.post("/{vol_id}/gcps/{set_id}/bundle")
def prepare_ground_control_bundle(
    vol_id: str,
    set_id: str,
    principal: OperatorPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = _authorized_mission(session, vol_id, principal, owner_subject, "gcp_bundle_create")
    gcp_set = _require_gcp_set(session, mission.id, set_id)
    try:
        bundle = materialize_gcp_bundle(gcp_set)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to publish immutable GCP bundle: {error}",
        ) from error
    record_gcp_audit(
        session,
        gcp_set,
        actor_subject=principal.subject,
        action="bundle_materialized",
        before_state=None,
        after_state=bundle,
    )
    session.flush()
    return bundle


@router.post("/{vol_id}/gcps/{set_id}/candidates/refresh")
def refresh_ground_control_candidates(
    vol_id: str,
    set_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    candidate_radius_m: Annotated[float, Query(gt=0, le=10_000)] = 250.0,
    max_candidates: Annotated[int, Query(ge=1, le=100)] = 20,
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    """Add visible/nearby photos without replacing operator decisions."""

    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = _authorized_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject,
            "gcp_candidate_refresh",
        )
        gcp_set = _require_gcp_set(typed_session, mission.id, set_id)
        try:
            namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                vol_id,
                mission.workspace_prefix,
            )
            positions = load_mission_image_positions(namespace)
            camera_index = load_camera_projection_index(namespace)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail=f"Published image positions are invalid: {error}",
            ) from error
        if positions is None and camera_index is None:
            raise HTTPException(
                status_code=409,
                detail="Image positions and registered cameras are not published yet; run reconstruction first",
            )

        added_count = 0
        removed_candidate_count = 0
        for point in cast(list[GcpPoint], gcp_set.points):
            observations = cast(list[GcpObservation], point.observations)
            for observation in observations:
                if observation.status == "candidate":
                    typed_session.delete(observation)
                    removed_candidate_count += 1
        typed_session.flush()
        record_gcp_audit(
            typed_session,
            gcp_set,
            actor_subject=principal.subject,
            action="candidates_refreshed",
            before_state={"candidate_count": removed_candidate_count},
            after_state={
                "candidate_count": added_count,
                "method": (
                    "camera-projection+exif-distance"
                    if camera_index and positions
                    else "camera-projection"
                    if camera_index
                    else "exif-distance"
                ),
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
            },
        )
        typed_session.flush()
        for point in cast(list[GcpPoint], gcp_set.points):
            typed_session.expire(point, ["observations"])
            observations = cast(list[GcpObservation], point.observations)
            existing_names = {cast(str, item.image_name) for item in observations}
            longitude, latitude = point_longitude_latitude(typed_session, point)
            try:
                new_candidates = _rank_candidate_specs(
                    longitude=longitude,
                    latitude=latitude,
                    altitude_m=float(point.altitude_m),
                    positions=positions,
                    camera_index=camera_index,
                    radius_m=candidate_radius_m,
                    limit=max_candidates,
                    existing_image_names=existing_names,
                )
            except ValueError as error:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unable to rank GCP photo candidates: {error}",
                ) from error
            for candidate in new_candidates:
                typed_session.add(
                    _candidate_observation(
                        point,
                        candidate,
                        dataset_prefix=mission.input_dataset,
                        actor_subject=principal.subject,
                    )
                )
                added_count += 1
        typed_session.flush()
        return {
            "gcp_set": set_json(typed_session, gcp_set, include_points=True),
            "candidate_generation": {
                "available": True,
                "method": (
                    "camera-projection+exif-distance"
                    if camera_index and positions
                    else "camera-projection"
                    if camera_index
                    else "exif-distance"
                ),
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
                "added_observation_count": added_count,
                "preserved_operator_observations": True,
            },
        }


@router.patch("/{vol_id}/gcps/points/{point_id}")
def update_ground_control_point(
    vol_id: str,
    point_id: str,
    request: GcpPointUpdate,
    principal: OperatorPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    mission = _authorized_mission(session, vol_id, principal, owner_subject, "gcp_point_update")
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
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = _authorized_mission(
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
                raise HTTPException(status_code=422, detail="Marked GCP pixels are required")
            width = stored_observation.image_width_px
            height = stored_observation.image_height_px
            if width is None or height is None:
                if not stored_observation.image_s3_key:
                    raise HTTPException(
                        status_code=422,
                        detail="The source image is unavailable for pixel-bound validation",
                    )
                try:
                    width, height = read_image_dimensions(stored_observation.image_s3_key)
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


@router.get("/{vol_id}/gcps/{set_id}/audit")
def ground_control_audit(
    vol_id: str,
    set_id: str,
    principal: ViewerPrincipal,
    session: GcpSessionDependency,
    owner_subject: OwnerSubjectQuery = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JsonObject:
    mission = _authorized_mission(session, vol_id, principal, owner_subject, "gcp_audit")
    gcp_set = _require_gcp_set(session, mission.id, set_id)
    events = cast(
        list[GcpAuditEvent],
        session.query(GcpAuditEvent)
        .filter(GcpAuditEvent.gcp_set_id == gcp_set.id)
        .order_by(GcpAuditEvent.created_at.desc(), GcpAuditEvent.id.desc())
        .limit(limit)
        .all(),
    )
    return {
        "set_id": set_id,
        "events": [audit_event_json(event) for event in events],
    }
