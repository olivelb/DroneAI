"""Select CI jobs from candidate paths without external actions.

Pull requests and merge groups execute only scopes affected by their candidate
diff. Manual runs execute every scope. Unclassifiable input fails safe by
selecting every scope.
"""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any, Final

from scripts.ci.changed_paths import event_changed_paths

SCOPES: Final = (
    "python",
    "duplication",
    "docs",
    "dronegs",
    "migrations",
    "integration",
    "frontend",
    "frontend_container",
    "containers",
    "helm",
    "infra",
)
CONTROL_PATHS: Final = {
    ".github/workflows/ci.yml",
    "scripts/ci/select_ci_jobs.py",
    "scripts/ci/check_selected_jobs.py",
    "scripts/ci/changed_paths.py",
}
PROMOTION_PATHS: Final = {
    ".github/workflows/promote-images.yml",
    "tools/promotion_manifest.py",
    "tests/test_promotion_manifest.py",
}
NO_TEST_PATHS: Final = {".gitignore", ".gitattributes"}
DOCUMENT_PATHS: Final = {"LICENSE", "THIRD_PARTY_NOTICES.md"}

DUPLICATION_TOOL_PATHS: Final = {
    ".jscpd.json",
    "app4-dashboard/frontend/package.json",
    "app4-dashboard/frontend/package-lock.json",
}


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(f"{directory}/")



def _is_recognized_path(path: str) -> bool:
    """Whether the dependency map deliberately owns this path."""
    if path in (
        CONTROL_PATHS | NO_TEST_PATHS | DOCUMENT_PATHS | DUPLICATION_TOOL_PATHS | PROMOTION_PATHS
    ):
        return True
    if path in {
        "Makefile", "pyproject.toml", "VERSION", "alembic.ini",
        ".dockerignore", ".github/compose.http-e2e.yaml",
        ".github/compose.integration.yaml", "compose.test.yaml",
        "app2-ia/Dockerfile", "app4-dashboard/api/Dockerfile",
        "scripts/deploy/publish-preprod-images.sh",
    }:
        return True
    if _under(path, "docs") or path.endswith(".md"):
        return True
    if any(_under(path, directory) for directory in (
        "shared", "app4-dashboard/api", "app4-dashboard/frontend",
        "app2-ia", "app1-colmap/dronegs", "alembic", "tests/integration",
        "requirements", "charts/drone-ai", "infra/ovh",
    )):
        return True
    if path.endswith(".py") and any(_under(path, directory) for directory in (
        "app1-colmap", "tests", "tools", "scripts/ci",
    )):
        return True
    return False

