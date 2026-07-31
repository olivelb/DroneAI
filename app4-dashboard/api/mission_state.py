"""Mission persistence, serialization, and resume policy."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from shared.config import SERVICE_ORDER
from shared.database import Mission, MissionLog, get_or_create_mission, get_session

TERMINAL_STATUSES = {"success", "error"}
MISSION_PROCESSING_STALE_SECONDS = float(os.getenv("MISSION_PROCESSING_STALE_SECONDS", "120"))


def apply_mission_state(session, payload: dict) -> None:
    """Apply one validated status event to an existing DB transaction."""
    vol_id = payload.get("vol_id")
    if not vol_id:
        raise ValueError("status event has no vol_id")

    service = payload.get("service") or "UNKNOWN"
    step = payload.get("step")
    progress = payload.get("progress", 0)
    status = payload.get("status", "processing")
    log_message = payload.get("log")
    details = payload.get("details")

    mission = get_or_create_mission(session, vol_id)
    states = dict(mission.service_states or {})
    states[service] = payload
    mission.service_states = states
    mission.current_step = step
    mission.progress = progress
    if status in TERMINAL_STATUSES:
        mission.status = status
    elif mission.status not in TERMINAL_STATUSES:
        mission.status = "processing"
    mission.updated_at = datetime.now(timezone.utc)

    if status == "error" and log_message:
        mission.error_message = log_message
    if details:
        event = details.get("event")
        resume_info = dict(mission.resume_info or {})
        if event in {
            "command_started",
            "command_finished",
            "command_failed",
            "command_cancelled",
        }:
            resume_info["last_command_event"] = details
        elif event == "copy_progress":
            resume_info["copy_progress"] = details
        mission.resume_info = resume_info

    session.add(
        MissionLog(
            mission_id=mission.id,
            vol_id=vol_id,
            service=service,
            step=step,
            status=status,
            progress=progress,
            message=log_message,
            details=details,
        )
    )


def update_mission_state(payload: dict) -> None:
    """Persist one status event using its own transaction."""

    with get_session() as session:
        apply_mission_state(session, payload)


def compute_overall_status(services: dict) -> str:
    if not services:
        return "idle"
    statuses = [
        payload.get("status", "processing") if isinstance(payload, dict) else "processing"
        for payload in services.values()
    ]
    if "error" in statuses:
        return "error"
    if all(status == "success" for status in statuses if status) and all(
        service in services for service in SERVICE_ORDER
    ):
        return "success"
    return "processing"


def is_mission_stale(mission: Mission) -> bool:
    if mission.updated_at is None:
        return False
    elapsed = (datetime.now(timezone.utc) - mission.updated_at).total_seconds()
    return elapsed > MISSION_PROCESSING_STALE_SECONDS


def build_colmap_resume_state(mission: Mission) -> dict:
    services = mission.service_states or {}
    colmap_service = services.get("COLMAP", {})
    colmap_status = colmap_service.get("status") if isinstance(colmap_service, dict) else None
    stale = colmap_status == "processing" and is_mission_stale(mission)
    downstream = [
        name
        for name, service in services.items()
        if name != "COLMAP" and isinstance(service, dict) and service.get("status") == "processing"
    ]

    if colmap_status == "processing" and not stale:
        return {
            "available": False,
            "state": "running",
            "reason": ("COLMAP is currently running. Resume is only relevant after an interruption."),
            "downstream_processing": downstream,
        }
    if stale:
        has_params = mission.params is not None
        return {
            "available": has_params,
            "state": "checkpointed" if has_params else "stale",
            "reason": (
                "The last COLMAP status update is stale. The mission can be resumed."
                if has_params
                else "COLMAP is stale and no saved params found."
            ),
            "downstream_processing": downstream,
        }
    if colmap_status == "success":
        suffix = " Downstream processing can continue." if downstream else ""
        return {
            "available": False,
            "state": "completed",
            "reason": f"COLMAP has already completed for this mission.{suffix}",
            "downstream_processing": downstream,
        }
    if colmap_status == "error":
        has_params = mission.params is not None
        return {
            "available": has_params,
            "state": "resumable" if has_params else "unavailable",
            "reason": (
                "COLMAP stopped with an error. A resume action can restart from the last checkpoint."
                if has_params
                else "COLMAP errored but no saved mission parameters found."
            ),
            "downstream_processing": downstream,
        }
    return {
        "available": False,
        "state": "unavailable",
        "reason": "No COLMAP state found yet.",
        "downstream_processing": downstream,
    }


def serialize_mission(mission: Mission) -> dict:
    services = mission.service_states or {}
    overall = compute_overall_status(services)
    if mission.status in TERMINAL_STATUSES:
        overall = mission.status
    if overall == "processing" and is_mission_stale(mission):
        overall = "error"

    resume_info = mission.resume_info or {}
    workspace_state = {
        "vol_id": mission.vol_id,
        "status": mission.status,
        "step": mission.current_step,
        "progress": mission.progress,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
        "started_at": mission.created_at.isoformat() if mission.created_at else None,
        "last_log": mission.error_message,
        "resume_info": mission.resume_info,
        "mission": mission.params,
        "current_command": resume_info.get("last_command_event"),
        "copy_progress": resume_info.get("copy_progress"),
    }
    return {
        "vol_id": mission.vol_id,
        "workspace_dir": f"missions/{mission.vol_id}",
        "workspace_state": workspace_state,
        "colmap_resume": build_colmap_resume_state(mission),
        "services": services,
        "logs": [],
        "updated_at": (mission.updated_at.timestamp() if mission.updated_at else time.time()),
        "overall_status": overall,
    }


def get_status_summary() -> dict:
    with get_session() as session:
        missions = session.query(Mission).order_by(Mission.updated_at.desc()).limit(50).all()
        serialized = [serialize_mission(mission) for mission in missions]
    serialized.sort(key=lambda item: item["updated_at"], reverse=True)
    active = next(
        (item for item in serialized if item["overall_status"] == "processing"),
        serialized[0] if serialized else None,
    )
    return {
        "active_vol_id": active["vol_id"] if active else None,
        "missions": serialized,
    }


def get_mission_state(vol_id: str) -> dict:
    with get_session() as session:
        mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
        if mission is None:
            return {"vol_id": vol_id, "workspace_state": None}
        return {
            "vol_id": vol_id,
            "workspace_state": serialize_mission(mission)["workspace_state"],
        }


def prepare_resume_in_session(session, vol_id: str) -> tuple[dict | None, dict]:
    mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
    if mission is None:
        return None, {
            "status": "error",
            "message": f"Mission {vol_id} not found.",
        }
    resume_state = build_colmap_resume_state(mission)
    if not resume_state["available"]:
        return None, {
            "status": "error",
            "message": resume_state["reason"],
            "colmap_resume": resume_state,
        }
    if not mission.params:
        return None, {
            "status": "error",
            "message": (f"Saved state for {vol_id} does not contain the original mission payload."),
            "colmap_resume": resume_state,
        }

    payload = dict(mission.params)
    payload["vol_id"] = vol_id
    mission.retry_count = int(mission.retry_count or 0) + 1
    payload["attempt"] = mission.retry_count
    mission.status = "processing"
    mission.current_step = "RESUMING"
    mission.error_message = None
    mission.updated_at = datetime.now(timezone.utc)
    response = {
        "status": "success",
        "message": f"Resume command queued for {vol_id}.",
        "colmap_resume": resume_state,
    }
    return payload, response


def prepare_resume(vol_id: str) -> tuple[dict | None, dict]:
    """Compatibility wrapper using its own transaction."""

    with get_session() as session:
        return prepare_resume_in_session(session, vol_id)
