from __future__ import annotations

from scripts.ci.select_ci_jobs import SCOPES, classify_paths


def _enabled(*paths: str) -> set[str]:
    return {scope for scope, enabled in classify_paths(list(paths)).items() if enabled}


def test_documentation_change_only_runs_link_validation() -> None:
    assert _enabled("README.md", "docs/OPERATIONS.md") == {"docs"}


def test_frontend_change_only_runs_frontend_job() -> None:
    assert _enabled("app4-dashboard/frontend/src/app/page.tsx") == {"frontend"}


def test_shared_change_runs_python_and_service_image_jobs() -> None:
    assert _enabled("shared/event_contracts.py") == {"python", "containers"}


def test_schema_change_runs_python_migrations_and_service_images() -> None:
    assert _enabled("shared/database.py") == {"python", "migrations", "containers"}
    assert _enabled("alembic/versions/20260806_revision.py") == {
        "python",
        "migrations",
        "containers",
    }


def test_native_dronegs_change_runs_python_and_native_jobs_when_relevant() -> None:
    assert _enabled("app1-colmap/dronegs/src/trainer.cpp") == {"dronegs"}
    assert _enabled("app1-colmap/dronegs/tools/lpips_eval.py") == {"python", "dronegs"}


def test_chart_change_only_runs_helm_job() -> None:
    assert _enabled("charts/drone-ai/templates/colmap-worker.yaml") == {"helm"}


def test_ovh_terraform_change_only_runs_infrastructure_validation() -> None:
    assert _enabled("infra/ovh/preprod/main.tf") == {"infra"}
    assert _enabled("scripts/deploy/publish-preprod-images.sh") == {"infra"}


def test_docker_context_change_only_runs_service_image_jobs() -> None:
    assert _enabled(".dockerignore") == {"containers"}


def test_ci_control_change_runs_all_lightweight_scopes_without_cuda() -> None:
    assert _enabled(".github/workflows/ci.yml") == set(SCOPES) - {"dronegs"}
    assert "dronegs" in _enabled(
        ".github/workflows/ci.yml",
        "app1-colmap/dronegs/src/trainer.cpp",
    )
