"""Validate the synchronized DroneAI platform version contract."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _read_chart_versions(path: Path) -> tuple[str | None, str | None]:
    chart_version: str | None = None
    app_version: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("version:"):
            chart_version = line.partition(":")[2].strip().strip("\"'")
        elif line.startswith("appVersion:"):
            app_version = line.partition(":")[2].strip().strip("\"'")
    return chart_version, app_version


def validate_platform_version(
    repository: Path,
    *,
    tag: str | None = None,
) -> list[str]:
    """Return every platform-version contract violation in *repository*."""

    errors: list[str] = []
    version = (repository / "VERSION").read_text(encoding="utf-8").strip()
    if VERSION_PATTERN.fullmatch(version) is None:
        errors.append(f"VERSION must be stable SemVer MAJOR.MINOR.PATCH, got {version!r}")

    with (repository / "pyproject.toml").open("rb") as stream:
        python_version = str(tomllib.load(stream)["project"]["version"])

    frontend = json.loads(
        (repository / "app4-dashboard/frontend/package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (repository / "app4-dashboard/frontend/package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    chart_version, chart_app_version = _read_chart_versions(
        repository / "charts/drone-ai/Chart.yaml"
    )

    synchronized_versions = {
        "pyproject.toml project.version": python_version,
        "frontend package.json version": str(frontend.get("version")),
        "frontend package-lock.json version": str(frontend_lock.get("version")),
        "frontend package-lock root version": str(
            frontend_lock.get("packages", {}).get("", {}).get("version")
        ),
        "Helm chart version": chart_version,
        "Helm appVersion": chart_app_version,
    }
    errors.extend(
        f"{name} must equal VERSION {version}, got {observed!r}"
        for name, observed in synchronized_versions.items()
        if observed != version
    )

    if tag is not None:
        normalized_tag = tag.removeprefix("refs/tags/")
        expected_tag = f"v{version}"
        if normalized_tag != expected_tag:
            errors.append(
                f"platform release tag must be {expected_tag!r}, got {normalized_tag!r}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="DroneAI repository root (defaults to the parent of tools/)",
    )
    parser.add_argument(
        "--tag",
        help="Optional platform tag or refs/tags/... ref to validate",
    )
    args = parser.parse_args()
    errors = validate_platform_version(args.repository.resolve(), tag=args.tag)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    version = (args.repository / "VERSION").read_text(encoding="utf-8").strip()
    print(f"DroneAI platform version contract is synchronized at {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
