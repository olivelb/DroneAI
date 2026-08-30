from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.ci.changed_paths import event_changed_paths
from scripts.ci.select_codeql import codeql_scopes


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def test_pull_request_and_merge_group_use_exact_candidate_diff(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("base")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    source = tmp_path / "shared" / "security.py"
    source.parent.mkdir()
    source.write_text("safe = True\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "candidate")
    head = _git(tmp_path, "rev-parse", "HEAD")

    pull_request = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    merge_group = {"merge_group": {"base_sha": base, "head_sha": head}}
    assert event_changed_paths("pull_request", pull_request, cwd=tmp_path) == ["shared/security.py"]
    assert event_changed_paths("merge_group", merge_group, cwd=tmp_path) == ["shared/security.py"]


def test_malformed_or_unknown_event_requires_conservative_fallback(tmp_path: Path) -> None:
    assert event_changed_paths("pull_request", {}, cwd=tmp_path) is None
    assert event_changed_paths("merge_group", {"merge_group": {}}, cwd=tmp_path) is None
    assert event_changed_paths("future_event", {}, cwd=tmp_path) is None


def test_codeql_languages_are_selected_independently() -> None:
    assert codeql_scopes(["shared/security.py"]) == {
        "python": True, "javascript": False, "c_cpp": False, "any": True,
    }
    assert codeql_scopes(["app4-dashboard/frontend/app/page.tsx"]) == {
        "python": False, "javascript": True, "c_cpp": False, "any": True,
    }
    assert codeql_scopes(["app1-colmap/dronegs/src/model.cpp"]) == {
        "python": False, "javascript": False, "c_cpp": True, "any": True,
    }
    assert codeql_scopes(["docs/OPERATIONS.md", "charts/drone-ai/values.yaml"]) == {
        "python": False, "javascript": False, "c_cpp": False, "any": False,
    }
    assert codeql_scopes(["shared/unknown.template"]) == {
        "python": True, "javascript": True, "c_cpp": True, "any": True,
    }
    assert codeql_scopes([".github/workflows/codeql.yml"]) == {
        "python": True, "javascript": True, "c_cpp": True, "any": True,
    }
    assert codeql_scopes(["tests/test_ci_event_selection.py"]) == {
        "python": True, "javascript": False, "c_cpp": False, "any": True,
    }


def test_codeql_selector_manual_run_selects_all_languages(monkeypatch, tmp_path: Path) -> None:
    from scripts.ci.select_codeql import main

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    assert main() == 0
    assert output.read_text().splitlines() == [
        "python=true", "javascript=true", "c_cpp=true", "any=true",
    ]
