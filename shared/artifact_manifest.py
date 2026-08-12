"""Versioned, normalized workspace artifact manifest contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from shared.tenancy import LEGACY_ORGANIZATION_ID, validate_organization_id


LEGACY_MANIFEST_VERSION: Final = 1
ARTIFACT_MANIFEST_VERSION: Final = 2
TENANT_ARTIFACT_MANIFEST_VERSION: Final = 3
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
ROLE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


def content_addressed_blob_key(
    checksum_sha256: str,
    *,
    organization_id: str | None = None,
) -> str:
    """Return a legacy-global or tenant-scoped key for a SHA-256 blob."""

    checksum = _sha256(checksum_sha256, "checksum_sha256")
    if organization_id is not None:
        organization = validate_organization_id(organization_id)
        if organization != LEGACY_ORGANIZATION_ID:
            return (
                f"organizations/{organization}/blobs/sha256/"
                f"{checksum[:2]}/{checksum}"
            )
    return f"blobs/sha256/{checksum[:2]}/{checksum}"


@dataclass(frozen=True)
class ManifestBlob:
    key: str
    size_bytes: int
    checksum_sha256: str


@dataclass(frozen=True)
class ManifestFile:
    path: str
    role: str
    blob: ManifestBlob


@dataclass(frozen=True)
class ManifestParent:
    artifact_id: str
    manifest_key: str
    checksum_sha256: str


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: int
    files: tuple[ManifestFile, ...]
    parents: tuple[ManifestParent, ...] = ()
    organization_id: str | None = None


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise ValueError(f"{path} fields are invalid ({', '.join(details)})")


def _safe_relative_path(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{path} must be a non-empty POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or any(
        part in {"", "."} for part in pure.parts
    ) or pure.as_posix() != value:
        raise ValueError(f"{path} is unsafe: {value!r}")
    return pure.as_posix()


def _safe_object_key(value: object, path: str) -> str:
    key = _safe_relative_path(value, path)
    if "://" in key:
        raise ValueError(f"{path} must be an object key, not a URI")
    return key


def _sha256(value: object, path: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{path} must be a lower-case SHA-256")
    return value


def _size(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{path} must be a non-negative integer")
    return value


def _legacy_manifest(payload: Mapping[str, Any], manifest_key: str) -> ArtifactManifest:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("Legacy workspace manifest files must be an array")
    prefix = manifest_key.removesuffix("/manifest.json").rstrip("/")
    if not prefix:
        raise ValueError("Legacy workspace manifest key has no object prefix")
    files: list[ManifestFile] = []
    seen: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        path = f"files[{index}]"
        item = _object(raw_file, path)
        file_path = _safe_relative_path(item.get("path"), f"{path}.path")
        if file_path in seen:
            raise ValueError(f"Duplicate workspace manifest path: {file_path}")
        seen.add(file_path)
        files.append(
            ManifestFile(
                path=file_path,
                role="legacy",
                blob=ManifestBlob(
                    key=f"{prefix}/files/{file_path}",
                    size_bytes=_size(item.get("size"), f"{path}.size"),
                    checksum_sha256=_sha256(item.get("sha256"), f"{path}.sha256"),
                ),
            )
        )
    return ArtifactManifest(schema_version=LEGACY_MANIFEST_VERSION, files=tuple(files))


def _versioned_blob(
    value: object,
    path: str,
    organization_id: str | None,
) -> ManifestBlob:
    blob = _object(value, path)
    _exact_keys(blob, {"key", "size", "sha256"}, path)
    checksum = _sha256(blob.get("sha256"), f"{path}.sha256")
    key = _safe_object_key(blob.get("key"), f"{path}.key")
    expected_key = content_addressed_blob_key(
        checksum,
        organization_id=organization_id,
    )
    if key != expected_key:
        raise ValueError(f"{path}.key is not content-addressed by its SHA-256")
    return ManifestBlob(
        key=key,
        size_bytes=_size(blob.get("size"), f"{path}.size"),
        checksum_sha256=checksum,
    )


def _versioned_manifest(
    payload: Mapping[str, Any],
    *,
    schema_version: int,
    organization_id: str | None,
) -> ArtifactManifest:
    expected_fields = {"schema_version", "files", "parents"}
    if schema_version == TENANT_ARTIFACT_MANIFEST_VERSION:
        expected_fields.add("organization_id")
    _exact_keys(payload, expected_fields, "manifest")
    raw_files = payload.get("files")
    raw_parents = payload.get("parents")
    if not isinstance(raw_files, list):
        raise ValueError("manifest.files must be an array")
    if not isinstance(raw_parents, list):
        raise ValueError("manifest.parents must be an array")

    files: list[ManifestFile] = []
    seen_paths: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        path = f"manifest.files[{index}]"
        item = _object(raw_file, path)
        _exact_keys(item, {"path", "role", "blob"}, path)
        file_path = _safe_relative_path(item.get("path"), f"{path}.path")
        if file_path in seen_paths:
            raise ValueError(f"Duplicate workspace manifest path: {file_path}")
        seen_paths.add(file_path)
        role = item.get("role")
        if not isinstance(role, str) or not ROLE_PATTERN.fullmatch(role):
            raise ValueError(f"{path}.role is invalid")
        files.append(
            ManifestFile(
                path=file_path,
                role=role,
                blob=_versioned_blob(
                    item.get("blob"),
                    f"{path}.blob",
                    organization_id,
                ),
            )
        )

    parents: list[ManifestParent] = []
    seen_parents: set[str] = set()
    for index, raw_parent in enumerate(raw_parents):
        path = f"manifest.parents[{index}]"
        parent = _object(raw_parent, path)
        _exact_keys(parent, {"artifact_id", "manifest_key", "checksum_sha256"}, path)
        artifact_id = parent.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id or len(artifact_id) > 128:
            raise ValueError(f"{path}.artifact_id is invalid")
        if artifact_id in seen_parents:
            raise ValueError(f"Duplicate parent artifact: {artifact_id}")
        seen_parents.add(artifact_id)
        manifest_key = _safe_object_key(parent.get("manifest_key"), f"{path}.manifest_key")
        if not manifest_key.endswith("/manifest.json"):
            raise ValueError(f"{path}.manifest_key must end with /manifest.json")
        parents.append(
            ManifestParent(
                artifact_id=artifact_id,
                manifest_key=manifest_key,
                checksum_sha256=_sha256(
                    parent.get("checksum_sha256"),
                    f"{path}.checksum_sha256",
                ),
            )
        )
    return ArtifactManifest(
        schema_version=schema_version,
        files=tuple(files),
        parents=tuple(parents),
        organization_id=organization_id,
    )


def parse_artifact_manifest(content: bytes, *, manifest_key: str) -> ArtifactManifest:
    """Parse and normalize the deployed v1, global v2, or tenant v3 format."""

    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Workspace manifest is not valid UTF-8 JSON") from exc
    root = _object(payload, "manifest")
    version = root.get("schema_version")
    if version == LEGACY_MANIFEST_VERSION:
        return _legacy_manifest(root, manifest_key)
    if version == ARTIFACT_MANIFEST_VERSION:
        return _versioned_manifest(
            root,
            schema_version=ARTIFACT_MANIFEST_VERSION,
            organization_id=None,
        )
    if version == TENANT_ARTIFACT_MANIFEST_VERSION:
        organization = root.get("organization_id")
        if not isinstance(organization, str):
            raise ValueError("manifest.organization_id is invalid")
        organization = validate_organization_id(organization)
        if organization == LEGACY_ORGANIZATION_ID:
            raise ValueError(
                "Artifact Manifest v3 requires a non-legacy organization"
            )
        return _versioned_manifest(
            root,
            schema_version=TENANT_ARTIFACT_MANIFEST_VERSION,
            organization_id=organization,
        )
    raise ValueError(f"Unsupported workspace manifest schema: {version!r}")


def canonical_v2_bytes(manifest: ArtifactManifest) -> bytes:
    """Serialize a normalized v2 manifest deterministically."""

    if manifest.schema_version != ARTIFACT_MANIFEST_VERSION:
        raise ValueError("Only Artifact Manifest v2 can be serialized by this writer")
    if manifest.organization_id is not None:
        raise ValueError("Artifact Manifest v2 cannot declare organization_id")
    ordered = ArtifactManifest(
        schema_version=ARTIFACT_MANIFEST_VERSION,
        files=tuple(sorted(manifest.files, key=lambda item: item.path)),
        parents=tuple(sorted(manifest.parents, key=lambda item: item.artifact_id)),
    )
    payload = {
        "schema_version": ARTIFACT_MANIFEST_VERSION,
        "files": [
            {
                "path": item.path,
                "role": item.role,
                "blob": {
                    "key": item.blob.key,
                    "size": item.blob.size_bytes,
                    "sha256": item.blob.checksum_sha256,
                },
            }
            for item in ordered.files
        ],
        "parents": [
            {
                "artifact_id": parent.artifact_id,
                "manifest_key": parent.manifest_key,
                "checksum_sha256": parent.checksum_sha256,
            }
            for parent in ordered.parents
        ],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if parse_artifact_manifest(canonical, manifest_key="v2/manifest.json") != ordered:
        raise ValueError("Artifact Manifest v2 is not canonicalizable")
    return canonical


def canonical_v3_bytes(manifest: ArtifactManifest) -> bytes:
    """Serialize a tenant-scoped v3 manifest deterministically."""

    if manifest.schema_version != TENANT_ARTIFACT_MANIFEST_VERSION:
        raise ValueError("Only Artifact Manifest v3 can be serialized by this writer")
    if manifest.organization_id is None:
        raise ValueError("Artifact Manifest v3 requires organization_id")
    organization = validate_organization_id(manifest.organization_id)
    if organization == LEGACY_ORGANIZATION_ID:
        raise ValueError("Artifact Manifest v3 requires a non-legacy organization")
    ordered = ArtifactManifest(
        schema_version=TENANT_ARTIFACT_MANIFEST_VERSION,
        files=tuple(sorted(manifest.files, key=lambda item: item.path)),
        parents=tuple(sorted(manifest.parents, key=lambda item: item.artifact_id)),
        organization_id=organization,
    )
    payload = {
        "schema_version": TENANT_ARTIFACT_MANIFEST_VERSION,
        "organization_id": organization,
        "files": [
            {
                "path": item.path,
                "role": item.role,
                "blob": {
                    "key": item.blob.key,
                    "size": item.blob.size_bytes,
                    "sha256": item.blob.checksum_sha256,
                },
            }
            for item in ordered.files
        ],
        "parents": [
            {
                "artifact_id": parent.artifact_id,
                "manifest_key": parent.manifest_key,
                "checksum_sha256": parent.checksum_sha256,
            }
            for parent in ordered.parents
        ],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if parse_artifact_manifest(canonical, manifest_key="v3/manifest.json") != ordered:
        raise ValueError("Artifact Manifest v3 is not canonicalizable")
    return canonical


def validate_parent_graph(manifests: Mapping[str, ArtifactManifest]) -> None:
    """Reject cycles among the supplied artifact manifests."""

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visiting:
            raise ValueError(f"Artifact manifest parent cycle includes {artifact_id}")
        if artifact_id in visited:
            return
        manifest = manifests[artifact_id]
        visiting.add(artifact_id)
        for parent in manifest.parents:
            if parent.artifact_id in manifests:
                visit(parent.artifact_id)
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in sorted(manifests):
        visit(artifact_id)
