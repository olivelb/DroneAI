"""Parsing helpers for bounded raster display recipes."""

from __future__ import annotations

import math


def parse_band_indexes(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        indexes = [int(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise ValueError("bands must be comma-separated integer indexes") from error
    if len(indexes) not in {1, 3} or len(set(indexes)) != len(indexes):
        raise ValueError("bands must contain one grayscale or three unique RGB indexes")
    if any(index < 1 or index > 256 for index in indexes):
        raise ValueError("band indexes must be between 1 and 256")
    return indexes


def parse_display_ranges(
    value: str | None,
    *,
    expected_count: int | None,
) -> list[list[float] | None] | None:
    if not value:
        return None
    ranges: list[list[float] | None] = []
    for item in value.split(","):
        if item.strip().lower() in {"", "auto"}:
            ranges.append(None)
            continue
        parts = item.split(":", maxsplit=1)
        if len(parts) != 2:
            raise ValueError("display_ranges must use minimum:maximum pairs")
        try:
            low, high = (float(part) for part in parts)
        except ValueError as error:
            raise ValueError("display ranges must contain finite numbers") from error
        if not math.isfinite(low) or not math.isfinite(high) or high <= low:
            raise ValueError("every display maximum must exceed its finite minimum")
        ranges.append([low, high])
    if expected_count is not None and len(ranges) != expected_count:
        raise ValueError("display ranges must match the selected band count")
    return ranges