def classify_paths(paths: list[str]) -> dict[str, bool]:
    """Return the minimal safe job set for normalized repository paths."""

    normalized = set()
    for path in paths:
        normalized_path = PurePosixPath(path).as_posix()
        if normalized_path.startswith("./"):
            normalized_path = normalized_path[2:]
        normalized.add(normalized_path)
    control_changed = bool(normalized & CONTROL_PATHS)
    promotion_changed = bool(normalized & PROMOTION_PATHS)
    recognized = set(normalized & (CONTROL_PATHS | NO_TEST_PATHS | PROMOTION_PATHS))
    # Exercise every lightweight CI contract when the dispatcher changes, but
    # never turn a CI-only edit into a native CUDA/DroneGS build. A real
    # DroneGS path in the same diff is still classified by the loop below.
    selected = {
        scope: control_changed and scope != "dronegs"
        for scope in SCOPES
    }

    if promotion_changed:
        selected["python"] = True

    for path in normalized:
        is_python = path.endswith(".py") and any(
            _under(path, directory)
            for directory in (
                "app1-colmap",
                "app2-ia",
                "app4-dashboard/api",
                "shared",
                "alembic",
                "tests",
                "tools",
                "scripts/ci",
            )
        )
        if is_python or path in {"Makefile", "pyproject.toml", "VERSION"} or _under(path, "requirements"):
            selected["python"] = True
            recognized.add(path)
        if (
            (
                path.endswith(".py")
                and any(
                    _under(path, directory)
                    for directory in (
                        "app1-colmap",
                        "app2-ia",
                        "app4-dashboard/api",
                        "shared",
                        "tools",
                    )
                )
                and not _under(path, "tests")
                and "/tests/" not in path
                and not PurePosixPath(path).name.startswith("test_")
            )
            or path in DUPLICATION_TOOL_PATHS
        ):
            selected["duplication"] = True
        if _under(path, "docs") or path.endswith(".md") or path in DOCUMENT_PATHS or path in {
            "tools/check_markdown_links.py",
            "tools/check_documentation_contracts.py",
            "tools/production_qualification.py",
            "tests/test_markdown_links.py",
            "tests/test_documentation_contracts.py",
            "tests/test_production_qualification.py",
        }:
            selected["docs"] = True
        if _under(path, "app1-colmap/dronegs") and not path.endswith(".md"):
            selected["dronegs"] = True
        database_model = (
            _under(path, "shared")
            and PurePosixPath(path).name.startswith("database")
            and path.endswith(".py")
        )
        migration_verifier = (
            path.startswith("scripts/ci/verify_") and path.endswith("migration.py")
        )
        if database_model or migration_verifier or _under(path, "alembic") or path in {
            "alembic.ini",
            "app4-dashboard/api/stage_orchestrator.py",
            "shared/config.py",
            "shared/database.py",
            "tests/integration/test_stage_scheduler_postgres.py",
            "scripts/ci/verify_rolling_migration.py",
            "requirements/dev.in",
            "requirements/dev.txt",
        }:
            selected["migrations"] = True
        if (
            _under(path, "shared")
            or _under(path, "app4-dashboard/api")
            or _under(path, "alembic")
            or _under(path, "tests/integration")
            or path
            in {
                ".github/compose.http-e2e.yaml",
                ".github/compose.integration.yaml",
                "alembic.ini",
                "compose.test.yaml",
                "requirements/dev.in",
                "requirements/dev.txt",
            }
        ):
            selected["integration"] = True
        if _under(path, "app4-dashboard/frontend"):
            selected["frontend"] = True
        if path == ".dockerignore" or path in {
            "app4-dashboard/frontend/Dockerfile",
            "app4-dashboard/frontend/package.json",
            "app4-dashboard/frontend/package-lock.json",
        }:
            selected["frontend_container"] = True
        if (
            _under(path, "shared")
            or _under(path, "app2-ia")
            or _under(path, "app4-dashboard/api")
            or _under(path, "alembic")
            or path
            in {
                ".dockerignore",
                "alembic.ini",
                "app2-ia/Dockerfile",
                "app4-dashboard/api/Dockerfile",
                "requirements/api.in",
                "requirements/api.txt",
                "requirements/ia-extra.in",
                "requirements/ia-extra.txt",
                "requirements/processing.in",
                "requirements/processing.txt",
            }
        ):
            selected["containers"] = True
        if _under(path, "charts/drone-ai"):
            selected["helm"] = True
        if _under(path, "infra/ovh"):
            selected["infra"] = True
        if path == "scripts/deploy/publish-preprod-images.sh":
            selected["infra"] = True
        if _is_recognized_path(path):
            recognized.add(path)

    # A new path not covered by the dependency map is uncertainty, not proof
    # that no test applies. Select every scope until its ownership is codified.
    if normalized - recognized:
        return {scope: True for scope in SCOPES}
    return selected


def main() -> int:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is required")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        selected = {scope: True for scope in SCOPES}
    elif event_name in {"pull_request", "merge_group"}:
        event: dict[str, Any] = {}
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if event_path:
            try:
                with open(event_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    event = loaded
            except (OSError, json.JSONDecodeError):
                pass
        paths = event_changed_paths(event_name, event)
        selected = {scope: True for scope in SCOPES} if paths is None else classify_paths(paths)
    else:
        selected = {scope: True for scope in SCOPES}

    with open(output_path, "a", encoding="utf-8") as handle:
        for scope in SCOPES:
            handle.write(f"{scope}={str(selected[scope]).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
