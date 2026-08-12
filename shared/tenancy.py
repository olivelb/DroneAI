"""Organization boundary and versioned object-key helpers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import re
from collections.abc import Iterator


LEGACY_ORGANIZATION_ID = "legacy-unassigned"
LOCAL_ORGANIZATION_ID = "local-development"

_CURRENT_ORGANIZATION_ID: ContextVar[str | None] = ContextVar(
    "droneai_current_organization_id",
    default=None,
)
ORGANIZATION_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


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
    if organization == LEGACY_ORGANIZATION_ID:
        return f"datasets/{dataset_name}"
    return f"organizations/{organization}/datasets/{dataset_name}"


def mission_prefix(organization_id: str, vol_id: str) -> str:
    organization = validate_organization_id(organization_id)
    if organization == LEGACY_ORGANIZATION_ID:
        return f"missions/{vol_id}"
    return f"organizations/{organization}/missions/{vol_id}"
