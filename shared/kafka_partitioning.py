"""Stable Kafka keys for horizontally scalable worker topics."""

from __future__ import annotations


def tile_work_key(
    vol_id: str,
    analysis_run_id: str | None,
    tile_index: int,
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
    return f"{vol_id}:{run_scope}:tile:{normalized_index}"
