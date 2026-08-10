"""Checksum-verified S3 workspace transfer between bounded stage Jobs."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from shared import storage
from shared.artifact_manifest import (
    ROLE_PATTERN,
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    ManifestParent,
    canonical_v2_bytes,
    parse_artifact_manifest,
)
from shared.checksums import sha256_file
from shared.config import S3_BUCKET

WORKSPACE_MANIFEST_VERSION = 1
MAX_OVERLAY_MANIFESTS = 64
MAX_MATERIALIZED_FILES = 100_000


@dataclass(frozen=True)
class PublishedWorkspace:
    manifest_key: str
    uri: str
    checksum_sha256: str
    size_bytes: int
    file_count: int
    uploaded_bytes: int = 0
    reused_bytes: int = 0
    upload_seconds: float = 0.0
    manifest_size_bytes: int = 0


@dataclass(frozen=True)
class RestoredWorkspace:
    size_bytes: int
    file_count: int
    downloaded_bytes: int
    reused_bytes: int
    download_seconds: float
    manifest_size_bytes: int
    manifest_schema_version: int = WORKSPACE_MANIFEST_VERSION


@dataclass(frozen=True)
class WorkspaceSelection:
    roles: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.roles and not self.paths:
            raise ValueError("Workspace selection requires at least one role or path")
        for role in self.roles:
            if not isinstance(role, str) or not ROLE_PATTERN.fullmatch(role):
                raise ValueError(f"Invalid workspace selection role: {role!r}")
        for path in self.paths:
            _safe_relative_path(path)

    def includes(self, entry: ManifestFile) -> bool:
        return entry.role in self.roles or entry.path in self.paths


def workspace_transfer_provenance(
    published: PublishedWorkspace,
    restored: RestoredWorkspace | None = None,
) -> dict[str, Any]:
    """Return the stable v1 transfer measurement stored in stage provenance."""

    transfer: dict[str, Any] = {
        "schema_version": 1,
        "manifest_schema_version": WORKSPACE_MANIFEST_VERSION,
        "publish": {
            "logical_bytes": published.size_bytes,
            "file_count": published.file_count,
            "transferred_bytes": published.uploaded_bytes,
            "reused_bytes": published.reused_bytes,
            "manifest_bytes": published.manifest_size_bytes,
            "duration_seconds": published.upload_seconds,
        },
    }
    if restored is not None:
        transfer["restore"] = {
            "manifest_schema_version": restored.manifest_schema_version,
            "logical_bytes": restored.size_bytes,
            "file_count": restored.file_count,
            "transferred_bytes": restored.downloaded_bytes,
            "reused_bytes": restored.reused_bytes,
            "manifest_bytes": restored.manifest_size_bytes,
            "duration_seconds": restored.download_seconds,
        }
    return transfer


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _safe_relative_path(raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise ValueError(f"Unsafe workspace manifest path: {raw_path!r}")
    pure = PurePosixPath(raw_path)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or any(part in {"", "."} for part in pure.parts)
        or pure.as_posix() != raw_path
    ):
        raise ValueError(f"Unsafe workspace manifest path: {raw_path!r}")
    return Path(*pure.parts)


def resolve_workspace_path(workspace: str | Path, raw_path: str) -> Path:
    """Resolve one manifest-style relative path below a local workspace."""
    root = Path(workspace).resolve()
    relative = _safe_relative_path(raw_path)
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise ValueError(f"Workspace path escapes its root: {raw_path!r}")
    return candidate


def publish_workspace(
    workspace: str | Path,
    s3_prefix: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> PublishedWorkspace:
    started_at = time.monotonic()
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    prefix = s3_prefix.strip("/")
    if not prefix:
        raise ValueError("Workspace S3 prefix cannot be empty")
    entries: list[dict[str, int | str]] = []
    for path in sorted(root.rglob("*")):
        if cancellation_check is not None:
            cancellation_check()
        if path.is_symlink():
            raise ValueError(f"Workspace cannot publish symbolic link: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        verified = storage.upload_verified_file(path, f"{prefix}/files/{relative}")
        entries.append(
            {
                "path": relative,
                "size": int(verified["size"]),
                "sha256": str(verified["sha256"]),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": WORKSPACE_MANIFEST_VERSION,
        "files": entries,
    }
    canonical = _canonical(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    manifest_key = f"{prefix}/manifest.json"
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="droneai-workspace-",
        suffix=".json",
        delete=False,
    ) as descriptor:
        manifest_path = Path(descriptor.name)
        descriptor.write(canonical)
    try:
        verified_manifest = storage.upload_verified_file(manifest_path, manifest_key)
        if verified_manifest["sha256"] != digest:
            raise OSError("Workspace manifest changed before S3 publication")
    finally:
        manifest_path.unlink(missing_ok=True)
    return PublishedWorkspace(
        manifest_key=manifest_key,
        uri=f"s3://{S3_BUCKET}/{manifest_key}",
        checksum_sha256=digest,
        size_bytes=sum(cast(int, entry["size"]) for entry in entries),
        file_count=len(entries),
        uploaded_bytes=(
            sum(cast(int, entry["size"]) for entry in entries)
            + int(verified_manifest["size"])
        ),
        reused_bytes=0,
        upload_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=int(verified_manifest["size"]),
    )


def publish_workspace_v2(
    workspace: str | Path,
    s3_prefix: str,
    *,
    default_role: str,
    role_overrides: dict[str, str] | None = None,
    parents: tuple[ManifestParent, ...] = (),
    cancellation_check: Callable[[], None] | None = None,
) -> PublishedWorkspace:
    """Publish a verified incremental CAS overlay.

    Unchanged parent files remain inherited. Deletion is deliberately
    unsupported until the manifest contract gains explicit tombstones.
    """

    started_at = time.monotonic()
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    prefix = s3_prefix.strip("/")
    if not prefix:
        raise ValueError("Workspace S3 prefix cannot be empty")
    overrides = role_overrides or {}
    for override_path, role in overrides.items():
        _safe_relative_path(override_path)
        if not ROLE_PATTERN.fullmatch(role):
            raise ValueError(f"Invalid workspace artifact role: {role!r}")
    if not ROLE_PATTERN.fullmatch(default_role):
        raise ValueError(f"Invalid workspace artifact role: {default_role!r}")

    inherited: dict[str, ManifestFile] = {}
    for parent in parents:
        _manifest, parent_files, _manifest_bytes = _load_manifest_overlay(
            parent.manifest_key,
            parent.checksum_sha256,
            cancellation_check=cancellation_check,
        )
        for relative, entry in parent_files.items():
            existing = inherited.get(relative)
            if existing is not None and existing != entry:
                raise ValueError(f"Conflicting parent overlay path: {relative}")
            inherited[relative] = entry

    files: list[ManifestFile] = []
    local_paths: set[str] = set()
    logical_bytes = 0
    transferred_bytes = 0
    reused_bytes = 0
    for file_path in sorted(root.rglob("*")):
        if cancellation_check is not None:
            cancellation_check()
        if file_path.is_symlink():
            raise ValueError(f"Workspace cannot publish symbolic link: {file_path}")
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(root).as_posix()
        local_paths.add(relative)
        size = file_path.stat().st_size
        digest = sha256_file(file_path)
        logical_bytes += size
        parent_entry = inherited.get(relative)
        if (
            parent_entry is not None
            and parent_entry.blob.size_bytes == size
            and parent_entry.blob.checksum_sha256 == digest
        ):
            reused_bytes += size
            continue
        uploaded = storage.publish_content_addressed_file(file_path)
        transferred_bytes += uploaded.transferred_bytes
        if uploaded.reused:
            reused_bytes += size
        files.append(
            ManifestFile(
                path=relative,
                role=overrides.get(relative, default_role),
                blob=ManifestBlob(
                    key=uploaded.key,
                    size_bytes=uploaded.size_bytes,
                    checksum_sha256=uploaded.checksum_sha256,
                ),
            )
        )

    missing_inherited = set(inherited) - local_paths
    if missing_inherited:
        raise ValueError(
            "Artifact Manifest v2 cannot implicitly delete inherited files: "
            f"{sorted(missing_inherited)}"
        )
    manifest = ArtifactManifest(2, tuple(files), parents)
    canonical = canonical_v2_bytes(manifest)
    digest = hashlib.sha256(canonical).hexdigest()
    manifest_key = f"{prefix}/manifest.json"
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="droneai-workspace-v2-",
        suffix=".json",
        delete=False,
    ) as descriptor:
        manifest_path = Path(descriptor.name)
        descriptor.write(canonical)
    try:
        verified_manifest = storage.upload_verified_file(manifest_path, manifest_key)
        if verified_manifest["sha256"] != digest:
            raise OSError("Workspace manifest changed before S3 publication")
    finally:
        manifest_path.unlink(missing_ok=True)
    manifest_size = int(verified_manifest["size"])
    return PublishedWorkspace(
        manifest_key=manifest_key,
        uri=f"s3://{S3_BUCKET}/{manifest_key}",
        checksum_sha256=digest,
        size_bytes=logical_bytes,
        file_count=len(local_paths),
        uploaded_bytes=transferred_bytes + manifest_size,
        reused_bytes=reused_bytes,
        upload_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=manifest_size,
    )


def _load_manifest_overlay(
    manifest_key: str,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[ArtifactManifest, dict[str, ManifestFile], int]:
    manifest_bytes_total = 0
    loaded_count = 0
    active: set[tuple[str, str]] = set()
    cache: dict[tuple[str, str], tuple[ArtifactManifest, dict[str, ManifestFile]]] = {}

    def load(key: str, checksum: str) -> tuple[ArtifactManifest, dict[str, ManifestFile]]:
        nonlocal manifest_bytes_total, loaded_count
        identity = (key, checksum)
        if identity in active:
            raise ValueError(f"Artifact manifest parent cycle includes {key}")
        if identity in cache:
            return cache[identity]
        loaded_count += 1
        if loaded_count > MAX_OVERLAY_MANIFESTS:
            raise ValueError("Artifact manifest overlay exceeds its manifest limit")
        if cancellation_check is not None:
            cancellation_check()
        with tempfile.NamedTemporaryFile(
            prefix="droneai-workspace-",
            suffix=".json",
            delete=False,
        ) as descriptor:
            path = Path(descriptor.name)
        try:
            storage.download_file(key, path)
            content = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        if digest != checksum:
            raise OSError(f"Workspace manifest checksum mismatch: {digest}/{checksum}")
        manifest_bytes_total += len(content)
        manifest = parse_artifact_manifest(content, manifest_key=key)
        active.add(identity)
        inherited: dict[str, ManifestFile] = {}
        try:
            for parent in manifest.parents:
                _parent_manifest, parent_files = load(
                    parent.manifest_key,
                    parent.checksum_sha256,
                )
                for file_path, entry in parent_files.items():
                    existing = inherited.get(file_path)
                    if existing is not None and existing != entry:
                        raise ValueError(f"Conflicting parent overlay path: {file_path}")
                    inherited[file_path] = entry
            for entry in manifest.files:
                inherited[entry.path] = entry
        finally:
            active.remove(identity)
        if len(inherited) > MAX_MATERIALIZED_FILES:
            raise ValueError("Artifact manifest overlay exceeds its file limit")
        cache[identity] = (manifest, inherited)
        return cache[identity]

    manifest, resolved = load(manifest_key, expected_checksum_sha256)
    return manifest, resolved, manifest_bytes_total


def restore_workspace_measured(
    manifest_key: str,
    destination: str | Path,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
    selection: WorkspaceSelection | None = None,
) -> RestoredWorkspace:
    started_at = time.monotonic()
    destination_root = Path(destination).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    manifest, resolved_files, manifest_bytes_total = _load_manifest_overlay(
        manifest_key,
        expected_checksum_sha256,
        cancellation_check=cancellation_check,
    )
    selected_files = [
        entry
        for entry in resolved_files.values()
        if selection is None or selection.includes(entry)
    ]
    if selection is not None:
        missing_paths = selection.paths - set(resolved_files)
        if missing_paths:
            raise ValueError(f"Workspace selection paths are missing: {sorted(missing_paths)}")
        if not selected_files:
            raise ValueError("Workspace selection matched no files")
    seen: set[str] = set()
    restored_bytes = 0
    for entry in sorted(selected_files, key=lambda item: item.path):
        if cancellation_check is not None:
            cancellation_check()
        relative_raw = entry.path
        expected_size = entry.blob.size_bytes
        expected_digest = entry.blob.checksum_sha256
        relative = _safe_relative_path(relative_raw)
        seen.add(relative_raw)
        local_path = destination_root / relative
        local_path.parent.mkdir(parents=True, exist_ok=True)
        storage.download_file(entry.blob.key, local_path)
        actual_size = local_path.stat().st_size
        actual_digest = str(sha256_file(local_path))
        if actual_size != expected_size or actual_digest != expected_digest:
            local_path.unlink(missing_ok=True)
            raise OSError(
                f"Workspace file verification failed for {relative_raw}: "
                f"size={actual_size}/{expected_size}, sha256={actual_digest}/{expected_digest}"
            )
        restored_bytes += actual_size
    return RestoredWorkspace(
        size_bytes=restored_bytes,
        file_count=len(seen),
        downloaded_bytes=manifest_bytes_total + restored_bytes,
        reused_bytes=0,
        download_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=manifest_bytes_total,
        manifest_schema_version=manifest.schema_version,
    )


def restore_workspace(
    manifest_key: str,
    destination: str | Path,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
    selection: WorkspaceSelection | None = None,
) -> int:
    """Restore a v1 workspace and preserve the legacy file-count return value."""

    return restore_workspace_measured(
        manifest_key,
        destination,
        expected_checksum_sha256,
        cancellation_check=cancellation_check,
        selection=selection,
    ).file_count
