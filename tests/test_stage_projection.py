from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


projection = importlib.import_module("app4-dashboard.api.stage_projection")


def _run(
    identifier: int,
    stage: str,
    status: str,
    *,
    progress: int = 0,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
):
    timestamp = completed_at or started_at or datetime.now(UTC)
    return SimpleNamespace(
        id=identifier,
        run_id=f"run-{identifier}",
        stage=stage,
        attempt=0,
        status=status,
        progress=progress,
        current_step="EXECUTING" if status == "running" else None,
        executor="kubernetes-job",
        job_name=f"job-{identifier}",
        error_message="boom" if status == "failed" else None,
        heartbeat_at=timestamp,
        started_at=started_at,
        completed_at=completed_at,
        scheduled_at=timestamp,
        updated_at=timestamp,
    )


def test_stage_projection_drives_live_operator_status_and_progress():
    now = datetime.now(UTC)
    mission = SimpleNamespace(
        status="pending",
        params={
            "phases": [
                "reconstruction",
                "gaussian_training",
                "gaussian_filtering",
                "rasterization",
                "detection",
            ]
        },
        updated_at=now - timedelta(hours=1),
    )
    runs = [
        _run(1, "reconstruction", "succeeded", progress=100, completed_at=now),
        _run(2, "gaussian_training", "succeeded", progress=100, completed_at=now),
        _run(3, "gaussian_filtering", "running", progress=50, started_at=now),
        _run(4, "rasterization", "blocked"),
        _run(5, "detection", "blocked"),
    ]

    result = projection.project_stage_mission(mission, runs)

    assert result is not None
    assert result["overall_status"] == "processing"
    assert result["progress"] == 50
    assert result["current_step"] == "GAUSSIAN_FILTERING · EXECUTING"
    assert result["is_stale"] is False


def test_stage_projection_reports_failure_and_lifecycle_logs():
    now = datetime.now(UTC)
    mission = SimpleNamespace(
        status="pending",
        params={"phases": ["rasterization"]},
        updated_at=now,
    )
    run = _run(
        1,
        "rasterization",
        "failed",
        started_at=now - timedelta(seconds=10),
        completed_at=now,
    )

    result = projection.project_stage_mission(mission, [run])
    logs = projection.stage_lifecycle_logs([run])

    assert result is not None
    assert result["overall_status"] == "error"
    assert result["progress"] == 0
    assert result["current_step"] == "RASTERIZATION · FAILED"
    assert [entry["status"] for entry in logs] == ["processing", "error"]
    assert logs[-1]["message"].endswith("failed: boom")


def test_stage_projection_surfaces_failure_before_blocked_downstream_stage():
    now = datetime.now(UTC)
    mission = SimpleNamespace(
        status="pending",
        params={"phases": ["rasterization", "detection"]},
        updated_at=now,
    )
    failed_rasterization = _run(
        1, "rasterization", "failed", completed_at=now
    )
    # Old executors could leave their last live step behind at termination.
    failed_rasterization.current_step = "EXECUTING"
    runs = [failed_rasterization, _run(2, "detection", "blocked")]

    result = projection.project_stage_mission(mission, runs)

    assert result is not None
    assert result["overall_status"] == "error"
    assert result["current_step"] == "RASTERIZATION · FAILED"


def test_operator_parameters_hide_yolo_choice_from_sam3_projection():
    mission = projection.operator_parameters(
        {"ai_backend": "sam3", "ai_model_variant": "yolo26l"}
    )
    stage = projection.operator_parameters(
        {"ai": {"backend": "sam3", "model_variant": "yolo26l", "classes": ["car"]}}
    )

    assert "ai_model_variant" not in mission
    assert stage["ai"] == {"backend": "sam3", "classes": ["car"]}
