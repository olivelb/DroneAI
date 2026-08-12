"""Ground-control file import and photo-candidate refresh routes."""

from __future__ import annotations

import json
from typing import Annotated, cast

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError

from shared.database import GcpObservation, GcpPoint, GcpSet, get_session
from shared.gcp_import import import_gcp_bytes
from shared.tenancy import MissionObjectNamespace

from ..gcp_audit import record_gcp_audit
from ..gcp_candidate_support import (
    CandidateSpec,
    candidate_generation_method,
    candidate_observation,
    rank_candidate_specs,
)
from ..gcp_route_support import (
    OperatorPrincipal,
    OwnerSubjectQuery,
    authorized_mission,
    require_gcp_set,
)
from ..gcp_workspace import (
    MAX_GCP_UPLOAD_BYTES,
    imported_observation_status,
    load_camera_projection_index,
    load_mission_image_positions,
    persist_imported_set,
    point_longitude_latitude,
    safe_upload_name,
    set_json,
    source_checksum,
)
from ..map_support import JsonObject, RouteSession

router = APIRouter()


@router.post("/{vol_id}/gcps/import", status_code=status.HTTP_201_CREATED)
async def import_ground_control(
    vol_id: str,
    principal: OperatorPrincipal,
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
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    filename = safe_upload_name(upload.filename)
    payload = await upload.read(MAX_GCP_UPLOAD_BYTES + 1)
    if len(payload) > MAX_GCP_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="GCP upload exceeds 5 MiB")
    try:
        parsed_column_mapping = json.loads(column_mapping) if column_mapping else None
        if parsed_column_mapping is not None and not isinstance(
            parsed_column_mapping,
            dict,
        ):
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
        mission = authorized_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject,
            "gcp_import",
        )
        existing = (
            typed_session.query(GcpSet)
            .filter(
                GcpSet.mission_id == mission.id,
                GcpSet.name == name.strip(),
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="A GCP set with this name already exists",
            )
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
            positioned_by_name = (
                {item.image_name: item for item in positions.images}
                if positions
                else {}
            )
            cameras_by_name = (
                {item.image_name: item for item in camera_index.cameras}
                if camera_index
                else {}
            )
            observation_count = 0
            imported_marked_count = 0
            imported_unverified_count = 0
            for imported_point in imported.points:
                point = stored_points[imported_point.external_id]
                observation_specs: list[
                    tuple[CandidateSpec, str, float | None, float | None]
                ]
                if imported_point.observations:
                    observation_specs = []
                    for item in imported_point.observations:
                        camera = cameras_by_name.get(item.image_name)
                        width = camera.width if camera else None
                        height = camera.height if camera else None
                        imported_status = imported_observation_status(
                            item.pixel_x,
                            item.pixel_y,
                            width,
                            height,
                        )
                        if imported_status == "marked":
                            imported_marked_count += 1
                        else:
                            imported_unverified_count += 1
                        observation_specs.append(
                            (
                                CandidateSpec(
                                    image_name=item.image_name,
                                    method="imported-observation",
                                    image_width_px=width,
                                    image_height_px=height,
                                    positioned=positioned_by_name.get(item.image_name),
                                ),
                                imported_status,
                                item.pixel_x,
                                item.pixel_y,
                            )
                        )
                elif positions or camera_index:
                    observation_specs = [
                        (candidate, "candidate", None, None)
                        for candidate in rank_candidate_specs(
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
                for candidate, observation_status, pixel_x, pixel_y in observation_specs:
                    typed_session.add(
                        candidate_observation(
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
                    "imported_marked_count": imported_marked_count,
                    "imported_unverified_count": imported_unverified_count,
                },
            )
            typed_session.flush()
        except IntegrityError as error:
            raise HTTPException(
                status_code=409,
                detail="GCP import conflicts with stored data",
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if imported_unverified_count:
            import_message = (
                f"{imported_unverified_count} imported observations "
                "require operator pixel confirmation"
            )
        elif positions or camera_index:
            import_message = None
        else:
            import_message = (
                "Image positions are not published yet; candidates can be "
                "refreshed after preflight"
            )
        return {
            "gcp_set": set_json(typed_session, gcp_set, include_points=True),
            "candidate_generation": {
                "available": positions is not None or camera_index is not None,
                "method": candidate_generation_method(positions, camera_index),
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
                "observation_count": observation_count,
                "imported_marked_count": imported_marked_count,
                "imported_unverified_count": imported_unverified_count,
                "message": import_message,
            },
        }


@router.post("/{vol_id}/gcps/{set_id}/candidates/refresh")
def refresh_ground_control_candidates(
    vol_id: str,
    set_id: str,
    principal: OperatorPrincipal,
    candidate_radius_m: Annotated[float, Query(gt=0, le=10_000)] = 250.0,
    max_candidates: Annotated[int, Query(ge=1, le=100)] = 20,
    owner_subject: OwnerSubjectQuery = None,
) -> JsonObject:
    """Add visible/nearby photos without replacing operator decisions."""

    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = authorized_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject,
            "gcp_candidate_refresh",
        )
        gcp_set = require_gcp_set(typed_session, mission.id, set_id)
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
                detail=(
                    "Image positions and registered cameras are not published yet; "
                    "run reconstruction first"
                ),
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
        for point in cast(list[GcpPoint], gcp_set.points):
            typed_session.expire(point, ["observations"])
            observations = cast(list[GcpObservation], point.observations)
            existing_names = {cast(str, item.image_name) for item in observations}
            longitude, latitude = point_longitude_latitude(typed_session, point)
            try:
                new_candidates = rank_candidate_specs(
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
                    candidate_observation(
                        point,
                        candidate,
                        dataset_prefix=mission.input_dataset,
                        actor_subject=principal.subject,
                    )
                )
                added_count += 1
        typed_session.flush()
        for point in cast(list[GcpPoint], gcp_set.points):
            typed_session.expire(point, ["observations"])
        generation_method = candidate_generation_method(positions, camera_index)
        record_gcp_audit(
            typed_session,
            gcp_set,
            actor_subject=principal.subject,
            action="candidates_refreshed",
            before_state={"candidate_count": removed_candidate_count},
            after_state={
                "candidate_count": added_count,
                "method": generation_method,
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
            },
        )
        typed_session.flush()
        return {
            "gcp_set": set_json(typed_session, gcp_set, include_points=True),
            "candidate_generation": {
                "available": True,
                "method": generation_method,
                "radius_m": candidate_radius_m,
                "max_candidates_per_point": max_candidates,
                "added_observation_count": added_count,
                "preserved_operator_observations": True,
            },
        }
