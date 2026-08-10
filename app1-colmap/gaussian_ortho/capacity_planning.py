"""Deterministic scene and VRAM-aware Gaussian capacity planning."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.spatial import ConvexHull, QhullError


GIB = 1024**3
CAPACITY_QUANTUM = 100_000
GAUSSIAN_CAPACITY_BYTES = 1_280
VRAM_USABLE_FRACTION = 0.85
VRAM_FIXED_RESERVE_BYTES = 4 * GIB


@dataclass(frozen=True)
class GaussianCapacityPlan:
    """Auditable inputs and result of one capacity decision."""

    mode: str
    requested_cap: int
    capacity_floor: int
    target_spacing_pixels: float
    robust_ground_area_m2: float | None
    requested_gsd_m: float
    target_output_pixels: int | None
    surface_target: int | None
    free_vram_bytes: int | None
    total_vram_bytes: int | None
    vram_cap: int | None
    effective_scene_cap: int
    effective_cell_cap: int
    cell_count: int
    estimated_capacity_bytes: int

    def as_dict(self) -> dict[str, int | float | str | None]:
        return asdict(self)


def _round_up(value: float, quantum: int = CAPACITY_QUANTUM) -> int:
    return int(math.ceil(value / quantum) * quantum)


def _round_down(value: float, quantum: int = CAPACITY_QUANTUM) -> int:
    return max(quantum, int(value // quantum) * quantum)


def robust_ground_area_m2(
    points: np.ndarray,
    *,
    meters_per_model_unit: float,
    quantile: float = 0.005,
) -> float:
    """Estimate the surveyed plane area without depending on world orientation."""

    xyz = np.asarray(points, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or xyz.shape[0] < 3:
        raise ValueError("at least three 3D points are required for capacity planning")
    if not np.isfinite(xyz).all():
        raise ValueError("capacity planning points must be finite")
    if meters_per_model_unit <= 0 or not math.isfinite(meters_per_model_unit):
        raise ValueError("meters_per_model_unit must be positive and finite")
    if not 0.0 <= quantile < 0.5:
        raise ValueError("capacity area quantile must be in [0, 0.5)")

    centered = xyz - np.median(xyz, axis=0)
    if centered.shape[0] >= 200:
        radial_distance = np.linalg.norm(centered, axis=1)
        radial_limit = float(np.quantile(radial_distance, 0.995))
        centered = centered[radial_distance <= radial_limit]
    _u, _singular_values, axes = np.linalg.svd(centered, full_matrices=False)
    planar = centered @ axes[:2].T
    lower = np.quantile(planar, quantile, axis=0)
    upper = np.quantile(planar, 1.0 - quantile, axis=0)
    retained = planar[
        np.all((planar >= lower) & (planar <= upper), axis=1)
    ]
    try:
        planar_area = float(ConvexHull(retained).volume)
    except QhullError:
        width, height = np.maximum(upper - lower, 0.0)
        planar_area = float(width * height)
    area = planar_area * meters_per_model_unit**2
    if not math.isfinite(area) or area <= 0:
        raise ValueError("capacity planning produced an invalid ground area")
    return area


def detected_vram_bytes(cupy_module: Any) -> tuple[int, int] | None:
    """Return free and total device memory without allocating a buffer."""

    try:
        free_bytes, total_bytes = cupy_module.cuda.Device(0).mem_info
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    free = int(free_bytes)
    total = int(total_bytes)
    return (free, total) if 0 < free <= total else None


def vram_gaussian_cap(
    total_vram_bytes: int,
    free_vram_bytes: int | None = None,
) -> int:
    """Convert device memory to a conservative native-trainer capacity."""

    if total_vram_bytes <= VRAM_FIXED_RESERVE_BYTES:
        return CAPACITY_QUANTUM
    usable_from_total = (
        total_vram_bytes * VRAM_USABLE_FRACTION
        - VRAM_FIXED_RESERVE_BYTES
    )
    usable = usable_from_total
    if free_vram_bytes is not None:
        usable = min(
            usable,
            free_vram_bytes - VRAM_FIXED_RESERVE_BYTES,
        )
    return _round_down(usable / GAUSSIAN_CAPACITY_BYTES)


def plan_gaussian_capacity(
    *,
    mode: str,
    requested_cap: int,
    capacity_floor: int,
    target_spacing_pixels: float,
    points: np.ndarray,
    meters_per_model_unit: float,
    requested_gsd_m: float,
    total_vram_bytes: int | None,
    free_vram_bytes: int | None = None,
    cell_count: int = 1,
) -> GaussianCapacityPlan:
    """Resolve a global scene cap and an equal per-partition cap."""

    if mode not in {"fixed", "adaptive"}:
        raise ValueError("Gaussian capacity mode must be fixed or adaptive")
    if requested_cap < CAPACITY_QUANTUM:
        raise ValueError("requested Gaussian cap is too small")
    if not CAPACITY_QUANTUM <= capacity_floor <= requested_cap:
        raise ValueError("Gaussian capacity floor must not exceed its cap")
    if cell_count < 1:
        raise ValueError("Gaussian capacity cell count must be positive")
    if requested_gsd_m <= 0 or not math.isfinite(requested_gsd_m):
        raise ValueError("requested GSD must be positive and finite")

    area: float | None = None
    output_pixels: int | None = None
    surface_target: int | None = None
    memory_cap = (
        vram_gaussian_cap(total_vram_bytes, free_vram_bytes)
        if total_vram_bytes is not None
        else None
    )

    if mode == "fixed":
        effective = requested_cap
    else:
        if target_spacing_pixels <= 0 or not math.isfinite(target_spacing_pixels):
            raise ValueError("adaptive Gaussian spacing must be positive and finite")
        area = robust_ground_area_m2(
            points,
            meters_per_model_unit=meters_per_model_unit,
        )
        output_pixels = max(1, math.ceil(area / requested_gsd_m**2))
        surface_target = _round_up(
            output_pixels / target_spacing_pixels**2
        )
        desired = max(capacity_floor, surface_target)
        effective = min(requested_cap, desired)
        if memory_cap is not None:
            effective = min(effective, memory_cap)
        effective = max(CAPACITY_QUANTUM, _round_down(effective))

    per_cell = _round_up(effective / cell_count)
    return GaussianCapacityPlan(
        mode=mode,
        requested_cap=requested_cap,
        capacity_floor=capacity_floor,
        target_spacing_pixels=target_spacing_pixels,
        robust_ground_area_m2=area,
        requested_gsd_m=requested_gsd_m,
        target_output_pixels=output_pixels,
        surface_target=surface_target,
        free_vram_bytes=free_vram_bytes,
        total_vram_bytes=total_vram_bytes,
        vram_cap=memory_cap,
        effective_scene_cap=effective,
        effective_cell_cap=per_cell,
        cell_count=cell_count,
        estimated_capacity_bytes=effective * GAUSSIAN_CAPACITY_BYTES,
    )
