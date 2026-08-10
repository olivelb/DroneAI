"""Dependency-free raster tile planning shared by API and AI runtimes."""

from __future__ import annotations


def build_tile_starts(full_size: int, tile_size: int, overlap: int) -> list[int]:
    if full_size <= 0:
        raise ValueError("full_size must be positive")
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be between 0 and tile_size - 1")
    if full_size <= tile_size:
        return [0]
    stride = tile_size - overlap
    starts = list(range(0, full_size - tile_size + 1, stride))
    last_start = full_size - tile_size
    if starts[-1] != last_start:
        distance_to_edge = last_start - starts[-1]
        if len(starts) > 1 and overlap > 0 and distance_to_edge < overlap:
            starts[-1] = last_start
        else:
            starts.append(last_start)
    return starts
