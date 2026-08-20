"""Mission lifecycle and operational status routes."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, TypedDict, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from shared.cancellation import mark_cancellation_requested
from shared.config import TOPIC_CONTROL, TOPIC_MISSION
from shared.database import Mission, MissionStageRun, get_session
from shared.facade_process import product_process_catalog
from shared.inbox_outbox import enqueue_outbox
from shared.kafka_partitioning import tenant_mission_key
from shared.organization_saas import MANUAL_DELETION_STEP
from shared.pipeline_params import PARAMETER_METADATA, PIPELINE_DEFAULTS
from shared.phase_dag import initialize_stage_runs
from shared.stage_contracts import stage_dag_catalog
from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    mission_prefix,
)
from shared.quality_profiles import (
    DEFAULT_QUALITY_PROFILE_ID,
    profile_overrides_for_new_mission,
    quality_profile_candidates_enabled,
    quality_profile_for_new_mission,
    selectable_quality_profiles,
)
from shared.validation import configured_work_drives
from shared.yolo_capabilities import yolo_model_catalog, yolo_model_manifest
from shared.sam3_capabilities import Sam3Capability, sam3_capability

from ..analysis_support import request_mission_analysis_cancellations
from ..dataset_access import get_owned_dataset
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
    bind_tenant_context,
    require_admin,
    require_authenticated,
    require_operator,
)
from ..stage_orchestrator import stage_jobs_enabled
from .mission_catalog import router as mission_catalog_router
from .mission_stages import router as mission_stages_router

router = APIRouter(
    tags=["missions"],
    dependencies=[Depends(bind_tenant_context)],
)
router.include_router(mission_catalog_router)
router.include_router(mission_stages_router)


class MissionMutationRecord(Protocol):
    id: int
    vol_id: str
    organization_id: str
    workspace_prefix: str | None
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
    deletion_pending: bool


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
    sam3: Sam3Capability
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


def _resolve_owner_for_direct_lookup(
    principal: Principal,
    requested_owner: str | None,
    *,
    action: str,
    vol_id: str | None = None,
) -> str:
    """Persist explicit delegation before helpers open their own DB session."""

    normalized_owner = requested_owner.strip() if requested_owner is not None else None
    durable_delegation = normalized_owner not in {None, "", principal.subject} and principal.role == "admin"
    if durable_delegation:
        with get_session() as audit_session:
            return resolve_owner_subject(
                principal,
                requested_owner,
                action=action,
                vol_id=vol_id,
                audit_session=audit_session,
            )
    return resolve_owner_subject(
        principal,
        requested_owner,
        action=action,
        vol_id=vol_id,
    )


@router.get("/status/summary")
def status_summary(
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> StatusSummary:
    try:
        owner = _resolve_owner_for_direct_lookup(
            principal,
            owner_subject,
            action="summary",
        )
        return get_status_summary(owner, principal.organization_id)
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
        owner = _resolve_owner_for_direct_lookup(
            principal,
            owner_subject,
            action="state",
            vol_id=vol_id,
        )
        return get_mission_state(
            vol_id,
            owner,
            principal.organization_id,
        )
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
            organization_id=mission.organization_id,
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
                organization_id=mission.organization_id,
                attempt=attempt,
            ),
            key=tenant_mission_key(mission.organization_id, vol_id),
        )
    return {
        "status": "success",
        "message": f"Cancel command queued for {vol_id}",
    }


@router.delete(
    "/mission/{vol_id}",
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_202_ACCEPTED,
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
            if mission.current_step != MANUAL_DELETION_STEP and mission.status not in {
                "deleting",
                "deletion_failed",
            }:
                attempt = int(mission.retry_count or 0)
                if not mark_cancellation_requested(
                    session,
                    vol_id=vol_id,
                    attempt=attempt,
                    organization_id=mission.organization_id,
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(f"Mission {vol_id} changed generation before deletion"),
                    )
                now = datetime.now(UTC)
                (
                    session.query(MissionStageRun)
                    .filter(
                        MissionStageRun.mission_id == mission.id,
                        MissionStageRun.status.in_(("queued", "running")),
                    )
                    .update(
                        {
                            MissionStageRun.status: "cancelled",
                            MissionStageRun.completed_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                request_mission_analysis_cancellations(session, mission)
                mission.current_step = MANUAL_DELETION_STEP
                enqueue_outbox(
                    session,
                    topic=TOPIC_CONTROL,
                    event=build_cancel_event(
                        vol_id,
                        organization_id=mission.organization_id,
                        attempt=attempt,
                    ),
                    key=tenant_mission_key(mission.organization_id, vol_id),
                )
    return {
        "status": "success",
        "message": (
            f"Mission {vol_id} deletion queued. Storage will be removed after compute has stopped."
            if mission_exists
            else f"Mission {vol_id} does not exist."
        ),
        "s3_objects_deleted": 0,
        "db_deleted": False,
        "deletion_pending": mission_exists,
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
        owner = _resolve_owner_for_direct_lookup(
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
                principal.organization_id,
            )
            if payload is not None:
                enqueue_outbox(
                    session,
                    topic=TOPIC_MISSION,
                    event=build_resume_event(payload),
                    key=tenant_mission_key(principal.organization_id, vol_id),
                )
    except HTTPException:
        raise
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


@router.get("/mission/parameters")
def mission_parameters() -> MissionParametersResponse:
    work_drives = configured_work_drives()
    configured_names = {drive["name"] for drive in work_drives}
    work_drive_default = os.getenv("WORK_DRIVE_DEFAULT", "").strip()
    if work_drive_default not in configured_names:
        work_drive_default = work_drives[0]["name"] if work_drives else ""
    profiles = selectable_quality_profiles(include_candidates=quality_profile_candidates_enabled())
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "processes": product_process_catalog(),
        "metadata": PARAMETER_METADATA,
        "work_drives": work_drives,
        "work_drive_default": work_drive_default,
        "quality_profiles": [profile.as_api_dict() for profile in profiles],
        "quality_profile_default": DEFAULT_QUALITY_PROFILE_ID,
        "yolo_models": yolo_model_catalog(),
        "sam3": sam3_capability(),
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
    organization_id = getattr(
        principal,
        "organization_id",
        LEGACY_ORGANIZATION_ID,
    )
    try:
        selected_profile = quality_profile_for_new_mission(params.quality_profile)
        requested_profile_overrides = getattr(params, "colmap_params", {})
        effective_profile_parameters = {
            **selected_profile.parameters,
            **requested_profile_overrides,
        }
        selected_profile_overrides = profile_overrides_for_new_mission(
            params.quality_profile,
            requested_profile_overrides,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    try:
        with get_session() as session:
            existing = (
                session.query(Mission)
                .filter(
                    Mission.vol_id == params.vol_id,
                    Mission.organization_id == organization_id,
                )
                .first()
            )
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(f"Mission {params.vol_id} already exists; use the resume endpoint for an existing mission"),
                )
            dataset = get_owned_dataset(
                session,
                principal,
                prefix=params.input_dataset,
                action="launch_mission",
                for_update=True,
            )
            payload = _mission_payload(params)
            payload["owner_subject"] = principal.subject
            payload["organization_id"] = organization_id
            workspace_prefix = mission_prefix(organization_id, params.vol_id)
            payload["workspace_prefix"] = workspace_prefix
            payload["colmap_params"] = effective_profile_parameters
            payload["quality_profile_version"] = selected_profile.version
            payload["quality_profile_overrides"] = selected_profile_overrides
            if params.ai_backend == "yolo":
                payload["ai_model_manifest"] = yolo_model_manifest(params.ai_model_variant)
            mission = Mission(
                vol_id=params.vol_id,
                owner_subject=principal.subject,
                organization_id=organization_id,
                status="pending",
                pipeline=params.pipeline,
                dataset_id=dataset.id,
                input_dataset=params.input_dataset,
                workspace_prefix=workspace_prefix,
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
                    key=tenant_mission_key(organization_id, params.vol_id),
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
