"""Out-of-core spatial partitioner for immutable GSTile v1 bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import numpy as np

from gaussian_ortho.ply_stream import BinaryPlyLayout, read_binary_ply_layout
from shared.gstile_manifest import (
    GSTILE_ADAPTIVE_LOD_PROFILE,
    GSTILE_LOD_PROFILE,
    GSTILE_MOMENT_LOD_PROFILE,
    GSTILE_PROFILE,
    GSTILE_SCHEMA,
    GSTILE_STRATIFIED_LOD_PROFILE,
    GSTILE_VERSION,
)

from .format import (
    canonical_manifest_bytes,
    validate_manifest,
    write_pack_atomic,
)


@dataclass(frozen=True)
class GsTileBuildOptions:
    leaf_size: int = 65_536
    chunk_records: int = 131_072
    maximum_depth: int = 48
    temporary_root: Path | None = None
    coordinate_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    crs: str | None = None
    cancellation_check: Callable[[], None] | None = None
    progress_callback: Callable[[dict[str, Any]], None] | None = None
    lod_proxy_size: int | None = None
    lod_proxy_strategy: Literal[
        "adaptive-moment", "moment-matched", "spatial-stratified", "minhash"
    ] = "moment-matched"

    def validate(self) -> None:
        if not 1_024 <= self.leaf_size <= 1_048_576:
            raise ValueError("GSTile leaf_size must be between 1,024 and 1,048,576")
        if not 1_024 <= self.chunk_records <= 1_048_576:
            raise ValueError("GSTile chunk_records must be between 1,024 and 1,048,576")
        if not 1 <= self.maximum_depth <= 64:
            raise ValueError("GSTile maximum_depth must be between 1 and 64")
        if self.lod_proxy_size is not None and not (1_024 <= self.lod_proxy_size <= self.leaf_size):
            raise ValueError("GSTile lod_proxy_size must be between 1,024 and leaf_size")
        if self.lod_proxy_strategy not in {
            "adaptive-moment",
            "moment-matched",
            "spatial-stratified",
            "minhash",
        }:
            raise ValueError(
                "GSTile lod_proxy_strategy must be adaptive-moment, moment-matched, "
                "spatial-stratified or minhash"
            )
        if len(self.coordinate_origin) != 3 or not all(np.isfinite(value) for value in self.coordinate_origin):
            raise ValueError("GSTile coordinate origin must contain three finite values")


@dataclass(frozen=True)
class GsTileBuildResult:
    output: Path
    manifest_path: Path
    bundle_id: str
    gaussian_count: int
    leaf_count: int
    pack_bytes: int
    source_bytes: int
    maximum_errors: dict[str, float]


@dataclass(frozen=True)
class _WorkFile:
    path: Path
    count: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]


@dataclass(frozen=True)
class _LodProxy:
    records: np.ndarray
    errors: np.ndarray
    render_bounds_min: tuple[float, float, float] | None = None
    render_bounds_max: tuple[float, float, float] | None = None


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    event: str,
    **details: Any,
) -> None:
    if callback is not None:
        callback({"event": event, **details})


def _work_dtype(layout: BinaryPlyLayout) -> np.dtype:
    names = list(layout.dtype.names or ()) + ["source_id"]
    formats = [layout.dtype.fields[name][0] for name in layout.dtype.names or ()] + ["<u8"]
    offsets = [layout.dtype.fields[name][1] for name in layout.dtype.names or ()] + [layout.dtype.itemsize]
    return np.dtype(
        {
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": layout.dtype.itemsize + 8,
        }
    )


def _chunks(
    path: Path,
    dtype: np.dtype,
    count: int,
    cancellation_check: Callable[[], None] | None = None,
) -> Iterator[np.ndarray]:
    with path.open("rb") as handle:
        while True:
            if cancellation_check is not None:
                cancellation_check()
            records = np.fromfile(handle, dtype=dtype, count=count)
            if records.size == 0:
                return
            yield records


def _bounds(records: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xyz = np.column_stack((records["x"], records["y"], records["z"])).astype(np.float64, copy=False)
    if not np.all(np.isfinite(xyz)):
        raise ValueError("PLY contains non-finite Gaussian positions")
    return xyz.min(axis=0), xyz.max(axis=0)


def _minhash_keys(source_ids: np.ndarray) -> np.ndarray:
    """Return deterministic SplitMix64 keys without changing source records."""

    values = np.asarray(source_ids, dtype=np.uint64).copy()
    with np.errstate(over="ignore"):
        values += np.uint64(0x9E3779B97F4A7C15)
        values = (values ^ (values >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        values = (values ^ (values >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return values ^ (values >> np.uint64(31))


def _morton_codes(records: np.ndarray) -> np.ndarray:
    """Return deterministic 63-bit Morton codes in the population bounds."""

    xyz = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float64, copy=False
    )
    minimum = xyz.min(axis=0)
    extent = xyz.max(axis=0) - minimum
    normalized = np.divide(
        xyz - minimum,
        extent,
        out=np.zeros_like(xyz),
        where=extent > 0.0,
    )
    maximum_bin = np.uint64((1 << 21) - 1)
    quantized = np.floor(normalized * maximum_bin).astype(np.uint64)
    codes = np.zeros(records.shape[0], dtype=np.uint64)
    for bit in range(21):
        codes |= ((quantized[:, 0] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit)
        codes |= ((quantized[:, 1] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit + 1)
        codes |= ((quantized[:, 2] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit + 2)
    return codes


def _select_lod_proxy(
    records: np.ndarray,
    limit: int,
    strategy: Literal["spatial-stratified", "minhash"],
) -> np.ndarray:
    """Select a deterministic replacement proxy without changing source records."""

    if records.shape[0] <= limit:
        return records.copy()
    if strategy == "minhash":
        keys = _minhash_keys(records["source_id"])
        selected = np.argpartition(keys, limit - 1)[:limit]
        order = np.lexsort((records["source_id"][selected], keys[selected]))
        return records[selected[order]].copy()

    codes = _morton_codes(records)
    order = np.lexsort((records["source_id"], codes))
    stratum_centres = (
        (np.arange(limit, dtype=np.int64) * 2 + 1) * records.shape[0]
        // (2 * limit)
    ).astype(np.intp)
    return records[order[stratum_centres]].copy()

_ELLIPSOID_AREA_POWER = 1.6075


def _sigmoid(values: np.ndarray) -> np.ndarray:
    result = np.empty_like(values, dtype=np.float64)
    positive = values >= 0
    result[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exponential = np.exp(values[~positive])
    result[~positive] = exponential / (1.0 + exponential)
    return result


def _ellipsoid_area(scales: np.ndarray) -> np.ndarray:
    xy = np.power(scales[:, 0] * scales[:, 1], _ELLIPSOID_AREA_POWER)
    xz = np.power(scales[:, 0] * scales[:, 2], _ELLIPSOID_AREA_POWER)
    yz = np.power(scales[:, 1] * scales[:, 2], _ELLIPSOID_AREA_POWER)
    return 4.0 * np.pi * np.power((xy + xz + yz) / 3.0, 1.0 / _ELLIPSOID_AREA_POWER)


def _opacity_design_matrix(coefficient_count: int, sample_count: int = 32) -> np.ndarray:
    """Deterministic equal-area directions for nonlinear opacity-logit fitting."""

    if coefficient_count not in {0, 3, 8, 15}:
        raise ValueError("DroneGS opacity SH coefficient count must encode degree 0 through 3")
    sample_count = max(sample_count, 2 * (coefficient_count + 1))
    index = np.arange(sample_count, dtype=np.float64) + 0.5
    z = 1.0 - 2.0 * index / sample_count
    radius = np.sqrt(np.maximum(1.0 - z * z, 0.0))
    phi = index * (np.pi * (3.0 - np.sqrt(5.0)))
    x = radius * np.cos(phi)
    y = radius * np.sin(phi)
    xx, yy, zz = x * x, y * y, z * z
    basis = np.column_stack(
        (
            np.ones(sample_count),
            -0.4886025119029199 * y,
            0.4886025119029199 * z,
            -0.4886025119029199 * x,
            1.0925484305920792 * x * y,
            -1.0925484305920792 * y * z,
            0.31539156525252005 * (2.0 * zz - xx - yy),
            -1.0925484305920792 * x * z,
            0.5462742152960396 * (xx - yy),
            -0.5900435899266435 * y * (3.0 * xx - yy),
            2.890611442640554 * x * y * z,
            -0.4570457994644658 * y * (4.0 * zz - xx - yy),
            0.3731763325901154 * z * (2.0 * zz - 3.0 * xx - 3.0 * yy),
            -0.4570457994644658 * x * (4.0 * zz - xx - yy),
            1.445305721320277 * z * (xx - yy),
            -0.5900435899266435 * x * (xx - 3.0 * yy),
        )
    )
    return basis[:, : coefficient_count + 1]


def _opacity_property_names(records: np.ndarray) -> list[str]:
    return sorted(
        (name for name in records.dtype.names or () if name.startswith("opacity_sh_")),
        key=lambda name: int(name.rsplit("_", 1)[1]),
    )


def _refit_directional_opacity(
    ordered: np.ndarray,
    starts: np.ndarray,
    scales: np.ndarray,
    merged_scales: np.ndarray,
) -> np.ndarray:
    """Fit merged logits to directional optical mass after the sigmoid."""

    opacity_names = _opacity_property_names(ordered)
    design = _opacity_design_matrix(len(opacity_names))
    coefficients = np.column_stack(
        (ordered["opacity"], *(ordered[name] for name in opacity_names))
    ).astype(np.float64, copy=False)
    source_alpha = _sigmoid(coefficients @ design.T)
    source_area = _ellipsoid_area(scales)
    directional_mass = np.add.reduceat(
        source_alpha * source_area[:, None], starts, axis=0
    )
    target_alpha = np.clip(
        directional_mass / np.maximum(_ellipsoid_area(merged_scales)[:, None], 1e-30),
        1e-7,
        1.0 - 1e-7,
    )
    target_logits = np.log(target_alpha / (1.0 - target_alpha))
    fitted = target_logits @ np.linalg.pinv(design).T
    if not np.all(np.isfinite(fitted)):
        raise RuntimeError("GSTile directional opacity refit produced non-finite values")
    return fitted


def _rotation_matrices(quaternions: np.ndarray) -> np.ndarray:
    normalized = quaternions.astype(np.float64, copy=True)
    norms = np.linalg.norm(normalized, axis=1)
    invalid = norms <= 1e-12
    normalized[~invalid] /= norms[~invalid, None]
    normalized[invalid] = (1.0, 0.0, 0.0, 0.0)
    w, x, y, z = normalized.T
    matrices = np.empty((normalized.shape[0], 3, 3), dtype=np.float64)
    matrices[:, 0, 0] = 1.0 - 2.0 * (y * y + z * z)
    matrices[:, 0, 1] = 2.0 * (x * y - w * z)
    matrices[:, 0, 2] = 2.0 * (x * z + w * y)
    matrices[:, 1, 0] = 2.0 * (x * y + w * z)
    matrices[:, 1, 1] = 1.0 - 2.0 * (x * x + z * z)
    matrices[:, 1, 2] = 2.0 * (y * z - w * x)
    matrices[:, 2, 0] = 2.0 * (x * z - w * y)
    matrices[:, 2, 1] = 2.0 * (y * z + w * x)
    matrices[:, 2, 2] = 1.0 - 2.0 * (x * x + y * y)
    return matrices


def _rotation_matrices_to_quaternions(matrices: np.ndarray) -> np.ndarray:
    result = np.empty((matrices.shape[0], 4), dtype=np.float64)
    m00 = matrices[:, 0, 0]
    m11 = matrices[:, 1, 1]
    m22 = matrices[:, 2, 2]
    trace = m00 + m11 + m22
    first = trace > 0.0
    second = ~first & (m00 > m11) & (m00 > m22)
    third = ~first & ~second & (m11 > m22)
    fourth = ~(first | second | third)

    scale = np.sqrt(np.maximum(trace[first] + 1.0, 1e-18)) * 2.0
    result[first, 0] = 0.25 * scale
    result[first, 1] = (matrices[first, 2, 1] - matrices[first, 1, 2]) / scale
    result[first, 2] = (matrices[first, 0, 2] - matrices[first, 2, 0]) / scale
    result[first, 3] = (matrices[first, 1, 0] - matrices[first, 0, 1]) / scale

    scale = np.sqrt(np.maximum(1.0 + m00[second] - m11[second] - m22[second], 1e-18)) * 2.0
    result[second, 0] = (matrices[second, 2, 1] - matrices[second, 1, 2]) / scale
    result[second, 1] = 0.25 * scale
    result[second, 2] = (matrices[second, 0, 1] + matrices[second, 1, 0]) / scale
    result[second, 3] = (matrices[second, 0, 2] + matrices[second, 2, 0]) / scale

    scale = np.sqrt(np.maximum(1.0 + m11[third] - m00[third] - m22[third], 1e-18)) * 2.0
    result[third, 0] = (matrices[third, 0, 2] - matrices[third, 2, 0]) / scale
    result[third, 1] = (matrices[third, 0, 1] + matrices[third, 1, 0]) / scale
    result[third, 2] = 0.25 * scale
    result[third, 3] = (matrices[third, 1, 2] + matrices[third, 2, 1]) / scale

    scale = np.sqrt(np.maximum(1.0 + m22[fourth] - m00[fourth] - m11[fourth], 1e-18)) * 2.0
    result[fourth, 0] = (matrices[fourth, 1, 0] - matrices[fourth, 0, 1]) / scale
    result[fourth, 1] = (matrices[fourth, 0, 2] + matrices[fourth, 2, 0]) / scale
    result[fourth, 2] = (matrices[fourth, 1, 2] + matrices[fourth, 2, 1]) / scale
    result[fourth, 3] = 0.25 * scale

    result /= np.maximum(np.linalg.norm(result, axis=1, keepdims=True), 1e-12)
    result[result[:, 0] < 0.0] *= -1.0
    return result


def _moment_match_ordered_groups(
    ordered: np.ndarray,
    ordered_errors: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    refit_directional_opacity: bool = False,
) -> _LodProxy:
    """Moment-match contiguous groups using one deterministic vectorized pass."""

    group_sizes = ends - starts
    group_index = np.repeat(np.arange(starts.shape[0], dtype=np.intp), group_sizes)

    positions = np.column_stack((ordered["x"], ordered["y"], ordered["z"])).astype(
        np.float64, copy=False
    )
    log_scales = np.column_stack(
        (ordered["scale_0"], ordered["scale_1"], ordered["scale_2"])
    ).astype(np.float64, copy=False)
    scales = np.exp(np.clip(log_scales, -30.0, 30.0))
    opacity = _sigmoid(ordered["opacity"].astype(np.float64, copy=False))
    mass = np.maximum(opacity * _ellipsoid_area(scales), 1e-30)
    group_mass = np.add.reduceat(mass, starts)
    centers = np.add.reduceat(positions * mass[:, None], starts, axis=0) / group_mass[:, None]

    rotations = _rotation_matrices(
        np.column_stack(
            (ordered["rot_0"], ordered["rot_1"], ordered["rot_2"], ordered["rot_3"])
        )
    )
    covariances = np.matmul(
        rotations * np.square(scales)[:, None, :],
        np.swapaxes(rotations, 1, 2),
    )
    offsets = positions - centers[group_index]
    covariances += offsets[:, :, None] * offsets[:, None, :]
    merged_covariances = np.add.reduceat(
        covariances * mass[:, None, None], starts, axis=0
    ) / group_mass[:, None, None]
    merged_covariances[:, 0, 0] += 1e-8
    merged_covariances[:, 1, 1] += 1e-8
    merged_covariances[:, 2, 2] += 1e-8

    eigenvalues, eigenvectors = np.linalg.eigh(merged_covariances)
    descending = np.argsort(eigenvalues, axis=1)[:, ::-1]
    eigenvalues = np.take_along_axis(eigenvalues, descending, axis=1)
    eigenvectors = np.take_along_axis(eigenvectors, descending[:, None, :], axis=2)
    negative = np.linalg.det(eigenvectors) < 0.0
    eigenvectors[negative, :, 2] *= -1.0
    merged_scales = np.sqrt(np.maximum(eigenvalues, 1e-18))
    merged_quaternions = _rotation_matrices_to_quaternions(eigenvectors)
    merged_alpha = np.minimum(
        1.0,
        group_mass / np.maximum(_ellipsoid_area(merged_scales), 1e-30),
    )
    merged_alpha = np.clip(merged_alpha, 1e-7, 1.0 - 1e-7)

    output = ordered[starts].copy()
    float_names = [
        name
        for name in ordered.dtype.names or ()
        if np.issubdtype(ordered.dtype.fields[name][0], np.floating)
    ]
    float_values = np.column_stack([ordered[name] for name in float_names]).astype(
        np.float64, copy=False
    )
    averaged = np.add.reduceat(float_values * mass[:, None], starts, axis=0) / group_mass[:, None]
    for index, name in enumerate(float_names):
        output[name] = averaged[:, index]
    output["x"], output["y"], output["z"] = centers.T
    output["scale_0"], output["scale_1"], output["scale_2"] = np.log(merged_scales).T
    output["rot_0"], output["rot_1"], output["rot_2"], output["rot_3"] = merged_quaternions.T
    if refit_directional_opacity:
        fitted_opacity = _refit_directional_opacity(
            ordered, starts, scales, merged_scales
        )
        output["opacity"] = fitted_opacity[:, 0]
        opacity_names = _opacity_property_names(ordered)
        for index, name in enumerate(opacity_names):
            output[name] = fitted_opacity[:, index + 1]
    else:
        output["opacity"] = np.log(merged_alpha / (1.0 - merged_alpha))
    output["source_id"] = np.minimum.reduceat(ordered["source_id"], starts)

    coverage = np.linalg.norm(offsets, axis=1) + ordered_errors
    errors = np.maximum.reduceat(coverage, starts)
    if not (
        np.all(np.isfinite(averaged))
        and np.all(np.isfinite(centers))
        and np.all(np.isfinite(merged_scales))
        and np.all(np.isfinite(merged_quaternions))
        and np.all(np.isfinite(errors))
    ):
        raise RuntimeError("GSTile moment matching produced non-finite proxy values")
    return _LodProxy(output, errors)


def _moment_matched_lod_proxy(
    records: np.ndarray,
    input_errors: np.ndarray,
    limit: int,
) -> _LodProxy:
    """Merge uniform Morton strata while conserving mass and first two moments."""

    if records.shape[0] != input_errors.shape[0]:
        raise ValueError("GSTile proxy records and errors must have matching lengths")
    if records.shape[0] <= limit:
        return _LodProxy(records.copy(), input_errors.astype(np.float64, copy=True))

    order = np.lexsort((records["source_id"], _morton_codes(records)))
    ordered = records[order]
    ordered_errors = input_errors[order].astype(np.float64, copy=False)
    count = ordered.shape[0]
    starts = (np.arange(limit, dtype=np.int64) * count // limit).astype(np.intp)
    ends = (np.arange(1, limit + 1, dtype=np.int64) * count // limit).astype(np.intp)
    return _moment_match_ordered_groups(ordered, ordered_errors, starts, ends)


def _robust_cost_scale(values: np.ndarray) -> float:
    finite = values[np.isfinite(values) & (values > 0.0)]
    return max(float(np.median(finite)) if finite.size else 1.0, 1e-12)


def _adaptive_candidate_edges(records: np.ndarray, neighbors: int = 8) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build deterministic local candidates and a scene-normalized merge cost."""

    count = records.shape[0]
    morton_order = np.lexsort((records["source_id"], _morton_codes(records)))
    left = np.concatenate(
        [morton_order[:-offset] for offset in range(1, min(neighbors, count - 1) + 1)]
    )
    right = np.concatenate(
        [morton_order[offset:] for offset in range(1, min(neighbors, count - 1) + 1)]
    )
    positions = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float64, copy=False
    )
    log_scales = np.column_stack(
        (records["scale_0"], records["scale_1"], records["scale_2"])
    ).astype(np.float64, copy=False)
    scales = np.exp(np.clip(log_scales, -30.0, 30.0))
    spatial = np.sum(np.square(positions[left] - positions[right]), axis=1) / np.maximum(
        np.sum(np.square(scales[left]), axis=1) + np.sum(np.square(scales[right]), axis=1),
        1e-12,
    )
    shape = np.mean(np.square(log_scales[left] - log_scales[right]), axis=1)
    color_dc = np.column_stack(
        (records["f_dc_0"], records["f_dc_1"], records["f_dc_2"])
    ).astype(np.float64, copy=False)
    appearance = np.sum(np.square(color_dc[left] - color_dc[right]), axis=1)
    alpha = _sigmoid(records["opacity"].astype(np.float64, copy=False))
    opacity = np.square(alpha[left] - alpha[right])
    opacity_names = _opacity_property_names(records)
    if opacity_names:
        opacity_sh = np.column_stack([records[name] for name in opacity_names]).astype(
            np.float64, copy=False
        )
        directional = np.mean(np.square(opacity_sh[left] - opacity_sh[right]), axis=1)
    else:
        directional = np.zeros(left.shape[0], dtype=np.float64)
    cost = (
        np.log1p(spatial / _robust_cost_scale(spatial))
        + 0.35 * np.log1p(shape / _robust_cost_scale(shape))
        + 2.0 * np.log1p(appearance / _robust_cost_scale(appearance))
        + 0.5 * np.log1p(opacity / _robust_cost_scale(opacity))
        + 0.5 * np.log1p(directional / _robust_cost_scale(directional))
    )
    order = np.lexsort((right, left, cost))
    return left[order], right[order], cost[order]


