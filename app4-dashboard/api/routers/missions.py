"""Mission lifecycle and operational status routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status

from shared import storage
from shared.config import TOPIC_CONTROL, TOPIC_MISSION
from shared.database import Mission, get_or_create_mission, get_session
from shared.facade_process import product_process_catalog
from shared.inbox_outbox import enqueue_outbox
from shared.pipeline_params import PARAMETER_METADATA, PIPELINE_DEFAULTS
from shared.validation import configured_work_drives

from ..kubernetes_status import get_pod_states
from ..messaging import (
    build_cancel_event,
    build_new_mission_event,
    build_resume_event,
)
from ..mission_state import (
    get_mission_state,
    get_status_summary,
    prepare_resume_in_session,
)
from ..schemas import MissionParams
from ..security import (
    require_admin,
    require_authenticated,
    require_operator,
)

router = APIRouter(
    tags=["missions"],
    dependencies=[Depends(require_authenticated)],
)


@router.get("/status/summary")
def status_summary():
    try:
        return get_status_summary()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Mission status storage unavailable: {error}",
        ) from error


@router.get("/mission/state")
def mission_state(vol_id: str):
    try:
        return get_mission_state(vol_id)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Mission state unavailable: {error}",
        ) from error


@router.post(
    "/mission/cancel",
    dependencies=[Depends(require_operator)],
)
async def cancel_mission(vol_id: str):
    with get_session() as session:
        mission = (
            session.query(Mission)
            .filter(Mission.vol_id == vol_id)
            .with_for_update()
            .first()
        )
        if mission is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mission {vol_id} not found",
            )
        enqueue_outbox(
            session,
            topic=TOPIC_CONTROL,
            event=build_cancel_event(
                vol_id,
                attempt=int(mission.retry_count or 0),
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
def delete_mission(vol_id: str):
    mission_exists = False
    with get_session() as session:
        mission = (
            session.query(Mission)
            .filter(Mission.vol_id == vol_id)
            .with_for_update()
            .first()
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
                mission = (
                    session.query(Mission)
                    .filter(Mission.vol_id == vol_id)
                    .with_for_update()
                    .first()
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
            mission = (
                session.query(Mission)
                .filter(Mission.vol_id == vol_id)
                .first()
            )
            if mission:
                session.delete(mission)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"S3 cleaned ({deleted_count} objects) but DB delete failed: "
                f"{error}"
            ),
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
async def resume_mission(vol_id: str):
    try:
        with get_session() as session:
            payload, response = prepare_resume_in_session(session, vol_id)
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
def pod_statuses():
    return get_pod_states()


@router.get("/mission/parameters")
def mission_parameters():
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
    }


@router.post(
    "/mission",
    dependencies=[Depends(require_operator)],
)
async def start_mission(params: MissionParams):
    payload = params.model_dump()
    try:
        with get_session() as session:
            get_or_create_mission(
                session,
                params.vol_id,
                status="pending",
                pipeline=params.pipeline,
                input_dataset=params.input_dataset,
                workspace_prefix=f"missions/{params.vol_id}",
                params=payload,
            )
            enqueue_outbox(
                session,
                topic=TOPIC_MISSION,
                event=build_new_mission_event(payload),
                key=params.vol_id,
            )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to persist mission: {error}",
        ) from error
    return {"status": "success", "vol_id": params.vol_id}
