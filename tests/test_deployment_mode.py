import pytest

from shared import deployment_mode


def test_development_can_run_control_plane_without_compute(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "development")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    assert deployment_mode.bounded_stage_jobs_enabled() is False


@pytest.mark.parametrize("environment", ("staging", "production"))
def test_protected_environment_requires_bounded_stage_jobs(
    monkeypatch,
    environment,
):
    monkeypatch.setenv("DRONEAI_ENV", environment)
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    with pytest.raises(RuntimeError, match="require bounded stage Jobs"):
        deployment_mode.bounded_stage_jobs_enabled()

def test_stage_job_mode_rejects_ambiguous_boolean(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "sometimes")

    with pytest.raises(RuntimeError, match="must be true or false"):
        deployment_mode.bounded_stage_jobs_enabled()
