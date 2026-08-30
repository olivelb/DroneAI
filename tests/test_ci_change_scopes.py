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
    assert _enabled("tools/production_qualification.py") == {
        "python",
        "docs",
        "duplication",
    }
    assert _enabled("tests/test_production_qualification.py") == {"python", "docs"}


def test_frontend_change_only_runs_frontend_job() -> None:
    assert _enabled("app4-dashboard/frontend/src/app/page.tsx") == {"frontend"}


def test_frontend_runtime_dependency_changes_run_image_supply_chain() -> None:
    assert _enabled("app4-dashboard/frontend/Dockerfile") == {
        "frontend",
        "frontend_container",
    }
    for path in (
        "app4-dashboard/frontend/package.json",
        "app4-dashboard/frontend/package-lock.json",
    ):
        assert _enabled(path) == {
            "frontend",
            "frontend_container",
            "duplication",
        }


def test_shared_change_runs_python_and_service_image_jobs() -> None:
    assert _enabled("shared/event_contracts.py") == {
        "python",
        "duplication",
        "containers",
        "integration",
    }


def test_schema_change_runs_python_migrations_and_service_images() -> None:
    assert _enabled("shared/database.py") == {
        "python",
        "duplication",
        "migrations",
        "containers",
        "integration",
    }
    assert _enabled("alembic/versions/20260806_revision.py") == {
        "python",
        "migrations",
        "containers",
        "integration",
    }


def test_scheduler_changes_run_postgres_locking_contract() -> None:
    assert _enabled("app4-dashboard/api/stage_orchestrator.py") == {
        "python",
        "duplication",
        "migrations",
        "containers",
        "integration",
    }
    assert _enabled("tests/integration/test_stage_scheduler_postgres.py") == {
        "python",
        "migrations",
        "integration",
    }
    assert _enabled("scripts/ci/verify_rolling_migration.py") == {
        "python",
        "migrations",
    }


def test_platform_composition_changes_run_real_service_integration() -> None:
    assert _enabled("shared/storage.py") == {
        "python",
        "duplication",
        "containers",
        "integration",
    }
    assert _enabled("tests/integration/test_platform_composition.py") == {
        "python",
        "integration",
    }
    assert _enabled(".github/compose.integration.yaml") == {"integration"}
    assert _enabled(".github/compose.http-e2e.yaml") == {"integration"}


def test_native_dronegs_change_runs_python_and_native_jobs_when_relevant() -> None:
    assert _enabled("app1-colmap/dronegs/src/trainer.cpp") == {"dronegs"}
    assert _enabled("app1-colmap/dronegs/tools/lpips_eval.py") == {
        "python",
        "duplication",
        "dronegs",
    }


def test_ia_changes_run_python_duplication_and_runtime_image_validation() -> None:
    expected = {"python", "duplication", "containers"}
    assert _enabled("app2-ia/detection_stage.py") == expected
    assert _enabled("app2-ia/Dockerfile") == {"containers"}
    assert _enabled("requirements/ia-extra.txt") == {"python", "containers"}


def test_duplication_tooling_changes_run_the_dedicated_gate() -> None:
    assert _enabled(".jscpd.json") == {"duplication"}


def test_chart_change_only_runs_helm_job() -> None:
    assert _enabled("charts/drone-ai/templates/colmap-work-pvc.yaml") == {"helm"}


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


def test_selector_unit_test_change_only_runs_python_suite() -> None:
    assert _enabled("tests/test_ci_change_scopes.py") == {"python"}
    assert _enabled("tests/test_ci_event_selection.py") == {"python"}


def test_promotion_contract_changes_only_run_python_contracts() -> None:
    assert _enabled(".github/workflows/promote-images.yml") == {"python"}
    assert _enabled("tools/promotion_manifest.py") == {"python", "duplication"}
    assert _enabled("tests/test_promotion_manifest.py") == {"python"}


def test_python_baseline_is_312_across_tooling_and_ci() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["tool"]["ruff"]["target-version"] == "py312"
    assert project["tool"]["coverage"]["report"]["fail_under"] >= 65

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap-dev.sh").read_text(encoding="utf-8")
    assert "3.11" not in workflow
    assert 'python-version: "3.12"' in workflow
    assert "(3, 12)" in bootstrap
    assert "3.11" not in bootstrap

    for lock_name in ("api.txt", "processing.txt", "dev.txt", "colmap.txt"):
        lock_header = (ROOT / "requirements" / lock_name).read_text(encoding="utf-8")[:160]
        assert "pip-compile with Python 3.12" in lock_header


def test_every_database_model_and_migration_verifier_runs_real_migration_gate():
    paths = [
        path.relative_to(ROOT).as_posix()
        for pattern in ("shared/database*.py", "scripts/ci/verify_*migration.py")
        for path in ROOT.glob(pattern)
    ]
    assert paths
    for path in paths:
        assert "migrations" in _enabled(path), path


def test_unknown_path_runs_every_scope_instead_of_silently_skipping() -> None:
    assert _enabled("new-runtime/unknown.contract") == set(SCOPES)


def test_license_change_runs_docs_without_all_general_ci() -> None:
    assert _enabled("LICENSE", "THIRD_PARTY_NOTICES.md") == {"docs"}


def test_merge_queue_is_classified_instead_of_unconditionally_running_everything() -> None:
    source = (ROOT / "scripts/ci/select_ci_jobs.py").read_text()
    assert 'event_name in {"pull_request", "merge_group"}' in source
    assert "event_changed_paths" in source