def _adaptive_generation(
    records: np.ndarray,
    input_errors: np.ndarray,
    target: int,
) -> _LodProxy:
    """Cost-ordered local pair matching with a deterministic completion pass."""

    count = records.shape[0]
    if count <= target:
        return _LodProxy(records.copy(), input_errors.astype(np.float64, copy=True))
    left, right, _cost = _adaptive_candidate_edges(records)
    parent = np.arange(count, dtype=np.intp)
    size = np.ones(count, dtype=np.intp)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    removals_needed = count - target
    removed = 0
    for left_value, right_value in zip(left, right, strict=True):
        left_root = find(int(left_value))
        right_root = find(int(right_value))
        if left_root == right_root or size[left_root] + size[right_root] > 2:
            continue
        if size[left_root] < size[right_root] or (
            size[left_root] == size[right_root] and left_root > right_root
        ):
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        size[left_root] += size[right_root]
        removed += 1
        if removed == removals_needed:
            break
    if removed != removals_needed:
        # A greedy matching can leave unmatched vertices even on a connected
        # candidate graph. Complete it in Morton order so every generation has
        # an exact, deterministic population and never stalls a large build.
        morton_order = np.lexsort((records["source_id"], _morton_codes(records)))
        unmatched = [index for index in morton_order if size[find(int(index))] == 1]
        for offset in range(0, len(unmatched) - 1, 2):
            left_root = find(int(unmatched[offset]))
            right_root = find(int(unmatched[offset + 1]))
            if left_root == right_root:
                continue
            parent[right_root] = left_root
            size[left_root] = 2
            removed += 1
            if removed == removals_needed:
                break
    if removed != removals_needed:
        raise RuntimeError(
            f"GSTile adaptive LOD stalled after {removed}/{removals_needed} merges"
        )

    roots = np.fromiter((find(index) for index in range(count)), dtype=np.intp, count=count)
    unique_roots, inverse = np.unique(roots, return_inverse=True)
    minimum_source_ids = np.full(unique_roots.shape[0], np.iinfo(np.uint64).max, dtype=np.uint64)
    np.minimum.at(minimum_source_ids, inverse, records["source_id"])
    group_order = np.argsort(minimum_source_ids, kind="stable")
    rank = np.empty(group_order.shape[0], dtype=np.intp)
    rank[group_order] = np.arange(group_order.shape[0], dtype=np.intp)
    groups = rank[inverse]
    order = np.lexsort((records["source_id"], groups))
    ordered_groups = groups[order]
    starts = np.flatnonzero(
        np.concatenate(([True], ordered_groups[1:] != ordered_groups[:-1]))
    ).astype(np.intp)
    ends = np.concatenate((starts[1:], np.asarray([count], dtype=np.intp)))
    return _moment_match_ordered_groups(
        records[order],
        input_errors[order].astype(np.float64, copy=False),
        starts,
        ends,
        refit_directional_opacity=True,
    )


