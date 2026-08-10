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
from shared.checksums import sha256_file
from shared.config import S3_BUCKET

WORKSPACE_MANIFEST_VERSION = 1


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
    pure = PurePosixPath(raw_path)
    if (
        not raw_path
        or pure.is_absolute()
        or ".." in pure.parts
        or any(part in {"", "."} for part in pure.parts)
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


def restore_workspace_measured(
    manifest_key: str,
    destination: str | Path,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> RestoredWorkspace:
    started_at = time.monotonic()
    destination_root = Path(destination).resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="droneai-workspace-",
        suffix=".json",
        delete=False,
    ) as manifest_descriptor:
        manifest_path = Path(manifest_descriptor.name)
    try:
        storage.download_file(manifest_key, manifest_path)
        manifest_bytes = manifest_path.read_bytes()
    finally:
        manifest_path.unlink(missing_ok=True)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest != expected_checksum_sha256:
        raise OSError(
            f"Workspace manifest checksum mismatch: {digest}/{expected_checksum_sha256}"
        )
    payload = json.loads(manifest_bytes)
    if not isinstance(payload, dict) or payload.get("schema_version") != WORKSPACE_MANIFEST_VERSION:
        raise ValueError("Unsupported workspace manifest schema")
    raw_entries = payload.get("files")
    if not isinstance(raw_entries, list):
        raise ValueError("Workspace manifest files must be a list")
    prefix = manifest_key.removesuffix("/manifest.json").rstrip("/")
    seen: set[str] = set()
    restored_bytes = 0
    for raw_entry in raw_entries:
        if cancellation_check is not None:
            cancellation_check()
        if not isinstance(raw_entry, dict):
            raise ValueError("Workspace manifest entry must be an object")
        relative_raw = raw_entry.get("path")
        expected_size = raw_entry.get("size")
        expected_digest = raw_entry.get("sha256")
        if (
            not isinstance(relative_raw, str)
            or not isinstance(expected_size, int)
            or expected_size < 0
            or not isinstance(expected_digest, str)
            or len(expected_digest) != 64
        ):
            raise ValueError("Workspace manifest entry is invalid")
        relative = _safe_relative_path(relative_raw)
        if relative_raw in seen:
            raise ValueError(f"Duplicate workspace manifest path: {relative_raw}")
        seen.add(relative_raw)
        local_path = destination_root / relative
        local_path.parent.mkdir(parents=True, exist_ok=True)
        storage.download_file(f"{prefix}/files/{relative_raw}", local_path)
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
        downloaded_bytes=len(manifest_bytes) + restored_bytes,
        reused_bytes=0,
        download_seconds=round(time.monotonic() - started_at, 6),
        manifest_size_bytes=len(manifest_bytes),
    )


def restore_workspace(
    manifest_key: str,
    destination: str | Path,
    expected_checksum_sha256: str,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> int:
    """Restore a v1 workspace and preserve the legacy file-count return value."""

    return restore_workspace_measured(
        manifest_key,
        destination,
        expected_checksum_sha256,
        cancellation_check=cancellation_check,
    ).file_count
