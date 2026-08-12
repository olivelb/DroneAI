"""Organization boundary and versioned object-key helpers."""

from __future__ import annotations

import re


LEGACY_ORGANIZATION_ID = "legacy-unassigned"
LOCAL_ORGANIZATION_ID = "local-development"
ORGANIZATION_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


def validate_organization_id(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(ORGANIZATION_ID_PATTERN, normalized):
        raise ValueError(
            "organization_id must be a lower-case DNS-like identifier of 1-64 characters"
        )
    return normalized


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
