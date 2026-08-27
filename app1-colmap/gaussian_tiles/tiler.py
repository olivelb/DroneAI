"""Out-of-core spatial partitioner for immutable GSTile v1 bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from functools import partial
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
    PACK_HEADER_SIZE,
    PACK_RECORD_SIZE,
    PreparedPack,
    canonical_manifest_bytes,
    encode_pack,
    prepare_encoded_pack,
    validate_manifest,
    write_bundle_aggregate_pack_atomic,
    write_prepared_pack_atomic,
)
from .pack_preparation import OrderedPackPreparation


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
    invisible_gaussian_scale_threshold: float | None = None
    visibility_opacity_threshold: float = 0.05
    pack_target_bytes: int | None = None
    pack_workers: int = 1
    pack_pending_bytes: int = 128 * 1024**2

    def validate(self) -> None:
        if type(self.pack_workers) is not int or self.pack_workers not in (1, 2, 4):
            raise ValueError("GSTile pack_workers must be 1, 2 or 4")
        if type(self.pack_pending_bytes) is not int or not 1024**2 <= self.pack_pending_bytes <= 1024**3:
            raise ValueError("GSTile pack_pending_bytes must be between 1 MiB and 1 GiB")
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
        if self.invisible_gaussian_scale_threshold is not None and (
            not np.isfinite(self.invisible_gaussian_scale_threshold)
            or self.invisible_gaussian_scale_threshold <= 0.0
        ):
            raise ValueError(
                "GSTile invisible_gaussian_scale_threshold must be finite and positive"
            )
        if (
            not np.isfinite(self.visibility_opacity_threshold)
            or not 0.0 < self.visibility_opacity_threshold < 1.0
        ):
            raise ValueError(
                "GSTile visibility_opacity_threshold must be strictly between 0 and 1"
            )
        if self.pack_target_bytes is not None and (
            isinstance(self.pack_target_bytes, bool)
            or not PACK_HEADER_SIZE + PACK_RECORD_SIZE
            <= self.pack_target_bytes
            <= 1024**3
        ):
            raise ValueError(
                "GSTile pack_target_bytes must be between 128 bytes and 1 GiB"
            )
        if len(self.coordinate_origin) != 3 or not all(np.isfinite(value) for value in self.coordinate_origin):
            raise ValueError("GSTile coordinate origin must contain three finite values")


@dataclass(frozen=True)
class GsTileBuildResult:
    output: Path
    manifest_path: Path
    bundle_id: str
    gaussian_count: int
    input_gaussian_count: int
    filtered_gaussian_count: int
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


@dataclass
class _PendingEncodedTile:
    payload: memoryview
    tile: dict[str, Any]
    errors: dict[str, float]


@dataclass(frozen=True)
class _PreparedTile:
    content: bytes
    quantization: dict[str, Any]
    errors: dict[str, float]
    pack: PreparedPack | None


def _prepare_tile(records: np.ndarray, source_ids: np.ndarray, node_id: str, standalone: bool) -> _PreparedTile:
    content, quantization, errors = encode_pack(records, source_ids, node_id=node_id)
    return _PreparedTile(content, quantization, errors, prepare_encoded_pack(content) if standalone else None)


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
    axes = tuple(np.asarray(records[name]) for name in ("x", "y", "z"))
    if not all(np.all(np.isfinite(axis)) for axis in axes):
        raise ValueError("PLY contains non-finite Gaussian positions")
    # Avoid materializing an N x 3 float64 matrix for every chunk at every
    # hierarchy depth. The three strided reductions retain the exact bounds
    # while keeping peak scratch memory independent of the chunk population.
    return (
        np.asarray([axis.min() for axis in axes], dtype=np.float64),
        np.asarray([axis.max() for axis in axes], dtype=np.float64),
    )


def _copy_record_prefix(source: np.ndarray, destination: np.ndarray) -> None:
    """Copy the common structured-record prefix with one contiguous transfer."""

    if source.shape != destination.shape or source.ndim != 1:
        raise ValueError("GSTile record copies require matching one-dimensional arrays")
    prefix_bytes = min(source.dtype.itemsize, destination.dtype.itemsize)
    source_bytes = source.view(np.uint8).reshape(source.shape[0], source.dtype.itemsize)
    destination_bytes = destination.view(np.uint8).reshape(
        destination.shape[0], destination.dtype.itemsize
    )
    destination_bytes[:, :prefix_bytes] = source_bytes[:, :prefix_bytes]


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


def _invisible_giant_mask(
    records: np.ndarray,
    scale_threshold: float | None,
    opacity_threshold: float,
) -> np.ndarray:
    """Select giant splats proven invisible for every viewing direction.

    The real SH addition theorem bounds each degree's directional contribution:
    ``dot(c_l, Y_l(direction)) <= ||c_l|| sqrt((2l + 1) / 4pi)``.
    Filtering against that upper bound cannot discard a splat that reaches the
    configured opacity threshold in any direction. Small low-opacity splats and
    large visible splats are deliberately retained.
    """

    if scale_threshold is None or records.size == 0:
        return np.zeros(records.shape[0], dtype=np.bool_)
    log_scales = np.column_stack(
        (records["scale_0"], records["scale_1"], records["scale_2"])
    ).astype(np.float64, copy=False)
    if not np.all(np.isfinite(log_scales)):
        raise ValueError("PLY contains non-finite Gaussian scales")
    giant = np.max(log_scales, axis=1) > np.log(scale_threshold)
    if not np.any(giant):
        return giant

    upper_logit = records["opacity"].astype(np.float64, copy=True)
    opacity_names = _opacity_property_names(records)
    start = 0
    for degree, width in ((1, 3), (2, 5), (3, 7)):
        if start >= len(opacity_names):
            break
        stop = min(start + width, len(opacity_names))
        coefficients = np.column_stack(
            tuple(records[name] for name in opacity_names[start:stop])
        ).astype(np.float64, copy=False)
        upper_logit += np.linalg.norm(coefficients, axis=1) * np.sqrt(
            (2 * degree + 1) / (4.0 * np.pi)
        )
        start = stop
    if start != len(opacity_names):
        raise ValueError("DroneGS opacity SH properties do not encode degree 0 through 3")
    if not np.all(np.isfinite(upper_logit)):
        raise ValueError("PLY contains non-finite Gaussian opacity")
    threshold_logit = np.log(opacity_threshold / (1.0 - opacity_threshold))
    return giant & (upper_logit < threshold_logit)


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


def _positions_and_scales(records: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the common finite-space inputs used by LOD and bounds calculations."""

    positions = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float64, copy=False
    )
    log_scales = np.column_stack(
        (records["scale_0"], records["scale_1"], records["scale_2"])
    ).astype(np.float64, copy=False)
    scales = np.exp(np.clip(log_scales, -30.0, 30.0))
    return positions, log_scales, scales


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
    positions, log_scales, scales = _positions_and_scales(records)
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


