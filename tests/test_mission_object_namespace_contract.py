from __future__ import annotations

from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    REPOSITORY_ROOT / "app1-colmap" / "colmap_worker",
    REPOSITORY_ROOT / "app2-ia",
    REPOSITORY_ROOT / "app3-processing",
    REPOSITORY_ROOT / "app4-dashboard" / "api",
    REPOSITORY_ROOT / "app4-dashboard" / "frontend" / "app",
    REPOSITORY_ROOT / "shared",
)
SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}
RAW_MISSION_OBJECT_PATH = re.compile(
    r"(?:f[\"']missions/|[\"']missions/\{|'missions/'\s*\+|`missions/\$\{)"
)


def test_production_code_does_not_reconstruct_legacy_mission_object_paths() -> None:
    offenders: list[str] = []
    for root in PRODUCTION_ROOTS:
        for source in root.rglob("*"):
            if source.suffix not in SOURCE_SUFFIXES:
                continue
            if source == REPOSITORY_ROOT / "shared" / "tenancy.py":
                continue
            if any(part in {"node_modules", ".next", "__pycache__"} for part in source.parts):
                continue
            for line_number, line in enumerate(
                source.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if RAW_MISSION_OBJECT_PATH.search(line):
                    offenders.append(
                        f"{source.relative_to(REPOSITORY_ROOT)}:{line_number}"
                    )

    assert offenders == [], (
        "Mission object keys must come from MissionObjectNamespace: "
        + ", ".join(offenders)
    )
