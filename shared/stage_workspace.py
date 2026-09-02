"""Checksum-verified S3 workspace transfer between bounded stage Jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from shared import storage
from shared.artifact_manifest import (
    ARTIFACT_MANIFEST_VERSION,
    ROLE_PATTERN,
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    ManifestParent,
    canonical_v3_bytes,
    parse_artifact_manifest,
    validate_cas_organization,
)
from shared.checksums import sha256_file
from shared.config import S3_BUCKET

MAX_OVERLAY_MANIFESTS = 64
MAX_MATERIALIZED_FILES = 100_000
ARTIFACT_SELECTIVE_RESTORE_ENV = "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED"
VERIFICATION_CACHE_FLUSH_BYTES = 64 * 1024 * 1024
VERIFICATION_CACHE_FLUSH_FILE_COUNT = 64

logger = logging.getLogger(__name__)


def _strict_boolean_environment(name: str) -> bool:
    raw = os.getenv(name, "false").strip().lower()
    if raw in {"false", "0", "no", "off"}:
        return False
    if raw in {"true", "1", "yes", "on"}:
        return True
    raise ValueError(f"{name} must be an explicit boolean, not {raw!r}")


def artifact_selective_restore_enabled() -> bool:
    """Return the strict detection-only selective-restore switch."""
    return _strict_boolean_environment(ARTIFACT_SELECTIVE_RESTORE_ENV)


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
    manifest_schema_version: int = ARTIFACT_MANIFEST_VERSION


@dataclass(frozen=True)
class RestoredWorkspace:
    size_bytes: int
    file_count: int
    downloaded_bytes: int
    reused_bytes: int
    download_seconds: float
    manifest_size_bytes: int
    manifest_schema_version: int = ARTIFACT_MANIFEST_VERSION
    pruned_file_count: int = 0
    pruned_bytes: int = 0


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
        "manifest_schema_version": published.manifest_schema_version,
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
            "pruned_files": restored.pruned_file_count,
            "pruned_bytes": restored.pruned_bytes,
        }
    return transfer


def _upload_workspace_manifest(
    canonical: bytes,
    manifest_key: str,
    expected_digest: str,
    *,
    temporary_prefix: str,
) -> dict[str, int | str]:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=temporary_prefix,
        suffix=".json",
        delete=False,
    ) as descriptor:
        manifest_path = Path(descriptor.name)
        descriptor.write(canonical)
    try:
        verified = storage.upload_verified_file(manifest_path, manifest_key)
        if verified["sha256"] != expected_digest:
            raise OSError("Workspace manifest changed before S3 publication")
        return verified
    finally:
        manifest_path.unlink(missing_ok=True)


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


def _role_only_parent_override(
    relative_path: str,
    size: int,
    desired_role: str,
    parent_entry: ManifestFile | None,
) -> ManifestFile | None:
    if parent_entry is None:
        raise ValueError(
            f"Declared unchanged workspace path has no parent: {relative_path}"
        )
    if parent_entry.blob.size_bytes != size:
        raise ValueError(
            f"Declared unchanged workspace path changed size: {relative_path}"
        )
    if parent_entry.role == desired_role:
        return None
    return ManifestFile(
        path=relative_path,
        role=desired_role,
        blob=parent_entry.blob,
    )


def _validate_publish_roles(
    default_role: str,
    role_overrides: dict[str, str] | None,
    unchanged_parent_paths: frozenset[str],
) -> dict[str, str]:
    overrides = role_overrides or {}
    for override_path, role in overrides.items():
        _safe_relative_path(override_path)
        if not ROLE_PATTERN.fullmatch(role):
            raise ValueError(f"Invalid workspace artifact role: {role!r}")
    if not ROLE_PATTERN.fullmatch(default_role):
        raise ValueError(f"Invalid workspace artifact role: {default_role!r}")
    for unchanged_path in unchanged_parent_paths:
        _safe_relative_path(unchanged_path)
    return overrides


def publish_workspace(
    workspace: str | Path,
    s3_prefix: str,
    *,
    default_role: str,
    role_overrides: dict[str, str] | None = None,
    parents: tuple[ManifestParent, ...] = (),
    allow_partial_workspace: bool = False,
    unchanged_parent_paths: frozenset[str] = frozenset(),
    organization_id: str,
    cancellation_check: Callable[[], None] | None = None,
) -> PublishedWorkspace:
    """Publish a verified incremental CAS overlay.

    Unchanged parent files remain inherited. Deletion is deliberately
    unsupported until the manifest contract gains explicit tombstones.
    """

    started_at = time.monotonic()
    tenant_organization_id = validate_cas_organization(organization_id)
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    prefix = s3_prefix.strip("/")
    if not prefix:
        raise ValueError("Workspace S3 prefix cannot be empty")
    overrides = _validate_publish_roles(
        default_role,
        role_overrides,
        unchanged_parent_paths,
    )

    inherited: dict[str, ManifestFile] = {}
    for parent in parents:
        _manifest, parent_files, _manifest_bytes = _load_manifest_overlay(
            parent.manifest_key,
            parent.checksum_sha256,
            expected_organization_id=tenant_organization_id,
            cancellation_check=cancellation_check,
        )
        for relative, entry in parent_files.items():
            existing = inherited.get(relative)
            if existing is not None and existing != entry:
                raise ValueError(f"Conflicting parent overlay path: {relative}")
            inherited[relative] = entry

    files: list[ManifestFile] = []
    local_paths: set[str] = set()
    transferred_bytes = 0
    cas_reused_bytes = 0
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
        parent_entry = inherited.get(relative)
        desired_role = overrides.get(relative, default_role)
        if relative in unchanged_parent_paths:
            role_override = _role_only_parent_override(
                relative,
                size,
                desired_role,
                parent_entry,
            )
            if role_override is not None:
                files.append(role_override)
                cas_reused_bytes += size
            continue
        digest = sha256_file(file_path)
        if (
            parent_entry is not None
            and parent_entry.blob.size_bytes == size
            and parent_entry.blob.checksum_sha256 == digest
        ):
            continue
        uploaded = storage.publish_content_addressed_file(
            file_path,
            organization_id=tenant_organization_id,
            cancellation_check=cancellation_check,
        )
        transferred_bytes += uploaded.transferred_bytes
        if uploaded.reused:
            cas_reused_bytes += size
        entry = ManifestFile(
            path=relative,
            role=desired_role,
            blob=ManifestBlob(
                key=uploaded.key,
                size_bytes=uploaded.size_bytes,
                checksum_sha256=uploaded.checksum_sha256,
            ),
        )
        files.append(entry)

    missing_unchanged = unchanged_parent_paths - local_paths
    if missing_unchanged:
        raise ValueError(
            f"Declared unchanged workspace paths are missing: {sorted(missing_unchanged)}"
        )
    missing_inherited = set(inherited) - local_paths
    if missing_inherited and not allow_partial_workspace:
        raise ValueError(
            "A versioned Artifact Manifest cannot implicitly delete inherited "
            f"files: {sorted(missing_inherited)}"
        )
    resolved = dict(inherited)
    resolved.update((entry.path, entry) for entry in files)
    changed_paths = {entry.path for entry in files}
    inherited_reused_bytes = sum(
        entry.blob.size_bytes
        for relative, entry in inherited.items()
        if relative not in changed_paths
    )
    manifest = ArtifactManifest(
        ARTIFACT_MANIFEST_VERSION,
        tuple(files),
        parents,
        organization_id=tenant_organization_id,
    )
    canonical = canonical_v3_bytes(manifest)
    digest = hashlib.sha256(canonical).hexdigest()
    manifest_key = f"{prefix}/manifest.json"
    verified_manifest = _upload_workspace_manifest(
        canonical,
        manifest_key,
        digest,
        temporary_prefix="droneai-workspace-v3-",
    )
    manifest_size = int(verified_manifest["size"])
    return PublishedWorkspace(
        manifest_key=manifest_key,
        uri=f"s3://{S3_BUCKET}/{manifest_key}",
        checksum_sha256=digest,
        size_bytes=sum(entry.blob.size_bytes for entry in resolved.values()),
        file_count=len(resolved),
        uploaded_bytes=transferred_bytes + manifest_size,
        reused_bytes=inherited_reused_bytes + cas_reused_bytes,
        upload_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=manifest_size,
        manifest_schema_version=manifest.schema_version,
    )


def _load_manifest_overlay(
    manifest_key: str,
    expected_checksum_sha256: str,
    *,
    expected_organization_id: str,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[ArtifactManifest, dict[str, ManifestFile], int]:
    expected_organization_id = validate_cas_organization(expected_organization_id)
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
        manifest = parse_artifact_manifest(content)
        if manifest.organization_id != expected_organization_id:
            raise ValueError(
                "Artifact manifest organization does not match the request tenant"
            )
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


def resolve_workspace_files(
    manifest_key: str,
    expected_checksum_sha256: str,
    *,
    expected_organization_id: str,
) -> dict[str, ManifestFile]:
    """Resolve an immutable workspace overlay without materializing its blobs.

    Read-only consumers such as the map API need the verified object key for a
    small number of published products.  Reusing the same overlay validation as
    stage restoration keeps parent traversal, checksum verification and path
    conflict handling identical without copying a complete workspace.
    """

    _manifest, resolved, _manifest_bytes = _load_manifest_overlay(
        manifest_key,
        expected_checksum_sha256,
        expected_organization_id=expected_organization_id,
    )
    return dict(resolved)


def _prune_unmanaged_workspace_files(
    root: Path,
    managed_paths: set[str],
) -> tuple[int, int]:
    """Remove everything outside an authoritative materialized manifest."""

    pruned_file_count = 0
    pruned_bytes = 0
    for directory, directory_names, file_names in os.walk(
        root, topdown=False, followlinks=False
    ):
        current = Path(directory)
        for name in file_names:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if relative in managed_paths and not path.is_symlink():
                continue
            stat = path.lstat()
            pruned_bytes += stat.st_size
            path.unlink()
            pruned_file_count += 1
        for name in directory_names:
            path = current / name
            if path.is_symlink():
                stat = path.lstat()
                pruned_bytes += stat.st_size
                path.unlink()
                pruned_file_count += 1
            else:
                with suppress(OSError):
                    path.rmdir()
    return pruned_file_count, pruned_bytes


def _download_workspace_file(
    entry: ManifestFile,
    local_path: Path,
) -> None:
    """Download and verify one blob before atomically exposing its final path."""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=local_path.parent,
            prefix=f".{local_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as descriptor:
            temporary_path = Path(descriptor.name)
        storage.download_file(entry.blob.key, temporary_path)
        actual_size = temporary_path.stat().st_size
        actual_digest = str(sha256_file(temporary_path))
        if (
            actual_size != entry.blob.size_bytes
            or actual_digest != entry.blob.checksum_sha256
        ):
            raise OSError(
                f"Workspace file verification failed for {entry.path}: "
                f"size={actual_size}/{entry.blob.size_bytes}, "
                f"sha256={actual_digest}/{entry.blob.checksum_sha256}"
            )
        os.replace(temporary_path, local_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _file_verification_fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
    }


def _load_verification_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return {}
    files = raw.get("files")
    if not isinstance(files, dict):
        return {}
    return {
        relative: entry
        for relative, entry in files.items()
        if isinstance(relative, str) and isinstance(entry, dict)
    }


def _cached_file_is_verified(
    path: Path,
    entry: ManifestFile,
    cached: dict[str, Any] | None,
) -> bool:
    if cached is None:
        return False
    if cached.get("checksum_sha256") != entry.blob.checksum_sha256:
        return False
    if cached.get("size_bytes") != entry.blob.size_bytes:
        return False
    try:
        fingerprint = _file_verification_fingerprint(path)
    except OSError:
        return False
    return all(cached.get(key) == value for key, value in fingerprint.items())


def _verification_cache_entry(path: Path, entry: ManifestFile) -> dict[str, Any]:
    return {
        "checksum_sha256": entry.blob.checksum_sha256,
        "size_bytes": entry.blob.size_bytes,
        **_file_verification_fingerprint(path),
    }


def _store_verification_cache(
    path: Path | None,
    files: dict[str, dict[str, Any]],
) -> None:
    if path is None:
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as descriptor:
            temporary_path = Path(descriptor.name)
            json.dump(
                {"version": 1, "files": files},
                descriptor,
                sort_keys=True,
                separators=(",", ":"),
            )
            descriptor.flush()
            os.fsync(descriptor.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _persist_verification_cache_best_effort(
    cache_path: Path | None,
    cache: dict[str, dict[str, Any]],
) -> None:
    try:
        _store_verification_cache(cache_path, cache)
    except OSError as error:
        logger.warning(
            "Could not persist workspace verification cache %s: %s",
            cache_path,
            error,
        )


def _remember_verified_file(
    cache_path: Path | None,
    cache: dict[str, dict[str, Any]],
    relative_path: str,
    local_path: Path,
    entry: ManifestFile,
    dirty_file_count: int,
    dirty_bytes: int,
) -> tuple[int, int]:
    if cache_path is None:
        return 0, 0
    cache[relative_path] = _verification_cache_entry(local_path, entry)
    dirty_file_count += 1
    dirty_bytes += entry.blob.size_bytes
    if (
        dirty_file_count >= VERIFICATION_CACHE_FLUSH_FILE_COUNT
        or dirty_bytes >= VERIFICATION_CACHE_FLUSH_BYTES
    ):
        _persist_verification_cache_best_effort(cache_path, cache)
        return 0, 0
    return dirty_file_count, dirty_bytes


def restore_workspace_measured(
    manifest_key: str,
    destination: str | Path,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
    selection: WorkspaceSelection | None = None,
    expected_organization_id: str,
    exact_inventory: bool = False,
    verification_cache_path: str | Path | None = None,
) -> RestoredWorkspace:
    started_at = time.monotonic()
    destination_root = Path(destination).resolve()
    cache_path = (
        Path(verification_cache_path).resolve() if verification_cache_path else None
    )
    verification_cache = _load_verification_cache(cache_path)
    destination_root.mkdir(parents=True, exist_ok=True)
    manifest, resolved_files, manifest_bytes_total = _load_manifest_overlay(
        manifest_key,
        expected_checksum_sha256,
        expected_organization_id=expected_organization_id,
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
    if exact_inventory and selection is not None:
        raise ValueError("Exact workspace inventory cannot use a partial selection")
    managed_paths = {entry.path for entry in selected_files}
    pruned_file_count, pruned_bytes = (0, 0)
    if exact_inventory:
        pruned_file_count, pruned_bytes = _prune_unmanaged_workspace_files(
            destination_root, managed_paths
        )
    seen: set[str] = set()
    logical_bytes = 0
    downloaded_bytes = 0
    reused_bytes = 0
    cache_dirty_file_count = 0
    cache_dirty_bytes = 0
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
        logical_bytes += expected_size
        if local_path.is_file() and local_path.stat().st_size == expected_size:
            if _cached_file_is_verified(
                local_path,
                entry,
                verification_cache.get(relative_raw),
            ):
                reused_bytes += expected_size
                continue
            actual_digest = str(sha256_file(local_path))
            if actual_digest == expected_digest:
                cache_dirty_file_count, cache_dirty_bytes = _remember_verified_file(
                    cache_path,
                    verification_cache,
                    relative_raw,
                    local_path,
                    entry,
                    cache_dirty_file_count,
                    cache_dirty_bytes,
                )
                reused_bytes += expected_size
                continue
        _download_workspace_file(entry, local_path)
        cache_dirty_file_count, cache_dirty_bytes = _remember_verified_file(
            cache_path,
            verification_cache,
            relative_raw,
            local_path,
            entry,
            cache_dirty_file_count,
            cache_dirty_bytes,
        )
        downloaded_bytes += expected_size
    if cache_dirty_file_count:
        _persist_verification_cache_best_effort(cache_path, verification_cache)
    return RestoredWorkspace(
        size_bytes=logical_bytes,
        file_count=len(seen),
        downloaded_bytes=manifest_bytes_total + downloaded_bytes,
        reused_bytes=reused_bytes,
        download_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=manifest_bytes_total,
        manifest_schema_version=manifest.schema_version,
        pruned_file_count=pruned_file_count,
        pruned_bytes=pruned_bytes,
    )
