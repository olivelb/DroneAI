"""Immutable contracts and object-store adapter for legacy adoption."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, cast

from sqlalchemy.orm import Session

from shared import storage


class SessionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Session]: ...


class AdoptionObjectStore(Protocol):
    def list_objects(self, prefix: str) -> list[str]: ...

    def object_info(self, key: str) -> Mapping[str, object] | None: ...

    def read_bytes(self, key: str) -> bytes: ...

    def copy(self, source_key: str, target_key: str) -> Mapping[str, object]: ...

    def put_bytes(self, key: str, data: bytes) -> Mapping[str, object]: ...


class S3AdoptionObjectStore:
    """Production adapter over the shared observed S3 boundary."""

    def list_objects(self, prefix: str) -> list[str]:
        return storage.list_objects(prefix)

    def object_info(self, key: str) -> Mapping[str, object] | None:
        return cast(
            Mapping[str, object] | None,
            storage.get_object_info(key),
        )

    def read_bytes(self, key: str) -> bytes:
        return storage.get_object_bytes(key)

    def copy(self, source_key: str, target_key: str) -> Mapping[str, object]:
        return cast(
            Mapping[str, object],
            storage.copy_verified_object(source_key, target_key),
        )

    def put_bytes(self, key: str, data: bytes) -> Mapping[str, object]:
        return cast(
            Mapping[str, object],
            storage.put_verified_bytes(key, data),
        )


@dataclass(frozen=True)
class ObjectIdentity:
    key: str
    size_bytes: int
    etag: str
    checksum_sha256: str | None


@dataclass(frozen=True)
class CopyIntent:
    source_key: str
    target_key: str
    size_bytes: int


@dataclass(frozen=True)
class ControlWrite:
    target_key: str
    payload: bytes
    checksum_sha256: str


@dataclass(frozen=True)
class ArtifactAdoption:
    database_id: int
    artifact_id: str
    target_manifest_key: str
    target_checksum_sha256: str
    target_schema_version: int


@dataclass(frozen=True)
class ResourceAdoption:
    kind: str
    database_id: int
    public_id: str
    source_prefix: str
    target_prefix: str
    source_objects: tuple[ObjectIdentity, ...]
    external_objects: tuple[ObjectIdentity, ...]
    copy_intents: tuple[CopyIntent, ...]
    control_writes: tuple[ControlWrite, ...]
    artifacts: tuple[ArtifactAdoption, ...] = ()

    @property
    def source_bytes(self) -> int:
        return sum(
            item.size_bytes
            for item in (*self.source_objects, *self.external_objects)
        )

    @property
    def source_object_count(self) -> int:
        return len(self.source_objects) + len(self.external_objects)

    @property
    def target_write_bytes(self) -> int:
        return sum(item.size_bytes for item in self.copy_intents) + sum(
            len(item.payload) for item in self.control_writes
        )


@dataclass(frozen=True)
class AdoptionPlan:
    run_id: str
    target_organization_id: str
    owner_subject: str
    actor_subject: str
    resources: tuple[ResourceAdoption, ...]
    plan_checksum_sha256: str
    logical_usage_bytes: int

    @property
    def source_object_count(self) -> int:
        return sum(resource.source_object_count for resource in self.resources)

    @property
    def source_bytes(self) -> int:
        return sum(resource.source_bytes for resource in self.resources)

    @property
    def target_write_bytes(self) -> int:
        return sum(resource.target_write_bytes for resource in self.resources)

    def public_summary(self, *, apply: bool) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "target_organization_id": self.target_organization_id,
            "owner_subject": self.owner_subject,
            "actor_subject": self.actor_subject,
            "apply": apply,
            "plan_checksum_sha256": self.plan_checksum_sha256,
            "source_object_count": self.source_object_count,
            "source_bytes": self.source_bytes,
            "target_write_bytes": self.target_write_bytes,
            "logical_usage_bytes": self.logical_usage_bytes,
            "resources": [
                {
                    "kind": resource.kind,
                    "public_id": resource.public_id,
                    "source_prefix": resource.source_prefix,
                    "target_prefix": resource.target_prefix,
                    "object_count": resource.source_object_count,
                    "source_bytes": resource.source_bytes,
                    "target_write_bytes": resource.target_write_bytes,
                    "artifact_count": len(resource.artifacts),
                }
                for resource in self.resources
            ],
            "source_retained": True,
        }
