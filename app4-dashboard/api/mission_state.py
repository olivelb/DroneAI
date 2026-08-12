"""Mission persistence, serialization, and resume policy."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, NotRequired, Protocol, TypedDict, cast

from shared.config import SERVICE_ORDER
from shared.database import Mission, MissionLog, get_or_create_mission, get_session
from shared.phase_dag import project_status_to_stage_run
from shared.tenancy import LEGACY_ORGANIZATION_ID, MissionObjectNamespace

TERMINAL_STATUSES = {"success", "error", "cancelled"}
MISSION_PROCESSING_STALE_SECONDS = float(os.getenv("MISSION_PROCESSING_STALE_SECONDS", "120"))


JsonObject = dict[str, Any]


class QueryProtocol(Protocol):
    """Minimal legacy SQLAlchemy query surface used by this service."""

    def filter(self, *criteria: Any) -> QueryProtocol: ...

    def order_by(self, *criteria: Any) -> QueryProtocol: ...

    def limit(self, value: int) -> QueryProtocol: ...

    def first(self) -> Any: ...

    def all(self) -> list[Any]: ...


class SessionProtocol(Protocol):
    """Transaction boundary required by the mission state service."""

    def add(self, instance: Any) -> None: ...

    def query(self, *entities: Any) -> QueryProtocol: ...


class MissionRecord(Protocol):
    id: int
    vol_id: str
    organization_id: str
    owner_subject: str
    workspace_prefix: str | None
    status: str
    current_step: str | None
    progress: int
    retry_count: int | None
    service_states: JsonObject | None
    resume_info: JsonObject | None
    params: JsonObject | None
    error_message: str | None
    created_at: datetime | None
    updated_at: datetime | None


class ColmapResumeState(TypedDict):
    available: bool
    state: str
    reason: str
    downstream_processing: list[str]


class WorkspaceState(TypedDict):
    vol_id: str
    status: str
    step: str | None
    progress: int
    updated_at: str | None
    started_at: str | None
    last_log: str | None
    resume_info: JsonObject | None
    mission: JsonObject | None
    current_command: JsonObject | None
    copy_progress: JsonObject | None


class SerializedMission(TypedDict):
    vol_id: str
    owner_subject: str
    workspace_dir: str
    workspace_state: WorkspaceState
    colmap_resume: ColmapResumeState
    services: JsonObject
    logs: list[JsonObject]
    updated_at: float
    overall_status: str
    is_stale: bool
    last_event_age_seconds: float | None


class StatusSummary(TypedDict):
    active_vol_id: str | None
    missions: list[SerializedMission]


class MissionStateResult(TypedDict):
    vol_id: str
    workspace_state: WorkspaceState | None


class ResumeResponse(TypedDict):
    status: str
    message: str
    colmap_resume: NotRequired[ColmapResumeState]


def apply_mission_state(session: SessionProtocol, payload: JsonObject) -> None:
    """Apply one validated status event to an existing DB transaction."""
    vol_id = payload.get("vol_id")
    if not isinstance(vol_id, str) or not vol_id:
        raise ValueError("status event has no vol_id")

    service = str(payload.get("service") or "UNKNOWN")
    step = cast(str | None, payload.get("step"))
    progress = cast(int, payload.get("progress", 0))
    event_status = str(payload.get("status", "processing"))
    log_message = cast(str | None, payload.get("log"))
    raw_details = payload.get("details")
    details = cast(JsonObject, raw_details) if isinstance(raw_details, dict) else None

    mission = cast(MissionRecord, get_or_create_mission(session, vol_id))
    states: JsonObject = dict(mission.service_states or {})
    states[service] = payload
    mission.service_states = states
    mission.current_step = step
    mission.progress = progress
    overall_status = compute_overall_status(states)
    if overall_status in TERMINAL_STATUSES:
        mission.status = overall_status
    elif (
        mission.status == "error" and event_status == "processing"
    ) or mission.status not in TERMINAL_STATUSES:
        mission.status = "processing"
    mission.updated_at = datetime.now(UTC)

    if event_status == "error" and log_message:
        mission.error_message = log_message
    elif (
        overall_status == "success"
        or event_status == "cancelled"
        or (event_status == "processing" and overall_status != "error")
    ):
        mission.error_message = None
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

    if isinstance(mission, Mission):
        project_status_to_stage_run(
            cast(Any, session),
            mission,
            service=service,
            step=step,
            event_status=event_status,
            progress=progress,
            error_message=log_message,
            stage_run_id=cast(str | None, payload.get("stage_run_id")),
        )

    session.add(
        MissionLog(
            mission_id=mission.id,
            vol_id=vol_id,
            service=service,
            step=step,
            status=event_status,
            progress=progress,
            message=log_message,
            details=details,
        )
    )


def update_mission_state(payload: JsonObject) -> None:
    """Persist one status event using its own transaction."""

    with get_session() as session:
        apply_mission_state(session, payload)


def compute_overall_status(services: Mapping[str, object]) -> str:
    if not services:
        return "idle"
    statuses: list[str] = []
    for payload in services.values():
        if not isinstance(payload, dict):
            statuses.append("processing")
            continue
        raw_status = payload.get("status", "processing")
        statuses.append(raw_status if isinstance(raw_status, str) else "processing")
    if "error" in statuses:
        return "error"
    if "cancelled" in statuses:
        return "cancelled"
    colmap = services.get("COLMAP")
    if isinstance(colmap, dict):
        details = colmap.get("details")
        if (
            colmap.get("status") == "success"
            and isinstance(details, dict)
            and details.get("process") == "facade"
            and details.get("terminal") is True
        ):
            return "success"
    if all(status == "success" for status in statuses if status) and all(
        service in services for service in SERVICE_ORDER
    ):
        return "success"
    return "processing"


def is_mission_stale(mission: MissionRecord) -> bool:
    if mission.updated_at is None:
        return False
    elapsed = (datetime.now(UTC) - mission.updated_at).total_seconds()
    return elapsed > MISSION_PROCESSING_STALE_SECONDS


def mission_event_age_seconds(mission: MissionRecord) -> float | None:
    """Return event age as monitoring metadata, never as a pipeline failure."""

    if mission.updated_at is None:
        return None
    updated_at = mission.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - updated_at).total_seconds())


def build_colmap_resume_state(mission: MissionRecord) -> ColmapResumeState:
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
    if colmap_status == "cancelled":
        has_params = mission.params is not None
        return {
            "available": has_params,
            "state": "cancelled" if has_params else "unavailable",
            "reason": (
                "COLMAP was cancelled by an operator. The mission can be restarted as a new attempt."
                if has_params
                else "COLMAP was cancelled but no saved mission parameters were found."
            ),
            "downstream_processing": downstream,
        }
    return {
        "available": False,
        "state": "unavailable",
        "reason": "No COLMAP state found yet.",
        "downstream_processing": downstream,
    }


def serialize_mission(mission: MissionRecord) -> SerializedMission:
    services = mission.service_states or {}
    overall = compute_overall_status(services)
    if mission.status in TERMINAL_STATUSES:
        overall = mission.status
    event_age = mission_event_age_seconds(mission)
    stale = (
        overall == "processing"
        and event_age is not None
        and event_age > MISSION_PROCESSING_STALE_SECONDS
    )

    resume_info = mission.resume_info or {}
    current_command = resume_info.get("last_command_event")
    copy_progress = resume_info.get("copy_progress")
    workspace_state: WorkspaceState = {
        "vol_id": mission.vol_id,
        "status": mission.status,
        "step": mission.current_step,
        "progress": mission.progress,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
        "started_at": mission.created_at.isoformat() if mission.created_at else None,
        "last_log": mission.error_message,
        "resume_info": mission.resume_info,
        "mission": mission.params,
        "current_command": (cast(JsonObject, current_command) if isinstance(current_command, dict) else None),
        "copy_progress": (cast(JsonObject, copy_progress) if isinstance(copy_progress, dict) else None),
    }
    return {
        "vol_id": mission.vol_id,
        "owner_subject": mission.owner_subject,
        "workspace_dir": MissionObjectNamespace.from_binding(
            mission.organization_id,
            mission.vol_id,
            mission.workspace_prefix,
        ).root,
        "workspace_state": workspace_state,
        "colmap_resume": build_colmap_resume_state(mission),
        "services": services,
        "logs": [],
        "updated_at": (mission.updated_at.timestamp() if mission.updated_at else time.time()),
        "overall_status": overall,
        "is_stale": stale,
        "last_event_age_seconds": event_age,
    }


def get_status_summary(
    owner_subject: str,
    organization_id: str = LEGACY_ORGANIZATION_ID,
) -> StatusSummary:
    with get_session() as session:
        missions = cast(
            list[MissionRecord],
            session.query(Mission)
            .filter(
                Mission.organization_id == organization_id,
                Mission.owner_subject == owner_subject,
            )
            .order_by(Mission.updated_at.desc())
            .limit(50)
            .all(),
        )
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


def get_mission_state(
    vol_id: str,
    owner_subject: str,
    organization_id: str = LEGACY_ORGANIZATION_ID,
) -> MissionStateResult:
    with get_session() as session:
        mission = cast(
            MissionRecord | None,
            session.query(Mission)
            .filter(
                Mission.vol_id == vol_id,
                Mission.owner_subject == owner_subject,
                Mission.organization_id == organization_id,
            )
            .first(),
        )
        if mission is None:
            return {"vol_id": vol_id, "workspace_state": None}
        return {
            "vol_id": vol_id,
            "workspace_state": serialize_mission(mission)["workspace_state"],
        }


def prepare_resume_in_session(
    session: SessionProtocol,
    vol_id: str,
    owner_subject: str,
    organization_id: str = LEGACY_ORGANIZATION_ID,
) -> tuple[JsonObject | None, ResumeResponse]:
    mission = cast(
        MissionRecord | None,
        session.query(Mission)
        .filter(
            Mission.vol_id == vol_id,
            Mission.owner_subject == owner_subject,
            Mission.organization_id == organization_id,
        )
        .first(),
    )
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
    namespace = MissionObjectNamespace.from_binding(
        mission.organization_id,
        mission.vol_id,
        mission.workspace_prefix,
    )
    payload["organization_id"] = namespace.organization_id
    payload["workspace_prefix"] = namespace.root
    mission.retry_count = int(mission.retry_count or 0) + 1
    payload["attempt"] = mission.retry_count
    mission.status = "processing"
    mission.current_step = "RESUMING"
    mission.error_message = None
    mission.updated_at = datetime.now(UTC)
    response: ResumeResponse = {
        "status": "success",
        "message": f"Resume command queued for {vol_id}.",
        "colmap_resume": resume_state,
    }
    return payload, response


def prepare_resume(
    vol_id: str,
    owner_subject: str,
    organization_id: str = LEGACY_ORGANIZATION_ID,
) -> tuple[JsonObject | None, ResumeResponse]:
    """Compatibility wrapper using its own transaction."""

    with get_session() as session:
        return prepare_resume_in_session(
            session,
            vol_id,
            owner_subject,
            organization_id,
        )
