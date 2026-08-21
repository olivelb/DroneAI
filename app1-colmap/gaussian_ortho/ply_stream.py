"""Bounded-memory binary PLY inspection and seam-safe resident assembly."""

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
CORE_MERGE_COMMENT = "droneai_merge partition-core-v1"
FEATHERED_MERGE_COMMENT = "droneai_merge partition-opacity-feather-v1"


@dataclass(frozen=True)
class BinaryPlyLayout:
    vertex_count: int
    header_size: int
    dtype: np.dtype
    property_names: tuple[str, ...]
    header_template: bytes
    count_offset: int
    comments: tuple[str, ...]


@dataclass(frozen=True)
class PartitionCorePly:
    bounds: CellBounds
    model_path: Path


@dataclass(frozen=True)
class PlyMergeResult:
    path: Path
    vertex_count: int
    size_bytes: int
    source_vertex_count: int
    algorithm: str


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
        comments=tuple(
            line.removeprefix("comment ")
            for line in lines
            if line.startswith("comment ")
        ),
    )


def _header_with_merge_comment(layout: BinaryPlyLayout, comment: str) -> bytes:
    encoded = f"comment {comment}\n".encode("ascii")
    format_end = layout.header_template.index(b"\n", len(b"ply\n")) + 1
    return (
        layout.header_template[:format_end]
        + encoded
        + layout.header_template[format_end:]
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
    output_header = _header_with_merge_comment(first, CORE_MERGE_COMMENT)
    output_count_offset = output_header.index(_VERTEX_PREFIX) + len(_VERTEX_PREFIX)
    maximum_bytes = sum(
        layout.vertex_count * layout.dtype.itemsize
        for layout in layouts
    ) + len(output_header)
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
            target.write(output_header)
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
            target.seek(output_count_offset)
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
        source_vertex_count=sum(layout.vertex_count for layout in layouts),
        algorithm=CORE_MERGE_COMMENT,
    )


def _axis_buffer_weight(
    coordinates: np.ndarray,
    *,
    core_min: float,
    core_max: float,
    buffer_min: float,
    buffer_max: float,
) -> np.ndarray:
    """Return the linear core/buffer influence used by resident rendering."""

    weights = np.ones(coordinates.shape, dtype=np.float64)
    lower = coordinates < core_min
    lower_width = core_min - buffer_min
    weights[lower] = (
        (coordinates[lower] - buffer_min) / lower_width
        if lower_width > 0.0
        else 0.0
    )
    upper = coordinates > core_max
    upper_width = buffer_max - core_max
    weights[upper] = (
        (buffer_max - coordinates[upper]) / upper_width
        if upper_width > 0.0
        else 0.0
    )
    return np.clip(weights, 0.0, 1.0)


def _partition_buffer_weight(
    ground: np.ndarray,
    bounds: CellBounds,
) -> np.ndarray:
    return _axis_buffer_weight(
        ground[:, 0],
        core_min=bounds.core_x_min,
        core_max=bounds.core_x_max,
        buffer_min=bounds.buffer_x_min,
        buffer_max=bounds.buffer_x_max,
    ) * _axis_buffer_weight(
        ground[:, 1],
        core_min=bounds.core_y_min,
        core_max=bounds.core_y_max,
        buffer_min=bounds.buffer_y_min,
        buffer_max=bounds.buffer_y_max,
    )


def _inside_partition_core(ground: np.ndarray, bounds: CellBounds) -> np.ndarray:
    x_upper = (
        ground[:, 0] <= bounds.core_x_max
        if bounds.include_core_x_max
        else ground[:, 0] < bounds.core_x_max
    )
    y_upper = (
        ground[:, 1] <= bounds.core_y_max
        if bounds.include_core_y_max
        else ground[:, 1] < bounds.core_y_max
    )
    return (
        (ground[:, 0] >= bounds.core_x_min)
        & x_upper
        & (ground[:, 1] >= bounds.core_y_min)
        & y_upper
    )


def _validate_shared_ground_frame(bounds: tuple[CellBounds, ...]) -> None:
    reference_linear = np.asarray(bounds[0].model_to_ground_linear)
    reference_offset = np.asarray(bounds[0].model_to_ground_offset)
    for candidate in bounds[1:]:
        if not (
            np.allclose(reference_linear, candidate.model_to_ground_linear)
            and np.allclose(reference_offset, candidate.model_to_ground_offset)
        ):
            raise ValueError(
                "Resident PLY feathering requires one shared projected-ground frame"
            )


def partition_opacity_weights(
    records: np.ndarray,
    owner: CellBounds,
    all_bounds: tuple[CellBounds, ...],
) -> tuple[np.ndarray, np.ndarray]:
    """Return a partition-of-unity opacity weight and product-domain mask.

    Every independently trained buffer contributes through the same linear
    influence used by raster feathering.  Normalizing those influences avoids
    both the holes caused by centre-only ownership and opacity doubling in the
    overlap.  The domain mask excludes buffer-only geometry outside the union
    of the requested cell cores.
    """

    xyz = np.column_stack((records["x"], records["y"], records["z"]))
    ground = (
        xyz @ np.asarray(owner.model_to_ground_linear, dtype=np.float64).T
        + np.asarray(owner.model_to_ground_offset, dtype=np.float64)
    )
    domain = np.zeros(records.shape[0], dtype=bool)
    denominator = np.zeros(records.shape[0], dtype=np.float64)
    for bounds in all_bounds:
        domain |= _inside_partition_core(ground, bounds)
        denominator += _partition_buffer_weight(ground, bounds)
    owner_weight = _partition_buffer_weight(ground, owner)
    weights = np.divide(
        owner_weight,
        denominator,
        out=np.zeros_like(owner_weight),
        where=denominator > 0.0,
    )
    return weights, domain