def _adaptive_pair_roots(
    records: np.ndarray,
    left: np.ndarray,
    right: np.ndarray,
    target: int,
) -> np.ndarray:
    """Match disjoint pairs in candidate order, preserving V4's exact roots."""

    count = records.shape[0]
    roots = np.arange(count, dtype=np.intp)
    matched = bytearray(count)
    removals_needed = count - target
    removed = 0
    # A generation only merges two singletons. Matched endpoints cannot be
    # accepted again, so roots stay flat and no union-find lookup is needed.
    # Memoryviews iterate native integers without copying the candidate arrays.
    for left_value, right_value in zip(memoryview(left), memoryview(right), strict=True):
        if matched[left_value] or matched[right_value] or left_value == right_value:
            continue
        matched[left_value] = matched[right_value] = 1
        if left_value > right_value:
            left_value, right_value = right_value, left_value
        roots[right_value] = left_value
        removed += 1
        if removed == removals_needed:
            break
    if removed != removals_needed:
        # A greedy matching can leave unmatched vertices even on a connected
        # candidate graph. Complete it in Morton order so every generation has
        # an exact, deterministic population and never stalls a large build.
        morton_order = np.lexsort((records["source_id"], _morton_codes(records)))
        unmatched = morton_order[~np.frombuffer(matched, dtype=np.bool_)[morton_order]]
        pair_count = min(removals_needed - removed, unmatched.size // 2)
        # The original completion pass chooses the first Morton endpoint as
        # root, not necessarily the lower index used by the greedy pass.
        roots[unmatched[1:2 * pair_count:2]] = unmatched[:2 * pair_count:2]
        removed += pair_count
    if removed != removals_needed:
        raise RuntimeError(
            f"GSTile adaptive LOD stalled after {removed}/{removals_needed} merges"
        )

    return roots


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
    roots = _adaptive_pair_roots(records, left, right, target)
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

    positions, _log_scales, scales = _positions_and_scales(records)
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
    invisible_gaussian_scale_threshold: float | None = None,
    visibility_opacity_threshold: float = 0.05,
) -> tuple[_WorkFile, str, int]:
    dtype = _work_dtype(layout)
    minimum = np.full(3, np.inf, dtype=np.float64)
    maximum = np.full(3, -np.inf, dtype=np.float64)
    source_id = 0
    kept_count = 0
    filtered_count = 0
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
            _copy_record_prefix(records, working)
            working["source_id"] = np.arange(source_id, source_id + count, dtype="<u8")
            source_id += count
            filtered = _invisible_giant_mask(
                working,
                invisible_gaussian_scale_threshold,
                visibility_opacity_threshold,
            )
            filtered_count += int(np.count_nonzero(filtered))
            working = working[~filtered]
            if working.size:
                chunk_min, chunk_max = _bounds(working)
                minimum = np.minimum(minimum, chunk_min)
                maximum = np.maximum(maximum, chunk_max)
                working.tofile(output_handle)
                kept_count += working.shape[0]
            remaining -= count
            _emit_progress(
                progress_callback,
                "source_copy",
                processed=source_id,
                total=layout.vertex_count,
                kept=kept_count,
                filtered=filtered_count,
            )
        for block in iter(lambda: input_handle.read(1024 * 1024), b""):
            digest.update(block)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    if kept_count < 1:
        raise ValueError("GSTile source filtering removed every Gaussian")
    return (
        _WorkFile(
            target,
            kept_count,
            tuple(float(value) for value in minimum),
            tuple(float(value) for value in maximum),
        ),
        digest.hexdigest(),
        filtered_count,
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

    # Coincident centers necessarily produce an empty midpoint partition.
    # Go straight to the identical stable count split, avoiding a full read/write.
    if extent[axis] > 0.0:
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
        if extent[axis] > 0.0:
            left_path.unlink()
            right_path.unlink()
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


class _GsTileTreeBuilder:
    """Stateful deterministic tree writer used by the atomic bundle publisher."""

    def __init__(
        self,
        layout: BinaryPlyLayout,
        options: GsTileBuildOptions,
        bundle_tmp: Path,
    ) -> None:
        self.layout = layout
        self.options = options
        self.bundle_tmp = bundle_tmp
        self.work_dtype = _work_dtype(layout)
        self.adaptive_v4 = (
            options.lod_proxy_size is not None
            and options.lod_proxy_strategy == "adaptive-moment"
        )
        self.nodes: list[dict[str, Any]] = []
        self.packs: list[dict[str, Any]] = []
        self.maximum_errors: dict[str, float] = {}
        self.exact_pack_bytes = 0
        self.proxy_pack_bytes = 0
        self.proxy_records = 0
        self.leaf_count = 0
        self.pending_tiles: dict[
            tuple[Literal["exact", "proxy"], int], list[_PendingEncodedTile]
        ] = {}
        self.pending_payload_bytes: dict[
            tuple[Literal["exact", "proxy"], int], int
        ] = {}
        self.aggregate_sequences: dict[Literal["exact", "proxy"], int] = {
            "exact": 0,
            "proxy": 0,
        }
        self.preparation = OrderedPackPreparation[_PreparedTile](
            options.pack_workers, options.pack_pending_bytes, options.cancellation_check,
        )

    def pack_tile(
        self,
        records: np.ndarray,
        *,
        pack_id: str,
        node_id: str,
        kind: Literal["exact", "proxy"],
        depth: int,
    ) -> dict[str, Any]:
        # Own the inputs before the traversal reuses/releases records. Reserve
        # input arrays plus raw and retained compressed output, not NumPy scratch.
        raw_bytes = PACK_HEADER_SIZE + records.shape[0] * PACK_RECORD_SIZE
        reserved_bytes = records.shape[0] * (self.layout.dtype.itemsize + 8) + 2 * raw_bytes
        self.preparation.make_room(reserved_bytes)
        ply_records = np.empty(records.shape[0], dtype=self.layout.dtype)
        _copy_record_prefix(records, ply_records)
        source_ids = records["source_id"].copy()
        ply_records.flags.writeable = False
        source_ids.flags.writeable = False
        tile: dict[str, Any] = {}
        self.preparation.submit(
            reserved_bytes,
            partial(_prepare_tile, ply_records, source_ids, node_id, self.options.pack_target_bytes is None),
            lambda prepared: self._accept_tile(prepared, tile, pack_id=pack_id, kind=kind, depth=depth),
        )
        return tile

    def _accept_tile(
        self, prepared: _PreparedTile, tile: dict[str, Any], *,
        pack_id: str, kind: Literal["exact", "proxy"], depth: int,
    ) -> None:
        content, quantization, errors = prepared.content, prepared.quantization, prepared.errors
        record_count = (len(content) - PACK_HEADER_SIZE) // PACK_RECORD_SIZE
        if self.options.pack_target_bytes is not None:
            payload = memoryview(content)[PACK_HEADER_SIZE:]
            key = (kind, depth)
            pending = self.pending_tiles.setdefault(key, [])
            self.pending_payload_bytes.setdefault(key, 0)
            target_payload_bytes = self.options.pack_target_bytes - PACK_HEADER_SIZE
            if (
                pending
                and self.pending_payload_bytes[key] + len(payload)
                > target_payload_bytes
            ):
                self._flush_aggregate(key)
                pending = self.pending_tiles.setdefault(key, [])
            tile.update({
                "pack": "",
                "byteOffset": 0,
                "byteLength": len(payload),
                "recordCount": record_count,
                "sha256": "",
                "quantization": quantization,
            })
            pending.append(
                _PendingEncodedTile(payload=payload, tile=tile, errors=errors)
            )
            self.pending_payload_bytes[key] += len(payload)
            if self.pending_payload_bytes[key] >= target_payload_bytes:
                self._flush_aggregate(key)
            self._enforce_aggregate_memory_bound()
            return

        relative = Path("packs") / f"{pack_id}.gst"
        if prepared.pack is None:
            raise RuntimeError("GSTile standalone pack preparation is missing")
        pack = write_prepared_pack_atomic(self.bundle_tmp / relative, prepared.pack)
        pack["id"] = pack_id
        pack["path"] = relative.as_posix()
        if zstd_encoding := pack.get("encodings", {}).get("zstd"):
            zstd_encoding["path"] = relative.with_suffix(
                relative.suffix + ".zst"
            ).as_posix()
        pack["byteOffset"] = PACK_HEADER_SIZE
        self.packs.append(pack)
        tile.update({
            "pack": pack_id,
            "byteOffset": PACK_HEADER_SIZE,
            "byteLength": pack["byteLength"] - PACK_HEADER_SIZE,
            "recordCount": record_count,
            "sha256": pack["sha256"],
            "quantization": quantization,
        })
        self._record_errors(errors)
        if kind == "exact":
            self.exact_pack_bytes += pack["byteLength"]
        else:
            self.proxy_pack_bytes += pack["byteLength"]

    def _record_errors(self, errors: dict[str, float]) -> None:
        for key, value in errors.items():
            self.maximum_errors[key] = max(self.maximum_errors.get(key, 0.0), value)

    def _enforce_aggregate_memory_bound(self) -> None:
        """Keep depth-local aggregation bounded under adversarial trees."""

        maximum_pending_bytes = 256 * 1024**2
        while sum(self.pending_payload_bytes.values()) > maximum_pending_bytes:
            key = min(
                self.pending_payload_bytes,
                key=lambda candidate: (-self.pending_payload_bytes[candidate], candidate),
            )
            self._flush_aggregate(key)

    def _flush_aggregate(
        self, key: tuple[Literal["exact", "proxy"], int]
    ) -> None:
        kind, depth = key
        pending = self.pending_tiles.get(key, [])
        if not pending:
            return
        sequence = self.aggregate_sequences[kind]
        self.aggregate_sequences[kind] += 1
        pack_id = f"aggregate-{kind}-{sequence:06d}"
        pack = write_bundle_aggregate_pack_atomic(
            self.bundle_tmp,
            [entry.payload for entry in pending],
            pack_id=pack_id,
        )
        byte_offset = PACK_HEADER_SIZE
        for entry in pending:
            entry.tile["pack"] = pack_id
            entry.tile["byteOffset"] = byte_offset
            entry.tile["sha256"] = pack["sha256"]
            byte_offset += len(entry.payload)
            self._record_errors(entry.errors)
        if byte_offset != pack["byteLength"]:
            raise RuntimeError("GSTile aggregate payload accounting mismatch")
        self.packs.append(pack)
        if kind == "exact":
            self.exact_pack_bytes += pack["byteLength"]
        else:
            self.proxy_pack_bytes += pack["byteLength"]
        _emit_progress(
            self.options.progress_callback,
            "pack_written",
            pack=pack_id,
            kind=kind,
            depth=depth,
            tileCount=len(pending),
            gaussianCount=pack["recordCount"],
            byteLength=pack["byteLength"],
        )
        self.pending_tiles[key] = []
        self.pending_payload_bytes[key] = 0

    def finish(self) -> None:
        """Flush bounded aggregate buffers before manifest publication."""
        self.preparation.drain()
        for key in sorted(self.pending_tiles):
            self._flush_aggregate(key)
        _emit_progress(self.options.progress_callback, "pack_preparation",
                       workers=self.options.pack_workers, maximumPendingBytes=self.options.pack_pending_bytes,
                       peakPendingBytes=self.preparation.peak_bytes, peakPendingTasks=self.preparation.peak_tasks,
                       oversizedInlineTasks=self.preparation.oversized_inline_tasks)

    def _lod_proxy(
        self,
        records: np.ndarray,
        errors: np.ndarray,
        item: _WorkFile,
    ) -> _LodProxy:
        limit = self.options.lod_proxy_size
        if limit is None:
            raise RuntimeError("GSTile LOD proxy requested without a configured size")
        if self.options.lod_proxy_strategy == "adaptive-moment":
            return _adaptive_moment_lod_proxy(records, errors, limit)
        if self.options.lod_proxy_strategy == "moment-matched":
            return _moment_matched_lod_proxy(records, errors, limit)
        return _replacement_lod_proxy(
            records,
            errors,
            limit,
            self.options.lod_proxy_strategy,
            item,
        )

    @staticmethod
    def _combined_render_bounds(
        left: _LodProxy,
        right: _LodProxy,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if (
            left.render_bounds_min is None
            or left.render_bounds_max is None
            or right.render_bounds_min is None
            or right.render_bounds_max is None
        ):
            raise RuntimeError("GSTile adaptive child render bounds are missing")
        minimum = tuple(
            min(left.render_bounds_min[axis], right.render_bounds_min[axis])
            for axis in range(3)
        )
        maximum = tuple(
            max(left.render_bounds_max[axis], right.render_bounds_max[axis])
            for axis in range(3)
        )
        return minimum, maximum

    @staticmethod
    def _expanded_render_bounds(
        minimum: tuple[float, float, float],
        maximum: tuple[float, float, float],
        proxy: _LodProxy,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        proxy_minimum, proxy_maximum = _gaussian_render_bounds(proxy.records)
        return (
            tuple(min(minimum[axis], proxy_minimum[axis]) for axis in range(3)),
            tuple(max(maximum[axis], proxy_maximum[axis]) for axis in range(3)),
        )

    def _visit_leaf(
        self,
        item: _WorkFile,
        node: dict[str, Any],
        node_id: str,
        depth: int,
    ) -> _LodProxy | None:
        records = np.fromfile(item.path, dtype=self.work_dtype, count=item.count)
        if records.shape[0] != item.count:
            raise RuntimeError("GSTile leaf payload is incomplete")
        render_bounds_min: tuple[float, float, float] | None = None
        render_bounds_max: tuple[float, float, float] | None = None
        if self.adaptive_v4:
            render_bounds_min, render_bounds_max = _gaussian_render_bounds(records)
            node["renderBounds"] = {
                "min": list(render_bounds_min),
                "max": list(render_bounds_max),
            }
        node["tile"] = self.pack_tile(
            records,
            pack_id=node_id,
            node_id=node_id,
            kind="exact",
            depth=depth,
        )
        self.leaf_count += 1
        if self.options.lod_proxy_size is not None:
            node["geometricError"] = 0.0
        item.path.unlink()
        _emit_progress(
            self.options.progress_callback,
            "leaf_written",
            node=node_id,
            depth=depth,
            gaussianCount=item.count,
            leafCount=self.leaf_count,
        )
        if self.options.lod_proxy_size is None:
            return None
        proxy = self._lod_proxy(records, np.zeros(records.shape[0], dtype=np.float64), item)
        return _LodProxy(
            proxy.records,
            proxy.errors,
            render_bounds_min,
            render_bounds_max,
        )

    def _visit_branch(
        self,
        item: _WorkFile,
        node: dict[str, Any],
        node_id: str,
        depth: int,
    ) -> _LodProxy | None:
        if depth >= self.options.maximum_depth:
            raise RuntimeError("GSTile partition exceeded maximum depth")
        _emit_progress(
            self.options.progress_callback,
            "partition_split",
            node=node_id,
            depth=depth,
            gaussianCount=item.count,
        )
        left, right = _split_work_file(
            item,
            node_id=node_id,
            dtype=self.work_dtype,
            chunk_records=self.options.chunk_records,
            cancellation_check=self.options.cancellation_check,
        )
        node["children"] = [node_id + "0", node_id + "1"]
        left_proxy = self.visit(left, node_id + "0", depth + 1)
        right_proxy = self.visit(right, node_id + "1", depth + 1)
        if self.options.lod_proxy_size is None:
            return None
        if left_proxy is None or right_proxy is None:
            raise RuntimeError("GSTile LOD child proxy is missing")
        return self._write_branch_proxy(item, node, node_id, depth, left_proxy, right_proxy)

    def _write_branch_proxy(
        self,
        item: _WorkFile,
        node: dict[str, Any],
        node_id: str,
        depth: int,
        left_proxy: _LodProxy,
        right_proxy: _LodProxy,
    ) -> _LodProxy:
        render_bounds_min: tuple[float, float, float] | None = None
        render_bounds_max: tuple[float, float, float] | None = None
        if self.adaptive_v4:
            render_bounds_min, render_bounds_max = self._combined_render_bounds(
                left_proxy, right_proxy
            )
        combined_records = np.concatenate((left_proxy.records, right_proxy.records))
        combined_errors = np.concatenate((left_proxy.errors, right_proxy.errors))
        proxy = self._lod_proxy(combined_records, combined_errors, item)
        if self.adaptive_v4:
            if render_bounds_min is None or render_bounds_max is None:
                raise RuntimeError("GSTile adaptive render bounds are missing")
            render_bounds_min, render_bounds_max = self._expanded_render_bounds(
                render_bounds_min, render_bounds_max, proxy
            )
            node["renderBounds"] = {
                "min": list(render_bounds_min),
                "max": list(render_bounds_max),
            }
        node["lodTile"] = self.pack_tile(
            proxy.records,
            pack_id=f"lod-{node_id}",
            node_id=f"lod-{node_id}",
            kind="proxy",
            depth=depth,
        )
        node["geometricError"] = max(
            float(np.max(proxy.errors, initial=0.0)),
            _proxy_support_error(proxy.records),
        )
        self.proxy_records += proxy.records.shape[0]
        _emit_progress(
            self.options.progress_callback,
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

    def visit(self, item: _WorkFile, node_id: str, depth: int) -> _LodProxy | None:
        if self.options.cancellation_check is not None:
            self.options.cancellation_check()
        node: dict[str, Any] = {
            "id": node_id,
            "bounds": {"min": list(item.bounds_min), "max": list(item.bounds_max)},
            "gaussianCount": item.count,
        }
        self.nodes.append(node)
        if item.count <= self.options.leaf_size:
            return self._visit_leaf(item, node, node_id, depth)
        return self._visit_branch(item, node, node_id, depth)


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
        root_work, source_sha256, filtered_gaussian_count = _create_root_work_file(
            source,
            layout,
            build_root / "r.work",
            options.chunk_records,
            options.cancellation_check,
            options.progress_callback,
            options.invisible_gaussian_scale_threshold,
            options.visibility_opacity_threshold,
        )
        _emit_progress(
            options.progress_callback,
            "source_ready",
            gaussianCount=root_work.count,
            inputGaussianCount=layout.vertex_count,
            filteredGaussianCount=filtered_gaussian_count,
            sha256=source_sha256,
        )
        tree = _GsTileTreeBuilder(layout, options, bundle_tmp)
        try:
            tree.visit(root_work, "r", 0)
            tree.finish()
        finally:
            # Join pure workers before the publisher's exception cleanup.
            tree.preparation.close()
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
                "gaussianCount": root_work.count,
                "inputGaussianCount": layout.vertex_count,
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
            "nodes": tree.nodes,
            "packs": tree.packs,
            "statistics": {
                "leafCount": tree.leaf_count,
                "packCount": len(tree.packs),
                "representationCount": tree.leaf_count
                + sum(1 for node in tree.nodes if "lodTile" in node),
                **(
                    {
                        "packTargetBytes": options.pack_target_bytes,
                        "packGrouping": "depth-spatial-v1",
                    }
                    if options.pack_target_bytes is not None
                    else {}
                ),
                "packBytes": sum(pack["byteLength"] for pack in tree.packs),
                "bytesPerGaussian": sum(pack["byteLength"] for pack in tree.packs)
                / root_work.count,
                "filteredGaussianCount": filtered_gaussian_count,
                "invisibleGiantFilter": {
                    "scaleThreshold": options.invisible_gaussian_scale_threshold,
                    "opacityThreshold": options.visibility_opacity_threshold,
                },
                "maximumQuantizationError": tree.maximum_errors,
                "lod": (lod_statistic if options.lod_proxy_size is not None else "leaf-only"),
                **(
                    {
                        "exactPackBytes": tree.exact_pack_bytes,
                        "proxyCount": sum(1 for node in tree.nodes if "lodTile" in node),
                        "proxyRecords": tree.proxy_records,
                        "proxyPackBytes": tree.proxy_pack_bytes,
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
            gaussianCount=root_work.count,
            inputGaussianCount=layout.vertex_count,
            filteredGaussianCount=filtered_gaussian_count,
            leafCount=manifest["statistics"]["leafCount"],
            packBytes=manifest["statistics"]["packBytes"],
        )
        return GsTileBuildResult(
            output=output,
            manifest_path=output / "manifest.json",
            bundle_id=manifest["bundleId"],
            gaussian_count=root_work.count,
            input_gaussian_count=layout.vertex_count,
            filtered_gaussian_count=filtered_gaussian_count,
            leaf_count=manifest["statistics"]["leafCount"],
            pack_bytes=manifest["statistics"]["packBytes"],
            source_bytes=source_payload_bytes,
            maximum_errors=tree.maximum_errors,
        )
    except Exception:
        shutil.rmtree(bundle_tmp, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
