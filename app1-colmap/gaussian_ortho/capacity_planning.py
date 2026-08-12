"""Deterministic scene and VRAM-aware Gaussian capacity planning."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, cast

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
    resident_cap: int
    partition_overlap: float
    buffer_capacity_factor: float
    required_cell_count: int
    cells_sufficient: bool
    effective_scene_cap: int
    effective_cell_cap: int
    cell_count: int
    estimated_capacity_bytes: int

    def as_dict(self) -> dict[str, int | float | str | None]:
        return asdict(self)


@dataclass(frozen=True)
class GaussianDensityAssessment:
    """Post-filter evidence that an adaptive output GSD is supportable."""

    robust_ground_area_m2: float
    requested_gsd_m: float
    target_spacing_pixels: float
    actual_gaussian_count: int
    required_gaussian_count: int
    achieved_spacing_m: float
    achieved_spacing_pixels: float
    minimum_compatible_gsd_m: float
    accepted: bool

    def as_dict(self) -> dict[str, int | float | bool]:
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
    partition_overlap: float = 0.20,
    resident_partitioning: bool = False,
) -> GaussianCapacityPlan:
    """Resolve merged-scene density and a safe resident-block capacity."""

    if mode not in {"fixed", "adaptive"}:
        raise ValueError("Gaussian capacity mode must be fixed or adaptive")
    if requested_cap < CAPACITY_QUANTUM:
        raise ValueError("requested Gaussian cap is too small")
    if not CAPACITY_QUANTUM <= capacity_floor <= requested_cap:
        raise ValueError("Gaussian capacity floor must not exceed its cap")
    if cell_count < 1:
        raise ValueError("Gaussian capacity cell count must be positive")
    if not 0.0 <= partition_overlap < 1.0 or not math.isfinite(
        partition_overlap
    ):
        raise ValueError("Gaussian partition overlap must be in [0, 1)")
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

    buffer_capacity_factor = 1.0
    required_cell_count = 1
    resident_cap = requested_cap
    if mode == "fixed":
        effective_scene = requested_cap
        effective_cell = _round_up(effective_scene / cell_count)
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
        if resident_partitioning:
            effective_scene = desired
            if memory_cap is not None:
                resident_cap = min(resident_cap, memory_cap)
            resident_cap = max(CAPACITY_QUANTUM, _round_down(resident_cap))
            buffer_capacity_factor = (1.0 + 2.0 * partition_overlap) ** 2
            required_cell_count = max(
                1,
                math.ceil(
                    effective_scene
                    * buffer_capacity_factor
                    / resident_cap
                ),
            )
            effective_cell = min(
                resident_cap,
                _round_up(
                    effective_scene
                    * buffer_capacity_factor
                    / cell_count
                ),
            )
        else:
            effective_scene = min(requested_cap, desired)
            if memory_cap is not None:
                effective_scene = min(effective_scene, memory_cap)
            effective_scene = max(
                CAPACITY_QUANTUM,
                _round_down(effective_scene),
            )
            resident_cap = effective_scene
            effective_cell = _round_up(effective_scene / cell_count)
    cells_sufficient = cell_count >= required_cell_count
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
        resident_cap=resident_cap,
        partition_overlap=partition_overlap,
        buffer_capacity_factor=buffer_capacity_factor,
        required_cell_count=required_cell_count,
        cells_sufficient=cells_sufficient,
        effective_scene_cap=effective_scene,
        effective_cell_cap=effective_cell,
        cell_count=cell_count,
        estimated_capacity_bytes=effective_cell * GAUSSIAN_CAPACITY_BYTES,
    )


def assess_gaussian_density(
    plan: GaussianCapacityPlan,
    *,
    actual_gaussian_count: int,
) -> GaussianDensityAssessment:
    """Compare achieved adaptive density with the requested raster sampling."""

    if plan.mode != "adaptive":
        raise ValueError("density assessment requires an adaptive capacity plan")
    area = plan.robust_ground_area_m2
    if area is None or area <= 0 or not math.isfinite(area):
        raise ValueError("adaptive density assessment requires a valid ground area")
    if plan.target_spacing_pixels <= 0 or not math.isfinite(
        plan.target_spacing_pixels
    ):
        raise ValueError("adaptive density assessment requires target spacing")
    if actual_gaussian_count <= 0:
        raise ValueError("actual Gaussian count must be positive")

    achieved_spacing_m = math.sqrt(area / actual_gaussian_count)
    achieved_spacing_pixels = achieved_spacing_m / plan.requested_gsd_m
    required_gaussian_count = math.ceil(
        area
        / (plan.requested_gsd_m * plan.target_spacing_pixels) ** 2
    )
    minimum_compatible_gsd_m = (
        achieved_spacing_m / plan.target_spacing_pixels
    )
    return GaussianDensityAssessment(
        robust_ground_area_m2=area,
        requested_gsd_m=plan.requested_gsd_m,
        target_spacing_pixels=plan.target_spacing_pixels,
        actual_gaussian_count=actual_gaussian_count,
        required_gaussian_count=required_gaussian_count,
        achieved_spacing_m=achieved_spacing_m,
        achieved_spacing_pixels=achieved_spacing_pixels,
        minimum_compatible_gsd_m=minimum_compatible_gsd_m,
        accepted=actual_gaussian_count >= required_gaussian_count,
    )


def _stored_int(
    payload: dict[str, Any],
    name: str,
    *,
    optional: bool = False,
) -> int | None:
    value = payload.get(name)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored Gaussian capacity integer is invalid: {name}")
    return value


def _stored_float(
    payload: dict[str, Any],
    name: str,
    *,
    optional: bool = False,
) -> float | None:
    value = payload.get(name)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"stored Gaussian capacity number is invalid: {name}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"stored Gaussian capacity number is not finite: {name}")
    return result


def _stored_bool(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"stored Gaussian capacity boolean is invalid: {name}")
    return value


def capacity_plan_from_dict(payload: object) -> GaussianCapacityPlan:
    """Validate and hydrate a capacity plan crossing a Stage Job boundary."""

    if not isinstance(payload, dict):
        raise ValueError("stored Gaussian capacity plan is invalid")
    mode = payload.get("mode")
    if mode not in {"fixed", "adaptive"}:
        raise ValueError("stored Gaussian capacity mode is invalid")
    return GaussianCapacityPlan(
        mode=mode,
        requested_cap=cast(int, _stored_int(payload, "requested_cap")),
        capacity_floor=cast(int, _stored_int(payload, "capacity_floor")),
        target_spacing_pixels=cast(
            float, _stored_float(payload, "target_spacing_pixels")
        ),
        robust_ground_area_m2=_stored_float(
            payload, "robust_ground_area_m2", optional=True
        ),
        requested_gsd_m=cast(float, _stored_float(payload, "requested_gsd_m")),
        target_output_pixels=_stored_int(
            payload, "target_output_pixels", optional=True
        ),
        surface_target=_stored_int(payload, "surface_target", optional=True),
        free_vram_bytes=_stored_int(payload, "free_vram_bytes", optional=True),
        total_vram_bytes=_stored_int(payload, "total_vram_bytes", optional=True),
        vram_cap=_stored_int(payload, "vram_cap", optional=True),
        resident_cap=cast(int, _stored_int(payload, "resident_cap")),
        partition_overlap=cast(
            float, _stored_float(payload, "partition_overlap")
        ),
        buffer_capacity_factor=cast(
            float, _stored_float(payload, "buffer_capacity_factor")
        ),
        required_cell_count=cast(
            int, _stored_int(payload, "required_cell_count")
        ),
        cells_sufficient=_stored_bool(payload, "cells_sufficient"),
        effective_scene_cap=cast(
            int, _stored_int(payload, "effective_scene_cap")
        ),
        effective_cell_cap=cast(
            int, _stored_int(payload, "effective_cell_cap")
        ),
        cell_count=cast(int, _stored_int(payload, "cell_count")),
        estimated_capacity_bytes=cast(
            int,
            _stored_int(payload, "estimated_capacity_bytes")
        ),
    )


def density_assessment_from_dict(payload: object) -> GaussianDensityAssessment:
    """Validate and hydrate post-filter density evidence."""

    if not isinstance(payload, dict):
        raise ValueError("stored Gaussian density assessment is invalid")
    accepted = payload.get("accepted")
    if not isinstance(accepted, bool):
        raise ValueError("stored Gaussian density verdict is invalid")
    return GaussianDensityAssessment(
        robust_ground_area_m2=cast(
            float,
            _stored_float(payload, "robust_ground_area_m2")
        ),
        requested_gsd_m=cast(float, _stored_float(payload, "requested_gsd_m")),
        target_spacing_pixels=cast(
            float,
            _stored_float(payload, "target_spacing_pixels")
        ),
        actual_gaussian_count=cast(
            int,
            _stored_int(payload, "actual_gaussian_count")
        ),
        required_gaussian_count=cast(
            int,
            _stored_int(payload, "required_gaussian_count")
        ),
        achieved_spacing_m=cast(
            float,
            _stored_float(payload, "achieved_spacing_m")
        ),
        achieved_spacing_pixels=cast(
            float,
            _stored_float(payload, "achieved_spacing_pixels")
        ),
        minimum_compatible_gsd_m=cast(
            float,
            _stored_float(payload, "minimum_compatible_gsd_m")
        ),
        accepted=accepted,
    )
