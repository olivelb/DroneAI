"""Stable SAM3 inference capabilities shared by API, scheduler and worker."""

from __future__ import annotations

from typing import Final, TypedDict


SAM3_PROCESSOR_TARGET_SIZE: Final = 1008
SAM3_MAXIMUM_SOURCE_TILE_SIZE: Final = 1024
SAM3_INFERENCE_BATCH_SIZE: Final = 1
SAM3_MINIMUM_VRAM_GIB: Final = 12
SAM3_DEFAULT_MODEL_ID: Final = "facebook/sam3"
SAM3_DEFAULT_MODEL_REVISION: Final = (
    "3c879f39826c281e95690f02c7821c4de09afae7"
)


class Sam3Capability(TypedDict):
    model_id: str
    model_revision: str
    processor_target_size: int
    maximum_source_tile_size: int
    inference_batch_size: int
    minimum_vram_gib: int


def sam3_capability() -> Sam3Capability:
    """Return the deployable batch-one SAM3 resource contract."""

    return {
        "model_id": SAM3_DEFAULT_MODEL_ID,
        "model_revision": SAM3_DEFAULT_MODEL_REVISION,
        "processor_target_size": SAM3_PROCESSOR_TARGET_SIZE,
        "maximum_source_tile_size": SAM3_MAXIMUM_SOURCE_TILE_SIZE,
        "inference_batch_size": SAM3_INFERENCE_BATCH_SIZE,
        "minimum_vram_gib": SAM3_MINIMUM_VRAM_GIB,
    }


def validate_sam3_tile_size(tile_size: int) -> int:
    """Reject source tiles larger than the effective SAM3 processor input."""

    if tile_size > SAM3_MAXIMUM_SOURCE_TILE_SIZE:
        raise ValueError(
            "SAM3 tile size must not exceed "
            f"{SAM3_MAXIMUM_SOURCE_TILE_SIZE} pixels because the pinned "
            f"processor resizes inputs to {SAM3_PROCESSOR_TARGET_SIZE} pixels"
        )
    return tile_size
