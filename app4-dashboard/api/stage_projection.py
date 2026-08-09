"""Read-only operator projection for the bounded stage DAG."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, TypedDict, cast

from shared.stage_contracts import STAGE_ORDER

from .mission_state import MISSION_PROCESSING_STALE_SECONDS


class StageMissionProjection(TypedDict):
    status: str
    current_step: str | None
    progress: int
    updated_at: datetime | None
    overall_status: str
    is_stale: bool
    last_event_age_seconds: float | None


def operator_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Hide backend-inapplicable choices without mutating durable provenance."""
    normalized = dict(parameters)
    if normalized.get("ai_backend") == "sam3":
        normalized.pop("ai_model_variant", None)
    ai = normalized.get("ai")
    if isinstance(ai, dict) and ai.get("backend") == "sam3":
        normalized["ai"] = {
            key: value for key, value in ai.items() if key != "model_variant"
        }
    return normalized


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _run_timestamp(run: Any) -> datetime | None:
    values = (
        _aware(getattr(run, "heartbeat_at", None)),
        _aware(getattr(run, "completed_at", None)),
        _aware(getattr(run, "started_at", None)),
        _aware(getattr(run, "scheduled_at", None)),
        _aware(getattr(run, "updated_at", None)),
    )
    return max((value for value in values if value is not None), default=None)


def _latest_runs(runs: Iterable[Any]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for run in runs:
        stage = str(run.stage)
        current = latest.get(stage)
        if current is None or (int(run.attempt), int(run.id)) > (
            int(current.attempt),
            int(current.id),
        ):
            latest[stage] = run
    return latest


def project_stage_mission(mission: Any, runs: Iterable[Any]) -> StageMissionProjection | None:
    """Project current mission state from the latest attempt of each stage."""
    latest = _latest_runs(runs)
    if not latest:
        return None

    parameters = cast(dict[str, Any], mission.params or {})
    requested = [
        str(stage)
        for stage in parameters.get("phases", STAGE_ORDER)
        if str(stage) in latest
    ]
    stages = requested or [stage for stage in STAGE_ORDER if stage in latest]
    selected = [latest[stage] for stage in stages]
    statuses = [str(run.status) for run in selected]

    if str(mission.status) == "cancelled" or "cancelled" in statuses:
        overall = "cancelled"
    elif "failed" in statuses:
        overall = "error"
    elif statuses and all(status == "succeeded" for status in statuses):
        overall = "success"
    else:
        overall = "processing"

    weighted_progress = [
        100 if str(run.status) == "succeeded" else int(run.progress or 0)
        for run in selected
    ]
    progress = round(sum(weighted_progress) / len(weighted_progress))

    # Keep genuine activity first, but surface a terminal failure before the
    # downstream stages it has blocked.  Otherwise an operator sees the
    # consequence (for example detection blocked) instead of the root cause
    # (rasterization failed).
    active = next(
        (
            run
            for status in ("running", "failed", "cancelled", "queued", "blocked")
            for run in selected
            if str(run.status) == status
        ),
        selected[-1],
    )
    active_status = str(active.status)
    active_step = (
        active_status
        if active_status in {"failed", "cancelled", "succeeded"}
        else str(active.current_step or active_status)
    ).upper()
    current_step = f"{str(active.stage).upper()} · {active_step}"

    updated_at = max(
        (
            timestamp
            for run in selected
            if (timestamp := _run_timestamp(run)) is not None
        ),
        default=_aware(getattr(mission, "updated_at", None)),
    )
    age = (
        max(0.0, (datetime.now(UTC) - updated_at).total_seconds())
        if updated_at is not None
        else None
    )
    return {
        "status": overall,
        "current_step": current_step,
        "progress": progress,
        "updated_at": updated_at,
        "overall_status": overall,
        "is_stale": (
            overall == "processing"
            and age is not None
            and age > MISSION_PROCESSING_STALE_SECONDS
        ),
        "last_event_age_seconds": age,
    }


def stage_lifecycle_logs(runs: Iterable[Any]) -> list[dict[str, Any]]:
    """Expose durable stage transitions as concise operator log entries."""
    entries: list[dict[str, Any]] = []
    for run in runs:
        stage = str(run.stage)
        attempt = int(run.attempt)
        if run.started_at is not None:
            entries.append(
                {
                    "service": "STAGE",
                    "step": stage,
                    "status": "processing",
                    "progress": int(run.progress or 0),
                    "message": f"{stage} attempt {attempt} started",
                    "details": {
                        "run_id": str(run.run_id),
                        "executor": run.executor,
                        "job_name": run.job_name,
                    },
                    "created_at": _aware(run.started_at),
                }
            )
        if run.completed_at is not None:
            terminal_status = str(run.status)
            entries.append(
                {
                    "service": "STAGE",
                    "step": stage,
                    "status": {
                        "succeeded": "success",
                        "failed": "error",
                    }.get(terminal_status, terminal_status),
                    "progress": int(run.progress or 0),
                    "message": (
                        f"{stage} attempt {attempt} {terminal_status}"
                        + (f": {run.error_message}" if run.error_message else "")
                    ),
                    "details": {
                        "run_id": str(run.run_id),
                        "executor": run.executor,
                        "job_name": run.job_name,
                    },
                    "created_at": _aware(run.completed_at),
                }
            )
    return sorted(entries, key=lambda entry: entry["created_at"] or datetime.min.replace(tzinfo=UTC))