def _apply_optical_depth_weight(
    records: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Apply cell influence in alpha optical-depth space.

    If coincident buffers contain the same Gaussian and their weights sum to
    one, compositing the weighted copies reconstructs its original base alpha:
    ``1 - product((1 - alpha) ** weight) == alpha``.
    """

    if "opacity" not in (records.dtype.names or ()):
        raise ValueError("Seam-safe PLY merging requires an opacity property")
    result = records.copy()
    logits = np.asarray(result["opacity"], dtype=np.float64)
    alpha = np.empty_like(logits)
    positive = logits >= 0.0
    alpha[positive] = 1.0 / (1.0 + np.exp(-logits[positive]))
    exp_logits = np.exp(logits[~positive])
    alpha[~positive] = exp_logits / (1.0 + exp_logits)
    alpha = np.clip(alpha, 1.0e-7, 1.0 - 1.0e-7)
    weighted_alpha = -np.expm1(weights * np.log1p(-alpha))
    weighted_alpha = np.clip(weighted_alpha, 1.0e-7, 1.0 - 1.0e-7)
    result["opacity"] = np.log(weighted_alpha / (1.0 - weighted_alpha))
    for name in result.dtype.names or ():
        if name.startswith("opacity_sh_"):
            result[name] *= weights.astype(result.dtype[name])
    return result


def _copy_feathered_records(
    source: BinaryIO,
    target: BinaryIO,
    *,
    layout: BinaryPlyLayout,
    owner: CellBounds,
    all_bounds: tuple[CellBounds, ...],
    chunk_records: int,
) -> int:
    retained = 0
    remaining = layout.vertex_count
    while remaining:
        count = min(remaining, chunk_records)
        records = np.fromfile(source, dtype=layout.dtype, count=count)
        if records.shape[0] != count:
            raise ValueError("PLY payload ended before its declared vertex count")
        weights, domain = partition_opacity_weights(records, owner, all_bounds)
        selected = domain & (weights > 0.0)
        if selected.any():
            weighted = _apply_optical_depth_weight(records[selected], weights[selected])
            weighted.tofile(target)
            retained += int(weighted.shape[0])
        remaining -= count
    return retained


def merge_partition_buffers_to_ply(
    partitions: Iterable[PartitionCorePly],
    output_path: str | Path,
    *,
    chunk_records: int = 131_072,
) -> PlyMergeResult:
    """Build a seam-safe PLY from independently trained resident buffers.

    Unlike centre-only core concatenation, this keeps all buffer Gaussians that
    contribute inside the requested product domain.  Overlapping cell models
    are cross-faded with normalized opacity in optical-depth space.  Processing
    remains bounded-memory and publication is atomic.
    """

    sources = tuple(partitions)
    if not sources:
        raise ValueError("At least one resident PLY is required")
    all_bounds = tuple(partition.bounds for partition in sources)
    _validate_shared_ground_frame(all_bounds)
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
    if "opacity" not in first.property_names:
        raise ValueError("Seam-safe PLY merging requires Gaussian opacity")
    output_header = _header_with_merge_comment(first, FEATHERED_MERGE_COMMENT)
    output_count_offset = output_header.index(_VERTEX_PREFIX) + len(_VERTEX_PREFIX)
    source_vertex_count = sum(layout.vertex_count for layout in layouts)
    maximum_bytes = source_vertex_count * first.dtype.itemsize + len(output_header)
    free_bytes = shutil.disk_usage(output.parent).free
    reserve_bytes = 5 * 1024**3
    if free_bytes < maximum_bytes + reserve_bytes:
        raise RuntimeError(
            "Insufficient disk space for atomic seam-safe PLY assembly: "
            f"need up to {(maximum_bytes + reserve_bytes) / 1024**3:.1f} GiB, "
            f"have {free_bytes / 1024**3:.1f} GiB"
        )
    temporary = output.with_name(output.name + ".tmp")
    total = 0
    try:
        with temporary.open("wb+") as target:
            target.write(output_header)
            for partition, layout in zip(sources, layouts, strict=True):
                with partition.model_path.open("rb") as source:
                    source.seek(layout.header_size)
                    total += _copy_feathered_records(
                        source,
                        target,
                        layout=layout,
                        owner=partition.bounds,
                        all_bounds=all_bounds,
                        chunk_records=chunk_records,
                    )
            encoded_count = f"{total:0{_COUNT_WIDTH}d}".encode("ascii")
            if len(encoded_count) != _COUNT_WIDTH:
                raise OverflowError("Unified PLY vertex count exceeds header capacity")
            target.seek(output_count_offset)
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
        source_vertex_count=source_vertex_count,
        algorithm=FEATHERED_MERGE_COMMENT,
    )
