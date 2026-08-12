"""Organization boundary and versioned object-key helpers."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


LEGACY_ORGANIZATION_ID = "legacy-unassigned"
LOCAL_ORGANIZATION_ID = "local-development"

_CURRENT_ORGANIZATION_ID: ContextVar[str | None] = ContextVar(
    "droneai_current_organization_id",
    default=None,
)
ORGANIZATION_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


def _object_key_component(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise ValueError(f"{field_name} must be one safe object-key component")
    return normalized


def validate_organization_id(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(ORGANIZATION_ID_PATTERN, normalized):
        raise ValueError(
            "organization_id must be a lower-case DNS-like identifier of 1-64 characters"
        )
    return normalized


def current_organization_id() -> str | None:
    """Return the organization bound to the current request task, if any."""

    return _CURRENT_ORGANIZATION_ID.get()


def bind_organization_id(organization_id: str) -> Token[str | None]:
    """Bind one validated organization until ``reset_organization_id``."""

    return _CURRENT_ORGANIZATION_ID.set(
        validate_organization_id(organization_id)
    )


def reset_organization_id(token: Token[str | None]) -> None:
    """Restore the organization context that preceded ``token``."""

    _CURRENT_ORGANIZATION_ID.reset(token)


@contextmanager
def organization_context(organization_id: str) -> Iterator[None]:
    """Bind one organization for nested synchronous database work."""

    token = bind_organization_id(organization_id)
    try:
        yield
    finally:
        reset_organization_id(token)


def dataset_prefix(organization_id: str, dataset_name: str) -> str:
    """Return the legacy prefix or the organization-scoped v2 prefix."""

    organization = validate_organization_id(organization_id)
    dataset = _object_key_component(dataset_name, field_name="dataset_name")
    if organization == LEGACY_ORGANIZATION_ID:
        return f"datasets/{dataset}"
    return f"organizations/{organization}/datasets/{dataset}"


def mission_prefix(organization_id: str, vol_id: str) -> str:
    organization = validate_organization_id(organization_id)
    mission_id = _object_key_component(vol_id, field_name="vol_id")
    if organization == LEGACY_ORGANIZATION_ID:
        return f"missions/{mission_id}"
    return f"organizations/{organization}/missions/{mission_id}"


@dataclass(frozen=True)
class MissionObjectNamespace:
    """Durable, tenant-bound namespace for every object owned by one mission."""

    organization_id: str
    vol_id: str
    root: str

    @classmethod
    def create(
        cls,
        organization_id: str,
        vol_id: str,
    ) -> MissionObjectNamespace:
        canonical = mission_prefix(organization_id, vol_id)
        return cls(
            organization_id=validate_organization_id(organization_id),
            vol_id=_object_key_component(vol_id, field_name="vol_id"),
            root=canonical,
        )

    @classmethod
    def from_binding(
        cls,
        organization_id: str,
        vol_id: str,
        workspace_prefix: str | None,
    ) -> MissionObjectNamespace:
        """Validate a persisted mission binding instead of recomputing it silently."""

        if workspace_prefix is None:
            raise ValueError("Mission has no durable workspace prefix")
        durable = workspace_prefix.strip().rstrip("/")
        namespace = cls.create(organization_id, vol_id)
        if durable != namespace.root:
            raise ValueError(
                "Mission workspace prefix is outside its tenant namespace"
            )
        return namespace

    def key(self, *components: str) -> str:
        if not components:
            return self.root
        safe_components = (
            _object_key_component(component, field_name="object key component")
            for component in components
        )
        return "/".join((self.root, *safe_components))

    def prefix(self, *components: str) -> str:
        return f"{self.key(*components)}/"


def mission_event_namespace(
    payload: Mapping[str, object],
) -> MissionObjectNamespace:
    """Resolve a mission namespace carried by an event, with legacy-v1 fallback."""

    vol_id = str(payload.get("vol_id") or "")
    organization_id = str(
        payload.get("organization_id") or LEGACY_ORGANIZATION_ID
    )
    workspace_prefix = payload.get("workspace_prefix")
    if workspace_prefix is None:
        if organization_id != LEGACY_ORGANIZATION_ID:
            raise ValueError(
                "Tenant-bound mission event has no durable workspace prefix"
            )
        return MissionObjectNamespace.create(organization_id, vol_id)
    return MissionObjectNamespace.from_binding(
        organization_id,
        vol_id,
        str(workspace_prefix),
    )
