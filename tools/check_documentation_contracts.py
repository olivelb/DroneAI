"""Keep normative documentation synchronized with executable contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.facade_process import FACADE_DRONEGS_PROFILE_ID, FACADE_PROCESS_PROFILE_ID
from shared.quality_profiles import DEFAULT_QUALITY_PROFILE_ID, QUALITY_PROFILES
from shared.stage_contracts import (
    NON_BLOCKING_STAGES,
    RESOURCE_CLASSES,
    STAGE_DAG_VERSION,
    STAGE_ORDER,
)


@dataclass(frozen=True)
class DocumentationContractIssue:
    document: Path
    message: str


def _require(
    issues: list[DocumentationContractIssue],
    relative_path: str,
    tokens: tuple[str, ...],
) -> None:
    document = ROOT / relative_path
    content = document.read_text(encoding="utf-8")
    for token in tokens:
        if token not in content:
            issues.append(
                DocumentationContractIssue(
                    document=document,
                    message=f"missing source-contract token {token!r}",
                )
            )


def _reject(
    issues: list[DocumentationContractIssue],
    relative_path: str,
    phrases: tuple[str, ...],
) -> None:
    document = ROOT / relative_path
    content = document.read_text(encoding="utf-8")
    for phrase in phrases:
        if phrase in content:
            issues.append(
                DocumentationContractIssue(
                    document=document,
                    message=f"contains retired normative phrase {phrase!r}",
                )
            )


def find_documentation_contract_issues() -> tuple[DocumentationContractIssue, ...]:
    issues: list[DocumentationContractIssue] = []
    stage_tokens = (
        f"runtime v{STAGE_DAG_VERSION}",
        *(f"`{stage}`" for stage in STAGE_ORDER),
        *(f"`{resource_class}`" for resource_class in RESOURCE_CLASSES),
        *(f"`{stage}`" for stage in NON_BLOCKING_STAGES),
    )
    _require(issues, "docs/contracts/versioned-stage-dag-v1.md", stage_tokens)

    profile_tokens = tuple(f"`{profile.profile_id}`" for profile in QUALITY_PROFILES)
    _require(
        issues,
        "docs/contracts/quality-profiles-v3.md",
        (*profile_tokens, f"`{DEFAULT_QUALITY_PROFILE_ID}`"),
    )
    _require(
        issues,
        "DOCUMENTATION.md",
        (
            *profile_tokens,
            f"`{FACADE_PROCESS_PROFILE_ID}`",
            f"`{FACADE_DRONEGS_PROFILE_ID}`",
            "`gaussian_viewer`",
        ),
    )
    _require(
        issues,
        "docs/README.md",
        (
            "`DOCUMENTATION_POLICY.md`",
            "audits/2026-08-29-audit-verification.md",
        ),
    )
    _reject(
        issues,
        "README.md",
        ("STAGE_JOBS_IMAGE_TAG=<git-sha>",),
    )
    _reject(
        issues,
        "docs/OPERATIONS.md",
        ("all five executor", "All five executor", "All five credential",
         "set `STAGE_JOBS_IMAGE_TAG`"),
    )
    _reject(
        issues,
        "docs/GEOSPATIAL_WORKSPACE.md",
        (
            "Kafka processing/IA worker",
            "the processing worker opens",
            "the IA worker runs",
            "Missing stale tiles are republished",
        ),
    )
    _reject(
        issues,
        "docs/PRODUCTION_READINESS.md",
        (
            "dashboard API and processing worker",
            "Forwarded headers are not trusted",
            "release that still embeds detections in Kafka",
        ),
    )
    _reject(
        issues,
        "docs/contracts/versioned-stage-dag-v1.md",
        ("declares four portable classes", "map for all five stages"),
    )
    _reject(
        issues,
        "CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md",
        (
            "export STAGE_JOBS_IMAGE_TAG",
            "or an OCI digest",
            "requires OIDC and ownership isolation",
        ),
    )
    return tuple(issues)


def main() -> int:
    issues = find_documentation_contract_issues()
    for issue in issues:
        print(f"{issue.document.relative_to(ROOT)}: {issue.message}")
    if issues:
        print(f"Found {len(issues)} documentation contract issue(s).")
        return 1
    print("Normative documentation matches executable contract identifiers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
