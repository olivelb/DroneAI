"""Stable Kafka keys for horizontally scalable worker topics."""

from __future__ import annotations

from shared.tenancy import validate_organization_id


def tenant_mission_key(organization_id: str, vol_id: str) -> str:
    if not vol_id:
        raise ValueError("vol_id is required for a tenant mission key")
    return f"{validate_organization_id(organization_id)}:{vol_id}"


def tile_work_key(
    vol_id: str,
    analysis_run_id: str | None,
    tile_index: int,
    *,
    organization_id: str | None = None,
) -> str:
    """Keep every delivery of one logical tile on the same partition.

    Different tiles intentionally receive different keys so Kafka can spread
    them across worker replicas. The attempt is excluded to preserve ordering
    between retries of the same logical tile.
    """

    if not vol_id:
        raise ValueError("vol_id is required for a tile work key")
    normalized_index = int(tile_index)
    if normalized_index < 0:
        raise ValueError("tile_index must be non-negative")
    run_scope = analysis_run_id or "pipeline"
    mission_scope = (
        tenant_mission_key(organization_id, vol_id)
        if organization_id is not None
        else vol_id
    )
    return f"{mission_scope}:{run_scope}:tile:{normalized_index}"