def _adaptive_moment_lod_proxy(
    records: np.ndarray,
    input_errors: np.ndarray,
    limit: int,
) -> _LodProxy:
    """Adaptively retain expensive detail and merge redundant local Gaussians."""

    if records.shape[0] != input_errors.shape[0]:
        raise ValueError("GSTile proxy records and errors must have matching lengths")
    proxy = _LodProxy(records.copy(), input_errors.astype(np.float64, copy=True))
    while proxy.records.shape[0] > limit:
        generation_target = max(limit, (proxy.records.shape[0] + 1) // 2)
        next_proxy = _adaptive_generation(proxy.records, proxy.errors, generation_target)
        if next_proxy.records.shape[0] >= proxy.records.shape[0]:
            raise RuntimeError("GSTile adaptive LOD failed to reduce the population")
        proxy = next_proxy
    return proxy


def _gaussian_render_bounds(
    records: np.ndarray,
    sigma: float = 3.0,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return conservative finite AABB support, including anisotropic splat extent."""

    positions = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float64, copy=False
    )
    log_scales = np.column_stack(
        (records["scale_0"], records["scale_1"], records["scale_2"])
    ).astype(np.float64, copy=False)
    scales = np.exp(np.clip(log_scales, -30.0, 30.0))
    rotations = _rotation_matrices(
        np.column_stack(
            (records["rot_0"], records["rot_1"], records["rot_2"], records["rot_3"])
        )
    )
    axis_variance = np.sum(np.square(rotations) * np.square(scales)[:, None, :], axis=2)
    extent = sigma * np.sqrt(np.maximum(axis_variance, 0.0))
    minimum = np.min(positions - extent, axis=0)
    maximum = np.max(positions + extent, axis=0)
    if not np.all(np.isfinite(minimum)) or not np.all(np.isfinite(maximum)):
        raise RuntimeError("GSTile render bounds are non-finite")
    return tuple(float(value) for value in minimum), tuple(float(value) for value in maximum)


def _proxy_support_error(records: np.ndarray) -> float:
    """Return a one-sigma proxy footprint for screen-space LOD error.

    Moment matching can preserve centers while increasing covariance. Without
    this term such a proxy reports nearly zero positional error and remains
    visibly blurred at arbitrarily close camera distances.
    """

    if records.shape[0] == 0:
        return 0.0
    log_scales = np.column_stack(
        (records["scale_0"], records["scale_1"], records["scale_2"])
    ).astype(np.float64, copy=False)
    return float(np.max(np.exp(np.clip(log_scales, -30.0, 30.0)), initial=0.0))


def _replacement_lod_proxy(
    records: np.ndarray,
    input_errors: np.ndarray,
    limit: int,
    strategy: Literal["spatial-stratified", "minhash"],
    item: _WorkFile,
) -> _LodProxy:
    if records.shape[0] <= limit:
        return _LodProxy(records.copy(), input_errors.astype(np.float64, copy=True))
    selected = _select_lod_proxy(records, limit, strategy)
    error = float(np.max(input_errors, initial=0.0)) + _geometric_error(item, selected.shape[0])
    return _LodProxy(selected, np.full(selected.shape[0], error, dtype=np.float64))



def _geometric_error(item: _WorkFile, proxy_count: int) -> float:
    diagonal = float(np.linalg.norm(np.asarray(item.bounds_max) - np.asarray(item.bounds_min)))
    return diagonal / max(float(proxy_count) ** (1.0 / 3.0), 1.0)


def _create_root_work_file(
    source: Path,
    layout: BinaryPlyLayout,
    target: Path,
    chunk_records: int,
    cancellation_check: Callable[[], None] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[_WorkFile, str]:
    dtype = _work_dtype(layout)
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    source_id = 0
    digest = hashlib.sha256()
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        header = input_handle.read(layout.header_size)
        if len(header) != layout.header_size:
            raise ValueError("PLY header ended before its declared size")
        digest.update(header)
        remaining = layout.vertex_count
        while remaining:
            if cancellation_check is not None:
                cancellation_check()
            count = min(chunk_records, remaining)
            records = np.fromfile(input_handle, dtype=layout.dtype, count=count)
            if records.shape[0] != count:
                raise ValueError("PLY payload ended before its declared vertex count")
            digest.update(memoryview(records).cast("B"))
            working = np.empty(count, dtype=dtype)
            for name in layout.dtype.names or ():
                working[name] = records[name]
            working["source_id"] = np.arange(source_id, source_id + count, dtype="<u8")
            source_id += count
            chunk_min, chunk_max = _bounds(working)
            minimum = np.minimum(minimum, chunk_min)
            maximum = np.maximum(maximum, chunk_max)
            working.tofile(output_handle)
            remaining -= count
            _emit_progress(
                progress_callback,
                "source_copy",
                processed=source_id,
                total=layout.vertex_count,
            )
        for block in iter(lambda: input_handle.read(1024 * 1024), b""):
            digest.update(block)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    return (
        _WorkFile(
            target,
            layout.vertex_count,
            tuple(float(value) for value in minimum),
            tuple(float(value) for value in maximum),
        ),
        digest.hexdigest(),
    )


def _split_work_file(
    item: _WorkFile,
    *,
    node_id: str,
    dtype: np.dtype,
    chunk_records: int,
    cancellation_check: Callable[[], None] | None = None,
) -> tuple[_WorkFile, _WorkFile]:
    extent = np.asarray(item.bounds_max) - np.asarray(item.bounds_min)
    axis = int(np.argmax(extent))
    midpoint = (item.bounds_min[axis] + item.bounds_max[axis]) / 2.0
    left_path = item.path.with_name(f"{node_id}0.work")
    right_path = item.path.with_name(f"{node_id}1.work")
    left_count = right_count = 0
    left_minimum = np.full(3, np.inf, dtype=np.float64)
    left_maximum = np.full(3, -np.inf, dtype=np.float64)
    right_minimum = np.full(3, np.inf, dtype=np.float64)
    right_maximum = np.full(3, -np.inf, dtype=np.float64)

    def record_partition_bounds(records: np.ndarray, *, left: bool) -> None:
        nonlocal left_minimum, left_maximum, right_minimum, right_maximum
        if records.size == 0:
            return
        chunk_minimum, chunk_maximum = _bounds(records)
        if left:
            left_minimum = np.minimum(left_minimum, chunk_minimum)
            left_maximum = np.maximum(left_maximum, chunk_maximum)
        else:
            right_minimum = np.minimum(right_minimum, chunk_minimum)
            right_maximum = np.maximum(right_maximum, chunk_maximum)

    with left_path.open("xb") as left, right_path.open("xb") as right:
        for records in _chunks(item.path, dtype, chunk_records, cancellation_check):
            mask = records[("x", "y", "z")[axis]] < midpoint
            left_records, right_records = records[mask], records[~mask]
            left_records.tofile(left)
            right_records.tofile(right)
            record_partition_bounds(left_records, left=True)
            record_partition_bounds(right_records, left=False)
            left_count += left_records.shape[0]
            right_count += right_records.shape[0]

    if left_count == 0 or right_count == 0:
        left_path.unlink(missing_ok=True)
        right_path.unlink(missing_ok=True)
        left_count = item.count // 2
        right_count = item.count - left_count
        left_minimum.fill(np.inf)
        left_maximum.fill(-np.inf)
        right_minimum.fill(np.inf)
        right_maximum.fill(-np.inf)
        written = 0
        with left_path.open("xb") as left, right_path.open("xb") as right:
            for records in _chunks(item.path, dtype, chunk_records, cancellation_check):
                left_remaining = max(0, left_count - written)
                left_part = records[:left_remaining]
                right_part = records[left_remaining:]
                left_part.tofile(left)
                right_part.tofile(right)
                record_partition_bounds(left_part, left=True)
                record_partition_bounds(right_part, left=False)
                written += left_part.shape[0]

    if left_count + right_count != item.count:
        raise RuntimeError("GSTile partition count changed during split")
    item.path.unlink()
    return (
        _WorkFile(
            left_path,
            left_count,
            tuple(left_minimum.tolist()),
            tuple(left_maximum.tolist()),
        ),
        _WorkFile(
            right_path,
            right_count,
            tuple(right_minimum.tolist()),
            tuple(right_maximum.tolist()),
        ),
    )


def _source_degrees(layout: BinaryPlyLayout) -> tuple[int, int]:
    color = len([name for name in layout.property_names if name.startswith("f_rest_")])
    opacity = len([name for name in layout.property_names if name.startswith("opacity_sh_")])
    color_coefficients = color // 3
    color_degree = int(round(np.sqrt(color_coefficients + 1) - 1))
    opacity_degree = int(round(np.sqrt(opacity + 1) - 1))
    return color_degree, opacity_degree


def build_gstile_bundle(
    source_ply: str | Path,
    output_directory: str | Path,
    *,
    options: GsTileBuildOptions | None = None,
) -> GsTileBuildResult:
    """Build a deterministic leaf-streaming bundle without loading the PLY whole."""

    options = options or GsTileBuildOptions()
    options.validate()
    if options.cancellation_check is not None:
        options.cancellation_check()
    source = Path(source_ply).resolve()
    output = Path(output_directory).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError(f"GSTile bundles are immutable: {output}")
    layout = read_binary_ply_layout(source)
    if layout.vertex_count < 1:
        raise ValueError("Cannot tile an empty Gaussian PLY")
    color_degree, opacity_degree = _source_degrees(layout)
    if color_degree > 3 or opacity_degree > 3:
        raise ValueError("GSTile v1 supports SH degree at most 3")

    output_parent = output.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = (options.temporary_root or output_parent).resolve()
    temporary_parent.mkdir(parents=True, exist_ok=True)
    source_payload_bytes = layout.vertex_count * layout.dtype.itemsize
    estimated_temporary = layout.vertex_count * (layout.dtype.itemsize + 8) * 2
    estimated_output = (
        layout.vertex_count * 96 + ((layout.vertex_count + options.leaf_size - 1) // options.leaf_size) * 32
    )
    if options.lod_proxy_size is not None:
        # Every source record can occur in at most one proxy per tree depth.
        # This deliberately conservative bound fails before an atomic build
        # when an adversarially imbalanced tree would exhaust the output disk.
        estimated_output += layout.vertex_count * 96 * options.maximum_depth
    reserve = 1024**3
    shared_filesystem = os.stat(temporary_parent).st_dev == os.stat(output_parent).st_dev
    temporary_required = estimated_temporary + reserve
    output_required = estimated_output + reserve
    if shared_filesystem:
        temporary_required += estimated_output
    _emit_progress(
        options.progress_callback,
        "preflight",
        gaussianCount=layout.vertex_count,
        sourceBytes=source_payload_bytes,
        estimatedTemporaryBytes=temporary_required,
        estimatedOutputBytes=output_required,
        sharedFilesystem=shared_filesystem,
    )
    if shutil.disk_usage(temporary_parent).free < temporary_required:
        raise RuntimeError(
            f"Insufficient temporary disk for GSTile atomic build: need about {temporary_required / 1024**3:.1f} GiB"
        )
    if not shared_filesystem and shutil.disk_usage(output_parent).free < output_required:
        raise RuntimeError(
            f"Insufficient output disk for GSTile atomic build: need about {output_required / 1024**3:.1f} GiB"
        )

    bundle_tmp = output_parent / f".{output.name}.partial"
    if bundle_tmp.exists():
        raise FileExistsError(f"Stale GSTile publication path exists: {bundle_tmp}")
    build_root = Path(tempfile.mkdtemp(prefix="gstile-build-", dir=temporary_parent))
    try:
        bundle_tmp.mkdir()
        (bundle_tmp / "packs").mkdir()
        work_dtype = _work_dtype(layout)
        root_work, source_sha256 = _create_root_work_file(
            source,
            layout,
            build_root / "r.work",
            options.chunk_records,
            options.cancellation_check,
            options.progress_callback,
        )
        _emit_progress(
            options.progress_callback,
            "source_ready",
            gaussianCount=root_work.count,
            sha256=source_sha256,
        )
        nodes: list[dict[str, Any]] = []
        packs: list[dict[str, Any]] = []
        maximum_errors: dict[str, float] = {}
        exact_pack_bytes = 0
        proxy_pack_bytes = 0
        proxy_records = 0
        leaf_count = 0
        adaptive_v4 = (
            options.lod_proxy_size is not None
            and options.lod_proxy_strategy == "adaptive-moment"
        )

        def pack_tile(
            records: np.ndarray,
            *,
            pack_id: str,
            node_id: str,
        ) -> tuple[dict[str, Any], int]:
            ply_records = np.empty(records.shape[0], dtype=layout.dtype)
            for name in layout.dtype.names or ():
                ply_records[name] = records[name]
            relative = Path("packs") / f"{pack_id}.gst"
            pack, errors = write_pack_atomic(
                bundle_tmp / relative,
                ply_records,
                records["source_id"],
                node_id=node_id,
            )
            pack["id"] = pack_id
            pack["path"] = relative.as_posix()
            pack["byteOffset"] = 32
            packs.append(pack)
            for key, value in errors.items():
                maximum_errors[key] = max(maximum_errors.get(key, 0.0), value)
            return (
                {
                    "pack": pack_id,
                    "byteOffset": 32,
                    "byteLength": pack["byteLength"] - 32,
                    "recordCount": records.shape[0],
                    "sha256": pack["sha256"],
                    "quantization": pack.pop("quantization"),
                },
                pack["byteLength"],
            )

        def visit(item: _WorkFile, node_id: str, depth: int) -> _LodProxy | None:
            nonlocal exact_pack_bytes, leaf_count, proxy_pack_bytes, proxy_records
            if options.cancellation_check is not None:
                options.cancellation_check()
            node: dict[str, Any] = {
                "id": node_id,
                "bounds": {"min": list(item.bounds_min), "max": list(item.bounds_max)},
                "gaussianCount": item.count,
            }
            nodes.append(node)
            if item.count <= options.leaf_size:
                records = np.fromfile(item.path, dtype=work_dtype, count=item.count)
                if records.shape[0] != item.count:
                    raise RuntimeError("GSTile leaf payload is incomplete")
                render_bounds_min: tuple[float, float, float] | None = None
                render_bounds_max: tuple[float, float, float] | None = None
                if adaptive_v4:
                    render_bounds_min, render_bounds_max = _gaussian_render_bounds(records)
                    node["renderBounds"] = {
                        "min": list(render_bounds_min),
                        "max": list(render_bounds_max),
                    }
                node["tile"], written = pack_tile(
                    records,
                    pack_id=node_id,
                    node_id=node_id,
                )
                exact_pack_bytes += written
                leaf_count += 1
                if options.lod_proxy_size is not None:
                    node["geometricError"] = 0.0
                item.path.unlink()
                _emit_progress(
                    options.progress_callback,
                    "leaf_written",
                    node=node_id,
                    depth=depth,
                    gaussianCount=item.count,
                    leafCount=leaf_count,
                )
                if options.lod_proxy_size is None:
                    return None
                input_errors = np.zeros(records.shape[0], dtype=np.float64)
                if options.lod_proxy_strategy in {"adaptive-moment", "moment-matched"}:
                    proxy = (
                        _adaptive_moment_lod_proxy(
                            records, input_errors, options.lod_proxy_size
                        )
                        if adaptive_v4
                        else _moment_matched_lod_proxy(
                            records, input_errors, options.lod_proxy_size
                        )
                    )
                    return _LodProxy(
                        proxy.records,
                        proxy.errors,
                        render_bounds_min,
                        render_bounds_max,
                    )
                return _replacement_lod_proxy(
                    records,
                    input_errors,
                    options.lod_proxy_size,
                    options.lod_proxy_strategy,
                    item,
                )
            if depth >= options.maximum_depth:
                raise RuntimeError("GSTile partition exceeded maximum depth")
            _emit_progress(
                options.progress_callback,
                "partition_split",
                node=node_id,
                depth=depth,
                gaussianCount=item.count,
            )
            left, right = _split_work_file(
                item,
                node_id=node_id,
                dtype=work_dtype,
                chunk_records=options.chunk_records,
                cancellation_check=options.cancellation_check,
            )
            node["children"] = [node_id + "0", node_id + "1"]
            left_proxy = visit(left, node_id + "0", depth + 1)
            right_proxy = visit(right, node_id + "1", depth + 1)
            if options.lod_proxy_size is None:
                return None
            if left_proxy is None or right_proxy is None:
                raise RuntimeError("GSTile LOD child proxy is missing")
            render_bounds_min: tuple[float, float, float] | None = None
            render_bounds_max: tuple[float, float, float] | None = None
            if adaptive_v4:
                if (
                    left_proxy.render_bounds_min is None
                    or left_proxy.render_bounds_max is None
                    or right_proxy.render_bounds_min is None
                    or right_proxy.render_bounds_max is None
                ):
                    raise RuntimeError("GSTile adaptive child render bounds are missing")
                render_bounds_min = tuple(
                    min(left_proxy.render_bounds_min[axis], right_proxy.render_bounds_min[axis])
                    for axis in range(3)
                )
                render_bounds_max = tuple(
                    max(left_proxy.render_bounds_max[axis], right_proxy.render_bounds_max[axis])
                    for axis in range(3)
                )
                node["renderBounds"] = {
                    "min": list(render_bounds_min),
                    "max": list(render_bounds_max),
                }
            combined_records = np.concatenate((left_proxy.records, right_proxy.records))
            combined_errors = np.concatenate((left_proxy.errors, right_proxy.errors))
            if options.lod_proxy_strategy in {"adaptive-moment", "moment-matched"}:
                proxy = (
                    _adaptive_moment_lod_proxy(
                        combined_records, combined_errors, options.lod_proxy_size
                    )
                    if adaptive_v4
                    else _moment_matched_lod_proxy(
                        combined_records, combined_errors, options.lod_proxy_size
                    )
                )
            else:
                proxy = _replacement_lod_proxy(
                    combined_records,
                    combined_errors,
                    options.lod_proxy_size,
                    options.lod_proxy_strategy,
                    item,
                )
            if adaptive_v4:
                if render_bounds_min is None or render_bounds_max is None:
                    raise RuntimeError("GSTile adaptive render bounds are missing")
                proxy_bounds_min, proxy_bounds_max = _gaussian_render_bounds(proxy.records)
                render_bounds_min = tuple(
                    min(render_bounds_min[axis], proxy_bounds_min[axis])
                    for axis in range(3)
                )
                render_bounds_max = tuple(
                    max(render_bounds_max[axis], proxy_bounds_max[axis])
                    for axis in range(3)
                )
                node["renderBounds"] = {
                    "min": list(render_bounds_min),
                    "max": list(render_bounds_max),
                }
            node["lodTile"], written = pack_tile(
                proxy.records,
                pack_id=f"lod-{node_id}",
                node_id=f"lod-{node_id}",
            )
            node["geometricError"] = max(
                float(np.max(proxy.errors, initial=0.0)),
                _proxy_support_error(proxy.records),
            )
            proxy_pack_bytes += written
            proxy_records += proxy.records.shape[0]
            _emit_progress(
                options.progress_callback,
                "lod_proxy_written",
                node=node_id,
                depth=depth,
                gaussianCount=item.count,
                proxyCount=proxy.records.shape[0],
            )
            return _LodProxy(
                proxy.records,
                proxy.errors,
                render_bounds_min,
                render_bounds_max,
            )

        visit(root_work, "r", 0)
        if options.cancellation_check is not None:
            options.cancellation_check()
        if options.lod_proxy_strategy == "adaptive-moment":
            lod_profile = GSTILE_ADAPTIVE_LOD_PROFILE
            lod_statistic = "deterministic-adaptive-cost-moment-opacity-refit-v4"
        elif options.lod_proxy_strategy == "moment-matched":
            lod_profile = GSTILE_MOMENT_LOD_PROFILE
            lod_statistic = "deterministic-morton-moment-matched-v3"
        elif options.lod_proxy_strategy == "spatial-stratified":
            lod_profile = GSTILE_STRATIFIED_LOD_PROFILE
            lod_statistic = "deterministic-morton-stratified-replacement-v2"
        else:
            lod_profile = GSTILE_LOD_PROFILE
            lod_statistic = "deterministic-minhash-replacement-v1"
        manifest: dict[str, Any] = {
            "schema": GSTILE_SCHEMA,
            "version": GSTILE_VERSION,
            "profile": (lod_profile if options.lod_proxy_size is not None else GSTILE_PROFILE),
            "bundleId": "sha256:" + "0" * 64,
            "source": {
                "sha256": source_sha256,
                "gaussianCount": layout.vertex_count,
                "colorShDegree": color_degree,
                "opacityShDegree": opacity_degree,
                "recordBytes": layout.dtype.itemsize,
            },
            "coordinateFrame": {
                "kind": "projected" if options.crs else "local",
                "origin": list(options.coordinate_origin),
                "crs": options.crs,
            },
            "root": "r",
            "nodes": nodes,
            "packs": packs,
            "statistics": {
                "leafCount": leaf_count,
                "packBytes": sum(pack["byteLength"] for pack in packs),
                "bytesPerGaussian": sum(pack["byteLength"] for pack in packs) / layout.vertex_count,
                "maximumQuantizationError": maximum_errors,
                "lod": (lod_statistic if options.lod_proxy_size is not None else "leaf-only"),
                **(
                    {
                        "exactPackBytes": exact_pack_bytes,
                        "proxyCount": sum(1 for node in nodes if "lodTile" in node),
                        "proxyRecords": proxy_records,
                        "proxyPackBytes": proxy_pack_bytes,
                    }
                    if options.lod_proxy_size is not None
                    else {}
                ),
            },
        }
        validate_manifest(manifest)
        identity_payload = json.loads(json.dumps(manifest))
        identity_payload["bundleId"] = None
        bundle_hash = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest["bundleId"] = f"sha256:{bundle_hash}"
        manifest_bytes = canonical_manifest_bytes(manifest)
        with (bundle_tmp / "manifest.json").open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(bundle_tmp, output)
        _emit_progress(
            options.progress_callback,
            "published",
            bundleId=manifest["bundleId"],
            gaussianCount=layout.vertex_count,
            leafCount=manifest["statistics"]["leafCount"],
            packBytes=manifest["statistics"]["packBytes"],
        )
        return GsTileBuildResult(
            output=output,
            manifest_path=output / "manifest.json",
            bundle_id=manifest["bundleId"],
            gaussian_count=layout.vertex_count,
            leaf_count=manifest["statistics"]["leafCount"],
            pack_bytes=manifest["statistics"]["packBytes"],
            source_bytes=source_payload_bytes,
            maximum_errors=maximum_errors,
        )
    except Exception:
        shutil.rmtree(bundle_tmp, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
