from __future__ import annotations

import tomllib
from pathlib import Path

from scripts.ci.select_ci_jobs import SCOPES, classify_paths

ROOT = Path(__file__).resolve().parents[1]


def _enabled(*paths: str) -> set[str]:
    return {scope for scope, enabled in classify_paths(list(paths)).items() if enabled}


def test_documentation_change_only_runs_link_validation() -> None:
    assert _enabled("README.md", "docs/OPERATIONS.md") == {"docs"}
    assert _enabled("docs/benchmarks/release.qualification.json") == {"docs"}


def test_qualification_contract_changes_run_cpu_and_docs_validation() -> None:
    assert _enabled("tools/production_qualification.py") == {"python", "docs"}
    assert _enabled("tests/test_production_qualification.py") == {"python", "docs"}


def test_frontend_change_only_runs_frontend_job() -> None:
    assert _enabled("app4-dashboard/frontend/src/app/page.tsx") == {"frontend"}


def test_frontend_runtime_dependency_changes_run_image_supply_chain() -> None:
    for path in (
        "app4-dashboard/frontend/Dockerfile",
        "app4-dashboard/frontend/package.json",
        "app4-dashboard/frontend/package-lock.json",
    ):
        assert _enabled(path) == {"frontend", "frontend_container"}


def test_shared_change_runs_python_and_service_image_jobs() -> None:
    assert _enabled("shared/event_contracts.py") == {"python", "containers"}


def test_schema_change_runs_python_migrations_and_service_images() -> None:
    assert _enabled("shared/database.py") == {"python", "migrations", "containers"}
    assert _enabled("alembic/versions/20260806_revision.py") == {
        "python",
        "migrations",
        "containers",
    }


def test_scheduler_changes_run_postgres_locking_contract() -> None:
    assert _enabled("app4-dashboard/api/stage_orchestrator.py") == {
        "python",
        "migrations",
        "containers",
    }
    assert _enabled("tests/integration/test_stage_scheduler_postgres.py") == {
        "python",
        "migrations",
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
    assert _enabled(".dockerignore") == {"containers", "frontend_container"}


def test_ci_control_change_runs_all_lightweight_scopes_without_cuda() -> None:
    assert _enabled(".github/workflows/ci.yml") == set(SCOPES) - {"dronegs"}
    assert "dronegs" in _enabled(
        ".github/workflows/ci.yml",
        "app1-colmap/dronegs/src/trainer.cpp",
    )


def test_python_baseline_is_312_across_tooling_and_ci() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["tool"]["ruff"]["target-version"] == "py312"

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap-dev.sh").read_text(encoding="utf-8")
    assert "3.11" not in workflow
    assert 'python-version: "3.12"' in workflow
    assert "(3, 12)" in bootstrap
    assert "3.11" not in bootstrap

    for lock_name in ("api.txt", "processing.txt", "dev.txt", "colmap.txt"):
        lock_header = (ROOT / "requirements" / lock_name).read_text(encoding="utf-8")[:160]
        assert "pip-compile with Python 3.12" in lock_header
