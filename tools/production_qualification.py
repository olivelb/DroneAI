"""Create, validate and render DroneAI production drill evidence.

The tool deliberately does not execute disruptive drills. Operators run those
steps from the reviewed runbook, record bounded references to their evidence,
and use this module to enforce one stable release-gate contract.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final


SCHEMA_VERSION: Final = 1
EVIDENCE_SUFFIX: Final = ".qualification.json"
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
QUALIFICATION_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
PLACEHOLDERS: Final = {"record-me", "unknown", "todo", "tbd"}
REQUIRED_IMAGE_NAMES: Final = (
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
)
REQUIRED_DRILLS: Final = (
    ("five_stage_chain", "Complete immutable five-stage chain"),
    ("stage_cancellation", "Cancellation of a running stage"),
    ("stage_deadline", "Stage deadline expiry"),
    ("missing_job_reconciliation", "Missing Job or pod reconciliation"),
    ("api_restart_after_reservation", "API restart after reservation"),
    ("database_interruption", "Database interruption and recovery"),
    ("object_storage_interruption", "Object-storage interruption and recovery"),
    ("backup_restore", "Database and artifact backup/isolated restore"),
    ("helm_rollback", "Helm rollback to immutable images"),
)
DRILL_TITLES: Final = dict(REQUIRED_DRILLS)
DRILL_STATUSES: Final = {"not_run", "passed", "failed", "blocked"}
SENSITIVE_KEY_PATTERN: Final = re.compile(
    r"(?:authorization|password|secret|token|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERNS: Final = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(
        r"\b(?:password|secret|token|access[_-]?key)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


class EvidenceError(ValueError):
    """Raised when qualification evidence violates the contract."""


def _as_object(value: object, path: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected an object")
        return {}
    return value


def _as_list(value: object, path: str, errors: list[str]) -> Sequence[Any]:
    if not isinstance(value, list):
        errors.append(f"{path}: expected an array")
        return ()
    return value


def _required_string(
    value: Mapping[str, Any], key: str, path: str, errors: list[str]
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        errors.append(f"{path}.{key}: expected a non-empty string")
        return ""
    return item


def _number(
    value: Mapping[str, Any],
    key: str,
    path: str,
    errors: list[str],
    *,
    minimum: float,
) -> float | None:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int | float):
        errors.append(f"{path}.{key}: expected a number")
        return None
    if item < minimum:
        errors.append(f"{path}.{key}: must be >= {minimum:g}")
    return float(item)


def _date_time(value: object, path: str, errors: list[str], *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value:
        errors.append(f"{path}: expected an RFC 3339 date-time")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path}: invalid RFC 3339 date-time")
        return
    if parsed.tzinfo is None:
        errors.append(f"{path}: date-time must include a timezone")


def _string_array(value: object, path: str, errors: list[str]) -> None:
    items = _as_list(value, path, errors)
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{path}[{index}]: expected a non-empty string")
        elif item in seen:
            errors.append(f"{path}[{index}]: duplicate value {item!r}")
        else:
            seen.add(item)


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: set[str], path: str, errors: list[str]
) -> None:
    for key in sorted(set(value) - allowed):
        errors.append(f"{path}.{key}: unknown field")


def _sensitive_paths(value: object, path: str = "$") -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                yield item_path
            yield from _sensitive_paths(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _sensitive_paths(item, f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(value):
                yield path
                break


def validate_evidence(document: object) -> list[str]:
    """Return all shape and safety violations in an evidence document."""

    errors: list[str] = []
    root = _as_object(document, "$", errors)
    allowed_root = {
        "schema_version",
        "qualification_id",
        "generated_at",
        "environment",
        "release",
        "objectives",
        "drills",
        "attestation",
    }
    _reject_unknown_keys(root, allowed_root, "$", errors)
    if root.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$.schema_version: expected {SCHEMA_VERSION}")
    qualification_id = _required_string(root, "qualification_id", "$", errors)
    if qualification_id and not QUALIFICATION_ID_PATTERN.fullmatch(qualification_id):
        errors.append("$.qualification_id: use 3-128 lowercase letters, digits, '.', '_' or '-'")
    _date_time(root.get("generated_at"), "$.generated_at", errors)

    environment = _as_object(root.get("environment"), "$.environment", errors)
    _reject_unknown_keys(
        environment,
        {"name", "cluster", "namespace", "kubernetes_version", "node_type", "gpu"},
        "$.environment",
        errors,
    )
    for key in ("name", "cluster", "namespace", "kubernetes_version", "node_type"):
        _required_string(environment, key, "$.environment", errors)
    gpu = _as_object(environment.get("gpu"), "$.environment.gpu", errors)
    _reject_unknown_keys(
        gpu,
        {"model", "vram_mb", "driver", "cuda_runtime"},
        "$.environment.gpu",
        errors,
    )
    for key in ("model", "driver", "cuda_runtime"):
        _required_string(gpu, key, "$.environment.gpu", errors)
    _number(gpu, "vram_mb", "$.environment.gpu", errors, minimum=1)

    release = _as_object(root.get("release"), "$.release", errors)
    _reject_unknown_keys(
        release,
        {"git_commit", "chart_version", "values_sha256", "images"},
        "$.release",
        errors,
    )
    commit = _required_string(release, "git_commit", "$.release", errors)
    if commit and not COMMIT_PATTERN.fullmatch(commit):
        errors.append("$.release.git_commit: expected a 40-character lowercase Git SHA")
    _required_string(release, "chart_version", "$.release", errors)
    values_sha = _required_string(release, "values_sha256", "$.release", errors)
    if values_sha and not SHA256_PATTERN.fullmatch(values_sha):
        errors.append("$.release.values_sha256: expected a lowercase SHA-256")
    images = _as_object(release.get("images"), "$.release.images", errors)
    missing_images = set(REQUIRED_IMAGE_NAMES) - set(images)
    if missing_images:
        errors.append(f"$.release.images: missing executors {', '.join(sorted(missing_images))}")
    for name, digest in sorted(images.items()):
        if not isinstance(name, str) or not name.strip():
            errors.append("$.release.images: image names must be non-empty strings")
        if not isinstance(digest, str) or not OCI_DIGEST_PATTERN.fullmatch(digest):
            errors.append(f"$.release.images.{name}: expected sha256:<64 lowercase hex digits>")

    objectives = _as_object(root.get("objectives"), "$.objectives", errors)
    _reject_unknown_keys(
        objectives,
        {"rto_seconds", "rpo_seconds"},
        "$.objectives",
        errors,
    )
    _number(objectives, "rto_seconds", "$.objectives", errors, minimum=1)
    _number(objectives, "rpo_seconds", "$.objectives", errors, minimum=0)

    drills = _as_list(root.get("drills"), "$.drills", errors)
    seen_drills: set[str] = set()
    for index, raw_drill in enumerate(drills):
        path = f"$.drills[{index}]"
        drill = _as_object(raw_drill, path, errors)
        _reject_unknown_keys(
            drill,
            {
                "id",
                "status",
                "started_at",
                "completed_at",
                "observed_rto_seconds",
                "observed_rpo_seconds",
                "run_ids",
                "artifact_ids",
                "evidence_refs",
                "notes",
            },
            path,
            errors,
        )
        drill_id = _required_string(drill, "id", path, errors)
        if drill_id in seen_drills:
            errors.append(f"{path}.id: duplicate drill {drill_id!r}")
        seen_drills.add(drill_id)
        if drill_id and drill_id not in DRILL_TITLES:
            errors.append(f"{path}.id: unknown drill {drill_id!r}")
        status = _required_string(drill, "status", path, errors)
        if status and status not in DRILL_STATUSES:
            errors.append(f"{path}.status: expected one of {', '.join(sorted(DRILL_STATUSES))}")
        _date_time(drill.get("started_at"), f"{path}.started_at", errors, nullable=True)
        _date_time(drill.get("completed_at"), f"{path}.completed_at", errors, nullable=True)
        for metric in ("observed_rto_seconds", "observed_rpo_seconds"):
            metric_value = drill.get(metric)
            if metric_value is not None:
                _number(drill, metric, path, errors, minimum=0)
        for field in ("run_ids", "artifact_ids", "evidence_refs"):
            _string_array(drill.get(field), f"{path}.{field}", errors)
        if not isinstance(drill.get("notes"), str):
            errors.append(f"{path}.notes: expected a string")
    missing_drills = set(DRILL_TITLES) - seen_drills
    if missing_drills:
        errors.append(f"$.drills: missing drills {', '.join(sorted(missing_drills))}")

    attestation = _as_object(root.get("attestation"), "$.attestation", errors)
    _reject_unknown_keys(
        attestation,
        {"operator", "reviewed_at", "statement"},
        "$.attestation",
        errors,
    )
    _required_string(attestation, "operator", "$.attestation", errors)
    _date_time(attestation.get("reviewed_at"), "$.attestation.reviewed_at", errors, nullable=True)
    _required_string(attestation, "statement", "$.attestation", errors)

    for sensitive_path in _sensitive_paths(document):
        errors.append(f"{sensitive_path}: possible credential or secret is forbidden in evidence")
    return errors


def gate_failures(document: Mapping[str, Any]) -> list[str]:
    """Return reasons why structurally valid evidence cannot promote a release."""

    failures = validate_evidence(document)
    if failures:
        return failures
    environment = document["environment"]
    gpu = environment["gpu"]
    release = document["release"]
    for path, value in (
        ("environment.kubernetes_version", environment["kubernetes_version"]),
        ("environment.node_type", environment["node_type"]),
        ("environment.gpu.model", gpu["model"]),
        ("environment.gpu.driver", gpu["driver"]),
        ("environment.gpu.cuda_runtime", gpu["cuda_runtime"]),
        ("release.chart_version", release["chart_version"]),
    ):
        if value.strip().lower() in PLACEHOLDERS:
            failures.append(f"{path}: placeholder value cannot pass the production gate")
    if release["values_sha256"] == "0" * 64:
        failures.append("release.values_sha256: placeholder digest cannot pass the production gate")
    for name, digest in release["images"].items():
        if digest == f"sha256:{'0' * 64}":
            failures.append(f"release.images.{name}: placeholder digest cannot pass the production gate")
    objectives = document["objectives"]
    for drill in document["drills"]:
        drill_id = drill["id"]
        if drill["status"] != "passed":
            failures.append(f"drills.{drill_id}: status is {drill['status']!r}, expected 'passed'")
            continue
        if drill["started_at"] is None or drill["completed_at"] is None:
            failures.append(f"drills.{drill_id}: passed drill requires start and completion times")
        if drill["observed_rto_seconds"] is None or drill["observed_rpo_seconds"] is None:
            failures.append(f"drills.{drill_id}: passed drill requires observed RTO and RPO")
        else:
            if drill["observed_rto_seconds"] > objectives["rto_seconds"]:
                failures.append(f"drills.{drill_id}: observed RTO exceeds the objective")
            if drill["observed_rpo_seconds"] > objectives["rpo_seconds"]:
                failures.append(f"drills.{drill_id}: observed RPO exceeds the objective")
        if not drill["evidence_refs"]:
            failures.append(f"drills.{drill_id}: passed drill requires at least one evidence reference")
    if document["attestation"]["reviewed_at"] is None:
        failures.append("attestation.reviewed_at: production gate requires a review timestamp")
    return failures


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def new_draft(
    *,
    qualification_id: str,
    environment: str,
    cluster: str,
    namespace: str,
    operator: str,
    git_commit: str,
) -> dict[str, Any]:
    """Return a structurally valid, deliberately non-passing evidence draft."""

    return {
        "schema_version": SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "generated_at": _now(),
        "environment": {
            "name": environment,
            "cluster": cluster,
            "namespace": namespace,
            "kubernetes_version": "record-me",
            "node_type": "record-me",
            "gpu": {
                "model": "record-me",
                "vram_mb": 1,
                "driver": "record-me",
                "cuda_runtime": "record-me",
            },
        },
        "release": {
            "git_commit": git_commit,
            "chart_version": "record-me",
            "values_sha256": "0" * 64,
            "images": {name: f"sha256:{'0' * 64}" for name in REQUIRED_IMAGE_NAMES},
        },
        "objectives": {"rto_seconds": 900, "rpo_seconds": 0},
        "drills": [
            {
                "id": drill_id,
                "status": "not_run",
                "started_at": None,
                "completed_at": None,
                "observed_rto_seconds": None,
                "observed_rpo_seconds": None,
                "run_ids": [],
                "artifact_ids": [],
                "evidence_refs": [],
                "notes": "",
            }
            for drill_id, _title in REQUIRED_DRILLS
        ],
        "attestation": {
            "operator": operator,
            "reviewed_at": None,
            "statement": (
                "I confirm that this record contains no credentials, signed URLs, "
                "private dataset content or raw Terraform state."
            ),
        },
    }


def load_evidence(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{path}: cannot read JSON: {exc}") from exc
    errors = validate_evidence(document)
    if errors:
        raise EvidenceError("\n".join(f"- {error}" for error in errors))
    if not isinstance(document, dict):
        raise EvidenceError(f"{path}: root must be an object")
    return document


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(document: Mapping[str, Any], *, source_name: str) -> str:
    failures = gate_failures(document)
    environment = document["environment"]
    gpu = environment["gpu"]
    release = document["release"]
    objectives = document["objectives"]
    lines = [
        f"# Production qualification — {_markdown(document['qualification_id'])}",
        "",
        "> Generated from the machine-readable qualification record. Edit the JSON source, not this report.",
        "",
        f"- Gate: **{'BLOCKED' if failures else 'PASSED'}**",
        f"- Evidence source: `{_markdown(source_name)}`",
        f"- Generated at: `{_markdown(document['generated_at'])}`",
        f"- Environment: `{_markdown(environment['name'])}` / `{_markdown(environment['cluster'])}`",
        f"- Namespace: `{_markdown(environment['namespace'])}`",
        f"- Kubernetes: `{_markdown(environment['kubernetes_version'])}`",
        (
            f"- GPU: `{_markdown(gpu['model'])}`, {gpu['vram_mb']} MiB, "
            f"driver `{_markdown(gpu['driver'])}`, CUDA `{_markdown(gpu['cuda_runtime'])}`"
        ),
        f"- Git commit: `{_markdown(release['git_commit'])}`",
        f"- Helm chart: `{_markdown(release['chart_version'])}`",
        f"- RTO/RPO objectives: {objectives['rto_seconds']} s / {objectives['rpo_seconds']} s",
        "",
        "## Immutable executor images",
        "",
        "| Executor | OCI digest |",
        "|---|---|",
    ]
    lines.extend(
        f"| {_markdown(name)} | `{_markdown(digest)}` |"
        for name, digest in sorted(release["images"].items())
    )
    lines.extend(
        [
            "",
            "## Drills",
            "",
            "| Drill | Status | RTO | RPO | Runs | Artifacts | Evidence |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for drill in document["drills"]:
        rto = "—" if drill["observed_rto_seconds"] is None else f"{drill['observed_rto_seconds']} s"
        rpo = "—" if drill["observed_rpo_seconds"] is None else f"{drill['observed_rpo_seconds']} s"
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown(DRILL_TITLES[drill["id"]]),
                    _markdown(drill["status"]),
                    rto,
                    rpo,
                    str(len(drill["run_ids"])),
                    str(len(drill["artifact_ids"])),
                    str(len(drill["evidence_refs"])),
                )
            )
            + " |"
        )
    lines.extend(["", "## Gate findings", ""])
    if failures:
        lines.extend(f"- {_markdown(failure)}" for failure in failures)
    else:
        lines.append("All required production drills passed within the recorded objectives.")
    lines.extend(
        [
            "",
            "## Attestation",
            "",
            f"Operator: `{_markdown(document['attestation']['operator'])}`  ",
            f"Reviewed at: `{_markdown(document['attestation']['reviewed_at'] or 'not reviewed')}`",
            "",
            _markdown(document["attestation"]["statement"]),
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _print_errors(prefix: str, errors: Sequence[str]) -> None:
    print(prefix)
    for error in errors:
        print(f"- {error}")


def _command_init(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if output.exists() and not args.force:
        print(f"Refusing to overwrite {output}; pass --force explicitly.")
        return 2
    document = new_draft(
        qualification_id=args.qualification_id,
        environment=args.environment,
        cluster=args.cluster,
        namespace=args.namespace,
        operator=args.operator,
        git_commit=args.git_commit or _git_commit(),
    )
    errors = validate_evidence(document)
    if errors:
        _print_errors("Cannot create invalid draft:", errors)
        return 2
    _write_json(output, document)
    print(f"Created non-passing qualification draft: {output}")
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        load_evidence(path)
    except EvidenceError as exc:
        print(f"Invalid qualification evidence {path}:\n{exc}")
        return 1
    print(f"Valid qualification evidence contract: {path}")
    return 0


def _command_gate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    try:
        document = load_evidence(path)
    except EvidenceError as exc:
        print(f"Invalid qualification evidence {path}:\n{exc}")
        return 1
    failures = gate_failures(document)
    if failures:
        _print_errors(f"Production gate BLOCKED for {path}:", failures)
        return 1
    print(f"Production gate PASSED for {path}")
    return 0


def _command_render(args: argparse.Namespace) -> int:
    source = Path(args.path)
    try:
        document = load_evidence(source)
    except EvidenceError as exc:
        print(f"Invalid qualification evidence {source}:\n{exc}")
        return 1
    report = render_markdown(document, source_name=source.name)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"Rendered qualification report: {output}")
    return 0


def _command_check_tree(args: argparse.Namespace) -> int:
    root = Path(args.path)
    evidence_files = sorted(root.rglob(f"*{EVIDENCE_SUFFIX}"))
    invalid = 0
    for path in evidence_files:
        try:
            load_evidence(path)
        except EvidenceError as exc:
            invalid += 1
            print(f"Invalid qualification evidence {path}:\n{exc}")
    if invalid:
        print(f"Found {invalid} invalid qualification evidence file(s).")
        return 1
    print(f"Validated {len(evidence_files)} qualification evidence file(s) under {root}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a safe, non-passing evidence draft")
    init_parser.add_argument("--qualification-id", required=True)
    init_parser.add_argument("--environment", required=True)
    init_parser.add_argument("--cluster", required=True)
    init_parser.add_argument("--namespace", required=True)
    init_parser.add_argument("--operator", required=True)
    init_parser.add_argument("--git-commit")
    init_parser.add_argument("--output", required=True)
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=_command_init)

    validate_parser = subparsers.add_parser("validate", help="validate structure and secret safety")
    validate_parser.add_argument("path")
    validate_parser.set_defaults(handler=_command_validate)

    gate_parser = subparsers.add_parser("gate", help="require every release drill to pass")
    gate_parser.add_argument("path")
    gate_parser.set_defaults(handler=_command_gate)

    render_parser = subparsers.add_parser("render", help="render a human-readable Markdown report")
    render_parser.add_argument("path")
    render_parser.add_argument("--output", required=True)
    render_parser.set_defaults(handler=_command_render)

    tree_parser = subparsers.add_parser("check-tree", help="validate all tracked evidence below a directory")
    tree_parser.add_argument("path", nargs="?", default="docs/benchmarks")
    tree_parser.set_defaults(handler=_command_check_tree)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
