"""Canonical, bounded provenance records for AI inference models."""

from __future__ import annotations

import hashlib
import json
import re
from importlib import metadata
from pathlib import Path
from typing import Any, cast


MODEL_MANIFEST_SCHEMA = "droneai.ai-model-provenance/v1"
MAX_MODEL_MANIFEST_BYTES = 16 * 1024
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def immutable_revision(value: str) -> str:
    """Return a normalized immutable Git revision or fail fast."""

    revision = str(value or "").strip().lower()
    if not _COMMIT_PATTERN.fullmatch(revision):
        raise ValueError("model revision must be a full 40-character Git commit SHA")
    return revision


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a model artifact without loading a multi-GiB weight into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def installed_versions(*distributions: str) -> dict[str, str]:
    """Capture installed library versions without importing heavy runtimes."""

    versions: dict[str, str] = {}
    for distribution in distributions:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = "unknown"
    return versions


def build_model_manifest(
    *,
    backend: str,
    repository: str,
    revision: str,
    artifact: str,
    artifact_sha256: str,
    libraries: dict[str, str],
    runtime: dict[str, Any],
    inference: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate the model provenance stored with an analysis run."""

    return validate_model_manifest(
        {
            "schema": MODEL_MANIFEST_SCHEMA,
            "backend": backend,
            "identity": {
                "repository": repository,
                "revision": revision,
                "artifact": artifact,
                "artifact_sha256": artifact_sha256,
            },
            "libraries": libraries,
            "runtime": runtime,
            "inference": inference,
        }
    )


def validate_model_manifest(value: Any) -> dict[str, Any]:
    """Validate untrusted event provenance and return a canonical deep copy."""

    if not isinstance(value, dict):
        raise ValueError("AI result is missing its model provenance manifest")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("AI model provenance manifest must be JSON serializable") from error
    if len(encoded) > MAX_MODEL_MANIFEST_BYTES:
        raise ValueError("AI model provenance manifest exceeds the 16 KiB limit")

    manifest = cast(dict[str, Any], json.loads(encoded))
    if manifest.get("schema") != MODEL_MANIFEST_SCHEMA:
        raise ValueError("unsupported AI model provenance schema")
    backend = manifest.get("backend")
    if backend not in {"yolo", "sam3"}:
        raise ValueError("unsupported AI model provenance backend")

    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("AI model provenance identity must be an object")
    for field in ("repository", "revision", "artifact", "artifact_sha256"):
        if not isinstance(identity.get(field), str) or not identity[field].strip():
            raise ValueError(f"AI model provenance identity requires {field}")
    if not _SHA256_PATTERN.fullmatch(identity["artifact_sha256"].lower()):
        raise ValueError("AI model artifact SHA-256 is invalid")
    identity["artifact_sha256"] = identity["artifact_sha256"].lower()
    if backend == "sam3":
        identity["revision"] = immutable_revision(identity["revision"])

    for field in ("libraries", "runtime", "inference"):
        if not isinstance(manifest.get(field), dict):
            raise ValueError(f"AI model provenance {field} must be an object")
    return manifest
