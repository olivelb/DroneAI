"""Deterministic, bounded shard plans for full-raster detection."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from shared.detection_geometry import build_tile_starts

MAX_DETECTION_TILES = 100_000
MAX_DETECTION_SHARDS = 256


@dataclass(frozen=True)
class DetectionTile:
    tile_index: int
    offset_x: int
    offset_y: int
    width: int
    height: int


@dataclass(frozen=True)
class DetectionShard:
    shard_index: int
    first_tile_index: int
    tile_count: int

    @property
    def end_tile_index(self) -> int:
        return self.first_tile_index + self.tile_count


@dataclass(frozen=True)
class DetectionShardPlan:
    width: int
    height: int
    tile_size: int
    overlap: int
    tiles_per_shard: int
    x_starts: tuple[int, ...]
    y_starts: tuple[int, ...]
    shards: tuple[DetectionShard, ...]
    planned_inference_pixels: int
    checksum_sha256: str

    @property
    def tile_count(self) -> int:
        return len(self.x_starts) * len(self.y_starts)

    @property
    def shard_count(self) -> int:
        return len(self.shards)

    @property
    def pixel_amplification_ratio(self) -> float:
        return round(
            self.planned_inference_pixels / max(1, self.width * self.height),
            6,
        )

    def shard(self, shard_index: int) -> DetectionShard:
        if not 0 <= shard_index < self.shard_count:
            raise IndexError(f"Detection shard index is out of range: {shard_index}")
        return self.shards[shard_index]

    def tiles(self, shard_index: int) -> Iterator[DetectionTile]:
        shard = self.shard(shard_index)
        columns = len(self.x_starts)
        for tile_index in range(shard.first_tile_index, shard.end_tile_index):
            row_index, column_index = divmod(tile_index, columns)
            offset_x = self.x_starts[column_index]
            offset_y = self.y_starts[row_index]
            yield DetectionTile(
                tile_index=tile_index,
                offset_x=offset_x,
                offset_y=offset_y,
                width=min(self.tile_size, self.width - offset_x),
                height=min(self.tile_size, self.height - offset_y),
            )

    def descriptor(self) -> dict[str, Any]:
        """Return the stable plan descriptor persisted by coordinators."""

        return {
            "schema_version": 1,
            "width": self.width,
            "height": self.height,
            "tile_size": self.tile_size,
            "overlap": self.overlap,
            "tiles_per_shard": self.tiles_per_shard,
            "tile_count": self.tile_count,
            "shard_count": self.shard_count,
            "planned_inference_pixels": self.planned_inference_pixels,
            "pixel_amplification_ratio": self.pixel_amplification_ratio,
            "checksum_sha256": self.checksum_sha256,
        }


def _plan_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def build_detection_shard_plan(
    width: int,
    height: int,
    tile_size: int,
    overlap: int,
    *,
    tiles_per_shard: int,
    maximum_tiles: int = MAX_DETECTION_TILES,
    maximum_shards: int = MAX_DETECTION_SHARDS,
) -> DetectionShardPlan:
    """Build a compact row-major partition of all raster tile windows."""

    if width <= 0 or height <= 0:
        raise ValueError("Detection raster dimensions must be positive")
    if tiles_per_shard <= 0:
        raise ValueError("Detection tiles per shard must be positive")
    if maximum_tiles <= 0 or maximum_tiles > MAX_DETECTION_TILES:
        raise ValueError(
            f"Detection maximum tiles must be between 1 and {MAX_DETECTION_TILES}"
        )
    if maximum_shards <= 0 or maximum_shards > MAX_DETECTION_SHARDS:
        raise ValueError(
            f"Detection maximum shards must be between 1 and {MAX_DETECTION_SHARDS}"
        )
    x_starts = tuple(build_tile_starts(width, tile_size, overlap))
    y_starts = tuple(build_tile_starts(height, tile_size, overlap))
    tile_count = len(x_starts) * len(y_starts)
    if tile_count > maximum_tiles:
        raise ValueError(
            f"Detection plan exceeds its {maximum_tiles} tile safety limit"
        )
    shard_count = math.ceil(tile_count / tiles_per_shard)
    if shard_count > maximum_shards:
        raise ValueError(
            f"Detection plan requires {shard_count} shards, above its "
            f"{maximum_shards} shard safety limit"
        )
    shards = tuple(
        DetectionShard(
            shard_index=shard_index,
            first_tile_index=shard_index * tiles_per_shard,
            tile_count=min(
                tiles_per_shard,
                tile_count - shard_index * tiles_per_shard,
            ),
        )
        for shard_index in range(shard_count)
    )
    planned_inference_pixels = sum(
        min(tile_size, width - offset_x) for offset_x in x_starts
    ) * sum(min(tile_size, height - offset_y) for offset_y in y_starts)
    checksum_payload: dict[str, Any] = {
        "schema_version": 1,
        "width": width,
        "height": height,
        "tile_size": tile_size,
        "overlap": overlap,
        "tiles_per_shard": tiles_per_shard,
        "x_starts": x_starts,
        "y_starts": y_starts,
        "shards": [
            {
                "shard_index": shard.shard_index,
                "first_tile_index": shard.first_tile_index,
                "tile_count": shard.tile_count,
            }
            for shard in shards
        ],
    }
    return DetectionShardPlan(
        width=width,
        height=height,
        tile_size=tile_size,
        overlap=overlap,
        tiles_per_shard=tiles_per_shard,
        x_starts=x_starts,
        y_starts=y_starts,
        shards=shards,
        planned_inference_pixels=planned_inference_pixels,
        checksum_sha256=_plan_checksum(checksum_payload),
    )


def parse_detection_shard_plan_descriptor(payload: object) -> DetectionShardPlan:
    """Rebuild and verify an untrusted durable plan descriptor."""

    expected_keys = {
        "schema_version",
        "width",
        "height",
        "tile_size",
        "overlap",
        "tiles_per_shard",
        "tile_count",
        "shard_count",
        "planned_inference_pixels",
        "pixel_amplification_ratio",
        "checksum_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise ValueError("Detection shard plan descriptor has invalid fields")
    raw = cast(dict[str, Any], payload)
    if raw["schema_version"] != 1:
        raise ValueError("Unsupported detection shard plan schema version")

    def integer(field: str) -> int:
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Detection shard plan {field} must be an integer")
        return value

    plan = build_detection_shard_plan(
        integer("width"),
        integer("height"),
        integer("tile_size"),
        integer("overlap"),
        tiles_per_shard=integer("tiles_per_shard"),
    )
    if plan.descriptor() != raw:
        raise ValueError("Detection shard plan descriptor is inconsistent")
    return plan
