from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_platform_version",
    REPOSITORY / "tools/check_platform_version.py",
)
assert SPEC is not None and SPEC.loader is not None
version_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version_contract)


def test_repository_platform_versions_are_synchronized() -> None:
    assert version_contract.validate_platform_version(REPOSITORY) == []


def test_platform_release_tag_matches_canonical_version() -> None:
    version = (REPOSITORY / "VERSION").read_text(encoding="utf-8").strip()
    major, minor, patch = (int(part) for part in version.split("."))
    mismatched_version = f"{major}.{minor}.{patch + 1}"
    assert version_contract.validate_platform_version(
        REPOSITORY,
        tag=f"refs/tags/v{version}",
    ) == []
    errors = version_contract.validate_platform_version(
        REPOSITORY,
        tag=f"v{mismatched_version}",
    )
    assert errors == [
        f"platform release tag must be 'v{version}', got 'v{mismatched_version}'"
    ]
