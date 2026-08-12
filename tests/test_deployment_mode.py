from pathlib import Path

import pytest

from shared import deployment_mode


ROOT = Path(__file__).resolve().parents[1]


def test_development_keeps_fused_compute_compatibility(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "development")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    assert deployment_mode.bounded_stage_jobs_enabled() is False
    deployment_mode.assert_fused_compute_allowed("test")


@pytest.mark.parametrize("environment", ("staging", "production"))
def test_protected_environment_requires_bounded_stage_jobs(
    monkeypatch,
    environment,
):
    monkeypatch.setenv("DRONEAI_ENV", environment)
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    with pytest.raises(RuntimeError, match="require bounded stage Jobs"):
        deployment_mode.bounded_stage_jobs_enabled()
    with pytest.raises(RuntimeError, match="development-only"):
        deployment_mode.assert_fused_compute_allowed("test")


def test_stage_job_mode_rejects_ambiguous_boolean(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "sometimes")

    with pytest.raises(RuntimeError, match="must be true or false"):
        deployment_mode.bounded_stage_jobs_enabled()


def test_fused_worker_composition_roots_enforce_deployment_policy():
    for relative_path in (
        "app1-colmap/colmap_worker/worker.py",
        "app2-ia/main.py",
        "app3-processing/main.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "from shared.deployment_mode import assert_fused_compute_allowed" in source
        assert "assert_fused_compute_allowed(" in source
