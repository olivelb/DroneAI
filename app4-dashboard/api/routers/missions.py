"""Mission lifecycle and operational status routes."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter

from shared import storage
from shared.database import Mission, get_or_create_mission, get_session
from shared.pipeline_params import PARAMETER_METADATA, PIPELINE_DEFAULTS

from ..kubernetes_status import get_pod_states
from ..messaging import publish_cancel, publish_new_mission, publish_resume
from ..mission_state import (
    get_mission_state,
    get_status_summary,
    prepare_resume,
)
from ..schemas import MissionParams


router = APIRouter(tags=["missions"])


@router.get("/status/summary")
def status_summary():
    try:
        return get_status_summary()
    except Exception as error:
        return {"active_vol_id": None, "missions": [], "error": str(error)}


@router.get("/mission/state")
def mission_state(vol_id: str):
    try:
        return get_mission_state(vol_id)
    except Exception as error:
        return {
            "vol_id": vol_id,
            "workspace_state": None,
            "error": str(error),
        }


@router.post("/mission/cancel")
async def cancel_mission(vol_id: str):
    publish_cancel(vol_id)
    return {
        "status": "success",
        "message": f"Cancel command sent for {vol_id}",
    }


@router.delete("/mission/{vol_id}")
def delete_mission(vol_id: str):
    deleted_count = 0
    try:
        deleted_count = storage.delete_prefix(f"missions/{vol_id}/")
    except Exception as error:
        return {
            "status": "error",
            "message": f"S3 delete failed: {error}",
            "s3_objects_deleted": 0,
            "db_deleted": False,
        }

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
        return {
            "status": "error",
            "message": (
                f"S3 cleaned ({deleted_count} objects) but DB delete failed: "
                f"{error}"
            ),
            "s3_objects_deleted": deleted_count,
            "db_deleted": False,
        }
    return {
        "status": "success",
        "message": f"Mission {vol_id} deleted.",
        "s3_objects_deleted": deleted_count,
        "db_deleted": mission is not None,
    }


@router.post("/mission/resume")
async def resume_mission(vol_id: str):
    try:
        payload, response = prepare_resume(vol_id)
    except Exception as error:
        return {"status": "error", "message": str(error)}
    if payload is None:
        return response
    publish_resume(payload)
    return response


@router.get("/pods")
def pod_statuses():
    return get_pod_states()


@router.get("/mission/parameters")
def mission_parameters():
    try:
        work_drives = json.loads(os.getenv("WORK_DRIVES", "") or "[]")
    except json.JSONDecodeError:
        work_drives = []
    return {
        "pipelines": PIPELINE_DEFAULTS,
        "metadata": PARAMETER_METADATA,
        "work_drives": work_drives,
        "work_drive_default": os.getenv("WORK_DRIVE_DEFAULT", ""),
    }


@router.post("/mission")
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
    except Exception as error:
        return {
            "status": "error",
            "vol_id": params.vol_id,
            "message": f"Failed to persist mission: {error}",
        }
    publish_new_mission(payload)
    return {"status": "success", "vol_id": params.vol_id}
