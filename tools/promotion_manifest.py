"""Build validated image records and a commit-scoped promotion manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RELEASE_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
IMAGE_REFERENCE = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+$"
)
REQUIRED_IMAGES = frozenset(
    {
        "drone-colmap-base",
        "drone-colmap",
        "drone-ia",
        "drone-dashboard-api",
        "drone-dashboard-frontend",
    }
)
REQUIRED_WORKFLOWS = frozenset(
    {
        "ci.yml",
        "cuda-containers.yml",
        "dronegs-gpu-qualification.yml",
        "codeql.yml",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_record(
    *,
    name: str,
    image: str,
    digest: str,
    source_commit: str,
    sbom: Path,
    vulnerability_report: Path,
) -> dict[str, Any]:
    if name not in REQUIRED_IMAGES:
        raise ValueError(f"unsupported promoted image {name!r}")
    if not IMAGE_REFERENCE.fullmatch(image) or "@" in image:
        raise ValueError("image must be a tag-free registry/repository reference")
    if not OCI_DIGEST.fullmatch(digest):
        raise ValueError("digest must be an OCI SHA-256 digest")
    if not COMMIT.fullmatch(source_commit):
        raise ValueError("source_commit must be a full lower-case Git SHA")
    if not sbom.is_file() or not vulnerability_report.is_file():
        raise ValueError("SBOM and vulnerability report must both exist")
    return {
        "name": name,
        "image": image,
        "digest": digest,
        "reference": f"{image}@{digest}",
        "source_commit": source_commit,
        "sbom": {"format": "CycloneDX JSON", "sha256": _sha256(sbom)},
        "vulnerability_report": {
            "scanner": "Trivy",
            "sha256": _sha256(vulnerability_report),
            "fixable_high_critical_gate": "passed",
        },
        "signature": {
            "scheme": "Sigstore keyless",
            "oidc_issuer": "https://token.actions.githubusercontent.com",
        },
        "provenance": {
            "formats": ["BuildKit max", "GitHub artifact attestation"],
        },
    }


def assemble_manifest(
    *,
    records_directory: Path,
    qualification_runs_path: Path,
    release_tag: str,
    source_commit: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if not RELEASE_TAG.fullmatch(release_tag):
        raise ValueError("release_tag must use vMAJOR.MINOR.PATCH")
    if not COMMIT.fullmatch(source_commit):
        raise ValueError("source_commit must be a full lower-case Git SHA")
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(records_directory.glob("*.image.json"))
    ]
    by_name = {record.get("name"): record for record in records}
    if len(by_name) != len(records) or set(by_name) != REQUIRED_IMAGES:
        raise ValueError(
            "image records must contain exactly "
            + ", ".join(sorted(REQUIRED_IMAGES))
        )
    for name, record in by_name.items():
        if record.get("source_commit") != source_commit:
            raise ValueError(f"{name}: source commit differs from release")
        if record.get("reference") != f"{record.get('image')}@{record.get('digest')}":
            raise ValueError(f"{name}: image reference is not digest-bound")

    qualification_runs = json.loads(
        qualification_runs_path.read_text(encoding="utf-8")
    )
    if not isinstance(qualification_runs, list):
        raise ValueError("qualification runs must be a JSON array")
    by_workflow = {
        run.get("workflow_file"): run
        for run in qualification_runs
        if isinstance(run, dict)
    }
    if len(by_workflow) != len(qualification_runs) or set(by_workflow) != REQUIRED_WORKFLOWS:
        raise ValueError(
            "qualification runs must contain exactly "
            + ", ".join(sorted(REQUIRED_WORKFLOWS))
        )
    for workflow, run in by_workflow.items():
        if run.get("headSha") != source_commit or run.get("conclusion") != "success":
            raise ValueError(f"{workflow}: qualification is not successful for release")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if release_tag != f"v{version}":
        raise ValueError("release tag does not match VERSION")
    return {
        "schema_version": 1,
        "release_tag": release_tag,
        "platform_version": version,
        "source_commit": source_commit,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "images": [by_name[name] for name in sorted(REQUIRED_IMAGES)],
        "qualification_runs": [
            by_workflow[workflow] for workflow in sorted(REQUIRED_WORKFLOWS)
        ],
        "manifest_signature": {
            "scheme": "Sigstore keyless bundle",
            "workflow": ".github/workflows/promote-images.yml",
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--name", required=True)
    record.add_argument("--image", required=True)
    record.add_argument("--digest", required=True)
    record.add_argument("--source-commit", required=True)
    record.add_argument("--sbom", type=Path, required=True)
    record.add_argument("--vulnerability-report", type=Path, required=True)
    record.add_argument("--output", type=Path, required=True)
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--records-directory", type=Path, required=True)
    assemble.add_argument("--qualification-runs", type=Path, required=True)
    assemble.add_argument("--release-tag", required=True)
    assemble.add_argument("--source-commit", required=True)
    assemble.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "record":
        document = image_record(
            name=args.name,
            image=args.image,
            digest=args.digest,
            source_commit=args.source_commit,
            sbom=args.sbom,
            vulnerability_report=args.vulnerability_report,
        )
    else:
        document = assemble_manifest(
            records_directory=args.records_directory,
            qualification_runs_path=args.qualification_runs,
            release_tag=args.release_tag,
            source_commit=args.source_commit,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
