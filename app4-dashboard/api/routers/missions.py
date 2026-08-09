"""Mission lifecycle and operational status routes."""

from __future__ import annotations

import os
from typing import Annotated, Any, Protocol, TypedDict, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from shared import storage
from shared.cancellation import mark_cancellation_requested
from shared.config import TOPIC_CONTROL, TOPIC_MISSION
from shared.database import Mission, get_session
from shared.facade_process import product_process_catalog
from shared.inbox_outbox import enqueue_outbox
from shared.pipeline_params import PARAMETER_METADATA, PIPELINE_DEFAULTS
from shared.phase_dag import initialize_stage_runs
from shared.stage_contracts import stage_dag_catalog
from shared.quality_profiles import (
    DEFAULT_QUALITY_PROFILE_ID,
    QUALITY_PROFILES,
    profile_overrides,
    quality_profile,
)
from shared.validation import configured_work_drives
from shared.yolo_capabilities import yolo_model_catalog, yolo_model_manifest

from ..kubernetes_status import KubernetesStatus, get_pod_states
from ..mission_access import mission_query, resolve_owner_subject
from ..messaging import (
    build_cancel_event,
    build_new_mission_event,
    build_resume_event,
)
from ..mission_state import (
    MissionStateResult,
    ResumeResponse,
    StatusSummary,
    get_mission_state,
    get_status_summary,
    prepare_resume_in_session,
)
from ..schemas import MissionParams
from ..security import (
    Principal,
    require_admin,
    require_authenticated,
    require_operator,
)
from ..stage_orchestrator import stage_jobs_enabled
from .mission_catalog import router as mission_catalog_router
from .mission_stages import router as mission_stages_router

router = APIRouter(
    tags=["missions"],
    dependencies=[Depends(require_authenticated)],
)
router.include_router(mission_catalog_router)
router.include_router(mission_stages_router)


class MissionMutationRecord(Protocol):
    retry_count: int | None
    status: str
    current_step: str | None
    error_message: str | None


class CommandResponse(TypedDict):
    status: str
    message: str


class DeleteMissionResponse(CommandResponse):
    s3_objects_deleted: int
    db_deleted: bool


class StartMissionResponse(TypedDict):
    status: str
    vol_id: str


class MissionParametersResponse(TypedDict):
    pipelines: dict[str, dict[str, Any]]
    processes: list[dict[str, Any]]
    metadata: dict[str, dict[str, Any]]
    work_drives: list[dict[str, str]]
    work_drive_default: str
    quality_profiles: list[dict[str, Any]]
    quality_profile_default: str
    yolo_models: list[dict[str, object]]
    stage_dag: dict[str, Any]


def _mission_payload(params: MissionParams) -> dict[str, Any]:
    payload = params.model_dump()
    if params.ai_backend == "sam3":
        payload.pop("ai_model_variant", None)
    return payload


def _find_mission(
    session: Any,
    vol_id: str,
    principal: Principal,
    *,
    requested_owner: str | None = None,
    action: str = "read",
    for_update: bool = False,
) -> MissionMutationRecord | None:
    query = mission_query(
        session,
        principal,
        requested_owner=requested_owner,
        action=action,
        vol_id=vol_id,
    ).filter(Mission.vol_id == vol_id)
    if for_update:
        query = query.with_for_update()
    return cast(MissionMutationRecord | None, query.first())


@router.get("/status/summary")
def status_summary(
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> StatusSummary:
    try:
        owner = resolve_owner_subject(
            principal,
            owner_subject,
            action="summary",
        )
        return get_status_summary(owner)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Mission status storage unavailable: {error}",
        ) from error


@router.get("/mission/state")
def mission_state(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> MissionStateResult:
    try:
        owner = resolve_owner_subject(
            principal,
            owner_subject,
            action="state",
            vol_id=vol_id,
        )
        return get_mission_state(vol_id, owner)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Mission state unavailable: {error}",
        ) from error


@router.post(
    "/mission/cancel",
    dependencies=[Depends(require_operator)],
)
def cancel_mission(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> CommandResponse:
    return _cancel_mission(vol_id, principal, owner_subject)


def _cancel_mission(
    vol_id: str,
    principal: Principal,
    owner_subject: str | None = None,
) -> CommandResponse:
    with get_session() as session:
        mission = _find_mission(
            session,
            vol_id,
            principal,
            requested_owner=owner_subject,
            action="cancel",
            for_update=True,
        )
        if mission is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mission {vol_id} not found",
            )
        attempt = int(mission.retry_count or 0)
        if not mark_cancellation_requested(
            session,
            vol_id=vol_id,
            attempt=attempt,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mission {vol_id} changed generation before cancellation",
            )
        enqueue_outbox(
            session,
            topic=TOPIC_CONTROL,
            event=build_cancel_event(
                vol_id,
                attempt=attempt,
            ),
            key=vol_id,
        )
    return {
        "status": "success",
        "message": f"Cancel command queued for {vol_id}",
    }


@router.delete(
    "/mission/{vol_id}",
    dependencies=[Depends(require_admin)],
)
def delete_mission(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_admin)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> DeleteMissionResponse:
    return _delete_mission(vol_id, principal, owner_subject)


