"""Conservative CAS mark-and-sweep planning for a quiescent organization."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from shared.artifact_manifest import ArtifactManifest


@dataclass(frozen=True)
class CasObject:
    key: str
    size: int
    modified_at: datetime


def plan_cas_collection(
    manifests: Mapping[str, ArtifactManifest],
    roots: Iterable[str],
    blobs: Iterable[CasObject],
    *,
    now: datetime,
    grace: timedelta,
    protected_keys: Iterable[str] = (),
) -> list[CasObject]:
    if grace < timedelta(days=7):
        raise ValueError("CAS collection requires at least seven days of grace")
    marked = set(protected_keys)
    pending = list(roots)
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        if key not in manifests:
            raise ValueError(f"Missing live/held parent manifest: {key}")
        visited.add(key)
        manifest = manifests[key]
        marked.update(item.blob.key for item in manifest.files)
        pending.extend(parent.manifest_key for parent in manifest.parents)
    return sorted((blob for blob in blobs if blob.key not in marked and blob.modified_at < now - grace), key=lambda blob: blob.key)
