"""Bounded-memory binary PLY inspection and resident-core assembly."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np

from .partition import CellBounds


_VERTEX_PREFIX = b"element vertex "
_END_HEADER = b"end_header\n"
_COUNT_WIDTH = 20
_FLOAT_TYPES = {"float": "<f4", "float32": "<f4"}


@dataclass(frozen=True)
class BinaryPlyLayout:
    vertex_count: int
    header_size: int
    dtype: np.dtype
    property_names: tuple[str, ...]
    header_template: bytes
    count_offset: int


@dataclass(frozen=True)
class PartitionCorePly:
    bounds: CellBounds
    model_path: Path


@dataclass(frozen=True)
class PlyMergeResult:
    path: Path
    vertex_count: int
    size_bytes: int


def read_binary_ply_layout(path: str | Path) -> BinaryPlyLayout:
    """Validate the vertex-only float PLY contract used by GaussianModel."""

    source = Path(path)
    with source.open("rb") as handle:
        header = handle.read(64 * 1024)
    marker = header.find(_END_HEADER)
    if marker < 0:
        raise ValueError(f"PLY header is incomplete: {source}")
    header = header[: marker + len(_END_HEADER)]
    lines = header.decode("ascii").splitlines()
    if lines[:2] != ["ply", "format binary_little_endian 1.0"]:
        raise ValueError(f"PLY must be binary little-endian: {source}")
    element_lines = [line for line in lines if line.startswith("element ")]
    if len(element_lines) != 1 or not element_lines[0].startswith("element vertex "):
        raise ValueError(f"PLY must contain exactly one vertex element: {source}")
    try:
        vertex_count = int(element_lines[0].split()[2])
    except (IndexError, ValueError) as error:
        raise ValueError(f"PLY vertex count is invalid: {source}") from error
    properties: list[tuple[str, str]] = []
    for line in lines:
        if not line.startswith("property "):
            continue
        tokens = line.split()
        if len(tokens) != 3 or tokens[1] not in _FLOAT_TYPES:
            raise ValueError(f"PLY has an unsupported property: {line}")
        properties.append((tokens[2], _FLOAT_TYPES[tokens[1]]))
    property_names = tuple(name for name, _dtype in properties)
    if len(properties) < 3 or property_names[:3] != ("x", "y", "z"):
        raise ValueError(f"PLY vertex schema must begin with x/y/z: {source}")
    dtype = np.dtype(properties, align=False)
    expected_size = len(header) + vertex_count * dtype.itemsize
    actual_size = source.stat().st_size
    if actual_size != expected_size:
        raise ValueError(
            f"PLY byte size is inconsistent: {source} "
            f"({actual_size:,} versus {expected_size:,})"
        )
    count_start = header.index(_VERTEX_PREFIX) + len(_VERTEX_PREFIX)
    count_stop = header.index(b"\n", count_start)
    template = header[:count_start] + (b"0" * _COUNT_WIDTH) + header[count_stop:]
    return BinaryPlyLayout(
        vertex_count=vertex_count,
        header_size=len(header),
        dtype=dtype,
        property_names=property_names,
        header_template=template,
        count_offset=count_start,
    )


def _core_mask(records: np.ndarray, bounds: CellBounds) -> np.ndarray:
    xyz = np.column_stack((records["x"], records["y"], records["z"]))
    ground = (
        xyz @ np.asarray(bounds.model_to_ground_linear, dtype=np.float64).T
        + np.asarray(bounds.model_to_ground_offset, dtype=np.float64)
    )
    x_upper = ground[:, 0] <= bounds.core_x_max if bounds.include_core_x_max else ground[:, 0] < bounds.core_x_max
    y_upper = ground[:, 1] <= bounds.core_y_max if bounds.include_core_y_max else ground[:, 1] < bounds.core_y_max
    return (
        (ground[:, 0] >= bounds.core_x_min)
        & x_upper
        & (ground[:, 1] >= bounds.core_y_min)
        & y_upper
    )


def _copy_core_records(
    source: BinaryIO,
    target: BinaryIO | None,
    *,
    layout: BinaryPlyLayout,
    bounds: CellBounds,
    chunk_records: int,
) -> int:
    retained = 0
    remaining = layout.vertex_count
    while remaining:
        count = min(remaining, chunk_records)
        records = np.fromfile(source, dtype=layout.dtype, count=count)
        if records.shape[0] != count:
            raise ValueError("PLY payload ended before its declared vertex count")
        selected = records[_core_mask(records, bounds)]
        retained += int(selected.shape[0])
        if target is not None and selected.size:
            selected.tofile(target)
        remaining -= count
    return retained


def count_partition_core_vertices(
    partitions: Iterable[PartitionCorePly],
    *,
    chunk_records: int = 131_072,
) -> int:
    """Count uniquely owned core vertices without loading whole PLY files."""

    total = 0
    for partition in partitions:
        layout = read_binary_ply_layout(partition.model_path)
        with partition.model_path.open("rb") as source:
            source.seek(layout.header_size)
            total += _copy_core_records(
                source,
                None,
                layout=layout,
                bounds=partition.bounds,
                chunk_records=chunk_records,
            )
    return total


def merge_partition_cores_to_ply(
    partitions: Iterable[PartitionCorePly],
    output_path: str | Path,
    *,
    expected_vertex_count: int | None = None,
    chunk_records: int = 131_072,
) -> PlyMergeResult:
    """Atomically concatenate disjoint resident cores into one Gaussian PLY."""

    sources = tuple(partitions)
    if not sources:
        raise ValueError("At least one resident PLY is required")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output = output.resolve()
    if any(partition.model_path.resolve() == resolved_output for partition in sources):
        raise ValueError("Unified PLY output cannot replace a resident input")
    layouts = tuple(read_binary_ply_layout(partition.model_path) for partition in sources)
    first = layouts[0]
    if any(
        layout.dtype != first.dtype or layout.property_names != first.property_names
        for layout in layouts[1:]
    ):
        raise ValueError("Resident PLY schemas are inconsistent")
    maximum_bytes = sum(
        layout.vertex_count * layout.dtype.itemsize
        for layout in layouts
    ) + len(first.header_template)
    free_bytes = shutil.disk_usage(output.parent).free
    reserve_bytes = 5 * 1024**3
    if free_bytes < maximum_bytes + reserve_bytes:
        raise RuntimeError(
            "Insufficient disk space for atomic unified PLY assembly: "
            f"need up to {(maximum_bytes + reserve_bytes) / 1024**3:.1f} GiB, "
            f"have {free_bytes / 1024**3:.1f} GiB"
        )
    temporary = output.with_name(output.name + ".tmp")
    total = 0
    try:
        with temporary.open("wb+") as target:
            target.write(first.header_template)
            for partition, layout in zip(sources, layouts, strict=True):
                with partition.model_path.open("rb") as source:
                    source.seek(layout.header_size)
                    total += _copy_core_records(
                        source,
                        target,
                        layout=layout,
                        bounds=partition.bounds,
                        chunk_records=chunk_records,
                    )
            if expected_vertex_count is not None and total != expected_vertex_count:
                raise RuntimeError(
                    "Unified PLY core count drifted: "
                    f"{total:,} versus expected {expected_vertex_count:,}"
                )
            encoded_count = f"{total:0{_COUNT_WIDTH}d}".encode("ascii")
            if len(encoded_count) != _COUNT_WIDTH:
                raise OverflowError("Unified PLY vertex count exceeds header capacity")
            target.seek(first.count_offset)
            target.write(encoded_count)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return PlyMergeResult(
        path=output,
        vertex_count=total,
        size_bytes=output.stat().st_size,
    )
