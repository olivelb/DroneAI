"""Typed resolution of DroneGS training and qualification parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
    bounded_checkpoint_interval,
    checkpoint_interval_for_iterations,
)
from shared.quality_profiles import QUALITY_PROFILE_BY_ID, QualityProfileId
from gaussian_ortho.coverage_quality import SpatialCoveragePolicy

from .dronegs_identity import expected_profile_identity, qualification_identity


@dataclass(frozen=True)
class DroneGsRunConfig:
    resolution: float
    data_factor: int
    iterations: int
    cap_max: int
    capacity_mode: str
    capacity_floor: int
    target_gaussian_spacing_pixels: float
    resident_partitioning: bool
    sh_degree: int
    backend: str
    seed: int
    profile_id: str
    qualification_policy_id: str
    optimizer_profile: str
    pruning_policy: str
    raster_profile: str
    initial_scale_policy: str
    initial_max_projected_sigma_pixels: float
    maximum_scale_growth_factor: float
    capacity_targeted_growth: bool
    sh_degree_interval: int
    topology_cooldown: int
    photometric_finish: int
    photometric_mse_percent: int
    checkpoint_every: int
    host_image_cache_mib: int
    test_every: int
    test_split: str
    test_guard_percent: int
    canary_min_psnr: float
    canary_min_ssim: float
    max_width: int
    tile_mode: int
    mip_filter_variance: float
    tile_mode_auto: bool
    mip_filter_compensation: bool
    filter_enabled: bool
    filter_max_scale: float
    filter_min_retained_ratio: float
    filter_dist: float
    filter_opacity: float
    filter_needle: float
    filter_sor: bool
    filter_sor_sigma: float
    filter_cc: bool
    filter_z_floater: bool
    coverage_gate_enabled: bool
    coverage_grid_size: int
    coverage_min_valid_ratio: float
    coverage_cell_threshold: float
    coverage_min_covered_cells_ratio: float
    coverage_min_worst_cell_ratio: float
    coverage_min_camera_cell_ratio: float


def _boolean_parameter(value: object, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{name} must be a boolean")


def _profile_identity(config: DroneGsRunConfig) -> dict[str, Any]:
    return {
        "iterations": config.iterations,
        "data_factor": config.data_factor,
        "max_width": config.max_width,
        "tile_mode": config.tile_mode,
        "cap_max": config.cap_max,
        "capacity_mode": config.capacity_mode,
        "capacity_floor": config.capacity_floor,
        "target_gaussian_spacing_pixels": config.target_gaussian_spacing_pixels,
        "resident_partitioning": config.resident_partitioning,
        "sh_degree": config.sh_degree,
        "seed": config.seed,
        "optimizer_profile": config.optimizer_profile,
        "pruning_policy": config.pruning_policy,
        "raster_profile": config.raster_profile,
        "initial_scale_policy": config.initial_scale_policy,
        "initial_max_projected_sigma_pixels": (config.initial_max_projected_sigma_pixels),
        "maximum_scale_growth_factor": config.maximum_scale_growth_factor,
        "capacity_targeted_growth": config.capacity_targeted_growth,
        "sh_degree_interval": config.sh_degree_interval,
        "topology_cooldown": config.topology_cooldown,
        "photometric_finish": config.photometric_finish,
        "photometric_mse_percent": config.photometric_mse_percent,
        "test_every": config.test_every,
        "test_split": config.test_split,
        "test_guard_percent": config.test_guard_percent,
    }


def resolve_dronegs_config(
    params: Mapping[str, Any],
    *,
    facade_mode: bool,
    data_factor: int,
) -> tuple[DroneGsRunConfig, tuple[str, ...]]:
    profile_id = str(params.get("gs_production_profile", DRONEGS_PRODUCTION_PROFILE_V1.profile_id))
    selected_profile = QUALITY_PROFILE_BY_ID.get(cast(QualityProfileId, profile_id))
    selected_parameters = selected_profile.parameters if selected_profile else {}
    iterations = int(params.get("gs_iterations", DRONEGS_PRODUCTION_PROFILE_V1.iterations))
    cap_max = int(params.get("gs_cap_max", DRONEGS_PRODUCTION_PROFILE_V1.cap_max))
    requested_checkpoint_every = int(
        params.get(
            "gs_checkpoint_every",
            checkpoint_interval_for_iterations(iterations),
        )
    )
    checkpoint_every = bounded_checkpoint_interval(
        iterations,
        requested_checkpoint_every,
    )
    raw_tile_mode = params.get("gs_tile_mode", "auto")
    tile_mode_auto = isinstance(raw_tile_mode, str) and raw_tile_mode.strip().lower() == "auto"
    if tile_mode_auto:
        tile_mode = DRONEGS_PRODUCTION_PROFILE_V1.tile_mode
    else:
        try:
            tile_mode = int(raw_tile_mode)
        except (TypeError, ValueError) as error:
            raise ValueError("gs_tile_mode must be auto, 1, 2, or 4") from error
        if tile_mode not in {1, 2, 4}:
            raise ValueError("gs_tile_mode must be auto, 1, 2, or 4")
    qualification_policy_id = str(params.get("gs_qualification_policy", DRONEGS_QUALIFICATION_POLICY_ID))
    coverage_policy = SpatialCoveragePolicy()
    config = DroneGsRunConfig(
        resolution=float(params.get("ortho_mesh_resolution", 0.02)),
        data_factor=data_factor,
        iterations=iterations,
        cap_max=cap_max,
        capacity_mode=str(
            params.get(
                "gs_capacity_mode",
                selected_parameters.get("gs_capacity_mode", "fixed"),
            )
        ),
        capacity_floor=int(
            params.get(
                "gs_capacity_floor",
                selected_parameters.get("gs_capacity_floor", cap_max),
            )
        ),
        target_gaussian_spacing_pixels=float(
            params.get(
                "gs_target_gaussian_spacing_pixels",
                selected_parameters.get("gs_target_gaussian_spacing_pixels", 0.0),
            )
        ),
        resident_partitioning=_boolean_parameter(
            params.get(
                "gs_resident_partitioning",
                selected_parameters.get("gs_resident_partitioning", False),
            ),
            name="gs_resident_partitioning",
        ),
        sh_degree=int(params.get("gs_sh_degree", DRONEGS_PRODUCTION_PROFILE_V1.sh_degree)),
        backend=str(params.get("gs_backend", "dronegs")),
        seed=int(params.get("gs_seed", 42)),
        profile_id=profile_id,
        qualification_policy_id=qualification_policy_id,
        optimizer_profile=str(params.get("gs_optimizer_profile", DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile)),
        pruning_policy=str(params.get("gs_pruning_policy", DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy)),
        raster_profile=str(params.get("gs_raster_profile", DRONEGS_PRODUCTION_PROFILE_V1.raster_profile)),
        initial_scale_policy=str(
            params.get(
                "gs_initial_scale_policy",
                selected_parameters.get("gs_initial_scale_policy", "local-knn"),
            )
        ),
        initial_max_projected_sigma_pixels=float(
            params.get(
                "gs_initial_max_projected_sigma_pixels",
                selected_parameters.get("gs_initial_max_projected_sigma_pixels", 2.0),
            )
        ),
        maximum_scale_growth_factor=float(
            params.get(
                "gs_maximum_scale_growth_factor",
                selected_parameters.get("gs_maximum_scale_growth_factor", 54.59815),
            )
        ),
        capacity_targeted_growth=_boolean_parameter(
            params.get("gs_capacity_targeted_growth", False),
            name="gs_capacity_targeted_growth",
        ),
        sh_degree_interval=int(params.get("gs_sh_degree_interval", 1_000)),
        topology_cooldown=int(params.get("gs_topology_cooldown", 1_000)),
        photometric_finish=int(params.get("gs_photometric_finish", 1_000)),
        photometric_mse_percent=int(params.get("gs_photometric_mse_percent", 100)),
        checkpoint_every=checkpoint_every,
        host_image_cache_mib=int(params.get("gs_host_image_cache_mib", 0)),
        test_every=int(params.get("gs_test_every", 8)),
        test_split=str(params.get("gs_test_split", "modulo")),
        test_guard_percent=int(params.get("gs_test_guard_percent", 0)),
        canary_min_psnr=float(
            params.get(
                "facade_canary_min_psnr" if facade_mode else "gs_canary_min_psnr",
                DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr,
            )
        ),
        canary_min_ssim=float(
            params.get(
                "facade_canary_min_ssim" if facade_mode else "gs_canary_min_ssim",
                DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim,
            )
        ),
        max_width=int(params.get("gs_max_width", DRONEGS_PRODUCTION_PROFILE_V1.max_width)),
        tile_mode=tile_mode,
        tile_mode_auto=tile_mode_auto,
        mip_filter_variance=float(params.get("gs_ortho_mip_filter_variance", 0.03)),
        mip_filter_compensation=bool(params.get("gs_ortho_mip_filter_compensation", True)),
        filter_enabled=bool(params.get("gs_filter_enabled", True)),
        filter_max_scale=float(
            params.get(
                "facade_filter_max_scale" if facade_mode else "gs_filter_max_scale",
                1.0 if facade_mode else 5.0,
            )
        ),
        filter_min_retained_ratio=(0.0 if facade_mode else float(params.get("gs_filter_min_retained_ratio", 0.80))),
        filter_dist=float(params.get("gs_filter_dist", 1.0)),
        filter_opacity=float(params.get("gs_filter_opacity", 0.005)),
        filter_needle=float(params.get("gs_filter_needle", 0.0)),
        filter_sor=bool(params.get("gs_filter_sor", False)),
        filter_sor_sigma=float(params.get("gs_filter_sor_sigma", 4.0)),
        filter_cc=bool(params.get("gs_filter_cc", False)),
        filter_z_floater=bool(params.get("gs_filter_z_floater", False)),
        coverage_gate_enabled=(False if facade_mode else bool(params.get("gs_coverage_gate_enabled", True))),
        coverage_grid_size=int(params.get("gs_coverage_grid_size", coverage_policy.grid_size)),
        coverage_min_valid_ratio=float(
            params.get(
                "gs_coverage_min_valid_ratio",
                coverage_policy.minimum_valid_ratio,
            )
        ),
        coverage_cell_threshold=float(
            params.get(
                "gs_coverage_cell_threshold",
                coverage_policy.cell_coverage_threshold,
            )
        ),
        coverage_min_covered_cells_ratio=float(
            params.get(
                "gs_coverage_min_covered_cells_ratio",
                coverage_policy.minimum_covered_cells_ratio,
            )
        ),
        coverage_min_worst_cell_ratio=float(
            params.get(
                "gs_coverage_min_worst_cell_ratio",
                coverage_policy.minimum_worst_cell_ratio,
            )
        ),
        coverage_min_camera_cell_ratio=float(
            params.get(
                "gs_coverage_min_camera_cell_ratio",
                coverage_policy.minimum_camera_cell_ratio,
            )
        ),
    )
    if config.capacity_mode not in {"fixed", "adaptive"}:
        raise ValueError("gs_capacity_mode must be fixed or adaptive")
    if config.initial_scale_policy not in {"local-knn", "projected-knn"}:
        raise ValueError("gs_initial_scale_policy must be local-knn or projected-knn")
    if not 0 < config.initial_max_projected_sigma_pixels <= 64:
        raise ValueError("gs_initial_max_projected_sigma_pixels must be in (0, 64]")
    if not 1 <= config.maximum_scale_growth_factor <= 1024:
        raise ValueError("gs_maximum_scale_growth_factor must be in [1, 1024]")
    if not 1 <= config.capacity_floor <= config.cap_max:
        raise ValueError("gs_capacity_floor must be positive and no greater than gs_cap_max")
    if config.capacity_mode == "adaptive" and config.target_gaussian_spacing_pixels <= 0:
        raise ValueError("adaptive Gaussian capacity requires a positive target pixel spacing")
    if (
        config.host_image_cache_mib != 0
        and not 256 <= config.host_image_cache_mib <= 65_536
    ):
        raise ValueError("gs_host_image_cache_mib must be 0 (auto) or between 256 and 65536")

    warnings: list[str] = []
    if checkpoint_every != requested_checkpoint_every:
        warnings.append(
            f"DroneGS checkpoint interval was raised to {checkpoint_every} iterations to respect the recovery budget."
        )
    profile_identity = _profile_identity(config)
    expected_profile = expected_profile_identity(config.profile_id, profile_identity)
    if expected_profile is not None and (profile_identity != expected_profile or not config.tile_mode_auto):
        config = replace(config, profile_id="custom")
        warnings.append(
            "DroneGS expert overrides detected; the run is recorded as custom instead of its named profile."
        )

    expected_qualification = qualification_identity(config.qualification_policy_id)
    actual_qualification = {
        "canary_min_psnr": config.canary_min_psnr,
        "canary_min_ssim": config.canary_min_ssim,
    }
    if expected_qualification is not None and actual_qualification != expected_qualification:
        config = replace(config, qualification_policy_id="custom")
        warnings.append(
            "DroneGS canary thresholds differ from qualification policy V1; training recipe "
            "identity is preserved and qualification policy is recorded as custom."
        )
    return config, tuple(warnings)