def _delete_mission(
    vol_id: str,
    principal: Principal,
    owner_subject: str | None = None,
) -> DeleteMissionResponse:
    mission_exists = False
    with get_session() as session:
        mission = _find_mission(
            session,
            vol_id,
            principal,
            requested_owner=owner_subject,
            action="delete",
            for_update=True,
        )
        if mission is not None:
            mission_exists = True
            mission.status = "deleting"
            mission.current_step = "DELETING"
            mission.error_message = None

    deleted_count = 0
    try:
        deleted_count = storage.delete_prefix(f"missions/{vol_id}/")
    except Exception as error:
        if mission_exists:
            with get_session() as session:
                mission = _find_mission(
                    session,
                    vol_id,
                    principal,
                    requested_owner=owner_subject,
                    action="delete_failure",
                    for_update=True,
                )
                if mission is not None:
                    mission.status = "deletion_failed"
                    mission.current_step = "DELETION_FAILED"
                    mission.error_message = f"S3 delete failed: {error}"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"S3 delete failed: {error}",
        ) from error

    try:
        with get_session() as session:
            mission = _find_mission(
                session,
                vol_id,
                principal,
                requested_owner=owner_subject,
                action="delete_commit",
            )
            if mission:
                session.delete(mission)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(f"S3 cleaned ({deleted_count} objects) but DB delete failed: {error}"),
        ) from error
    return {
        "status": "success",
        "message": f"Mission {vol_id} deleted.",
        "s3_objects_deleted": deleted_count,
        "db_deleted": mission_exists,
    }


@router.post(
    "/mission/resume",
    dependencies=[Depends(require_operator)],
)
def resume_mission(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> ResumeResponse:
    return _resume_mission(vol_id, principal, owner_subject)


def _resume_mission(
    vol_id: str,
    principal: Principal,
    owner_subject: str | None = None,
) -> ResumeResponse:
    try:
        owner = resolve_owner_subject(
            principal,
            owner_subject,
            action="resume",
            vol_id=vol_id,
        )
        with get_session() as session:
            payload, response = prepare_resume_in_session(
                session,
                vol_id,
                owner,
            )
            if payload is not None:
                enqueue_outbox(
                    session,
                    topic=TOPIC_MISSION,
                    event=build_resume_event(payload),
                    key=vol_id,
                )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to queue mission resume: {error}",
        ) from error
    if payload is None:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in response.get("message", "").lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=code,
            detail=response.get("message", "Mission cannot be resumed"),
        )
    return response


@router.get("/pods")
def pod_statuses() -> KubernetesStatus:
    return get_pod_states()


@router.get("/mission/parameters")
def mission_parameters() -> MissionParametersResponse:
    work_drives = configured_work_drives()
    configured_names = {drive["name"] for drive in work_drives}
    work_drive_default = os.getenv("WORK_DRIVE_DEFAULT", "").strip()
    if work_drive_default not in configured_names:
        work_drive_default = work_drives[0]["name"] if work_drives else ""
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "processes": product_process_catalog(),
        "metadata": PARAMETER_METADATA,
        "work_drives": work_drives,
        "work_drive_default": work_drive_default,
        "quality_profiles": [profile.as_api_dict() for profile in QUALITY_PROFILES],
        "quality_profile_default": DEFAULT_QUALITY_PROFILE_ID,
        "yolo_models": yolo_model_catalog(),
        "stage_dag": stage_dag_catalog(),
    }


@router.post(
    "/mission",
    dependencies=[Depends(require_operator)],
)
def start_mission(
    params: MissionParams,
    principal: Annotated[Principal, Depends(require_operator)],
) -> StartMissionResponse:
    return _start_mission(params, principal)


def _start_mission(
    params: MissionParams,
    principal: Principal,
) -> StartMissionResponse:
    try:
        with get_session() as session:
            existing = session.query(Mission).filter(Mission.vol_id == params.vol_id).first()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(f"Mission {params.vol_id} already exists; use the resume endpoint for an existing mission"),
                )
            payload = _mission_payload(params)
            payload["owner_subject"] = principal.subject
            selected_profile = quality_profile(params.quality_profile)
            payload["colmap_params"] = {
                **selected_profile.parameters,
                **params.colmap_params,
            }
            payload["quality_profile_version"] = selected_profile.version
            payload["quality_profile_overrides"] = profile_overrides(
                params.quality_profile,
                params.colmap_params,
            )
            if params.ai_backend == "yolo":
                payload["ai_model_manifest"] = yolo_model_manifest(
                    params.ai_model_variant
                )
            mission = Mission(
                vol_id=params.vol_id,
                owner_subject=principal.subject,
                status="pending",
                pipeline=params.pipeline,
                input_dataset=params.input_dataset,
                workspace_prefix=f"missions/{params.vol_id}",
                params=payload,
            )
            session.add(mission)
            session.flush()
            initialize_stage_runs(session, mission, payload)
            if not stage_jobs_enabled():
                enqueue_outbox(
                    session,
                    topic=TOPIC_MISSION,
                    event=build_new_mission_event(payload),
                    key=params.vol_id,
                )
    except HTTPException:
        raise
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Mission {params.vol_id} already exists; choose a new mission ID or use resume"),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to persist mission: {error}",
        ) from error
    return {"status": "success", "vol_id": params.vol_id}
