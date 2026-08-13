"""Typed resolution of DroneGS training and qualification parameters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
)
from shared.facade_process import (
    FACADE_DRONEGS_IDENTITY_PARAMETERS,
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_LEGACY_DRONEGS_IDENTITY_PARAMETERS,
    FACADE_LEGACY_DRONEGS_PROFILE_ID,
    FACADE_PROCESS_OVERRIDES,
    FACADE_QUALIFICATION_POLICY_ID,
    FACADE_QUALIFICATION_THRESHOLDS,
)
from shared.quality_profiles import QUALITY_PROFILE_BY_ID, QualityProfileId
from gaussian_ortho.coverage_quality import SpatialCoveragePolicy


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
    sh_degree_interval: int
    topology_cooldown: int
    photometric_finish: int
    photometric_mse_percent: int
    checkpoint_every: int
    test_every: int
    test_split: str
    test_guard_percent: int
    canary_min_psnr: float
    canary_min_ssim: float
    max_width: int
    tile_mode: int
    mip_filter_variance: float
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
        "sh_degree_interval": config.sh_degree_interval,
        "topology_cooldown": config.topology_cooldown,
        "photometric_finish": config.photometric_finish,
        "photometric_mse_percent": config.photometric_mse_percent,
        "checkpoint_every": config.checkpoint_every,
        "test_every": config.test_every,
        "test_split": config.test_split,
        "test_guard_percent": config.test_guard_percent,
    }


def _expected_profile_identity(profile_id: str, fields: Mapping[str, Any]) -> dict[str, Any] | None:
    if profile_id == FACADE_DRONEGS_PROFILE_ID:
        expected = dict(FACADE_DRONEGS_IDENTITY_PARAMETERS)
        expected.update(
            {
                "capacity_mode": str(FACADE_PROCESS_OVERRIDES["gs_capacity_mode"]),
                "capacity_floor": int(FACADE_PROCESS_OVERRIDES["gs_capacity_floor"]),
                "target_gaussian_spacing_pixels": float(
                    FACADE_PROCESS_OVERRIDES["gs_target_gaussian_spacing_pixels"]
                ),
                "resident_partitioning": bool(
                    FACADE_PROCESS_OVERRIDES["gs_resident_partitioning"]
                ),
            }
        )
        return expected
    if profile_id == FACADE_LEGACY_DRONEGS_PROFILE_ID:
        expected = dict(FACADE_LEGACY_DRONEGS_IDENTITY_PARAMETERS)
        expected.update(
            {
                "capacity_mode": "fixed",
                "capacity_floor": int(expected["cap_max"]),
                "target_gaussian_spacing_pixels": 0.0,
                "resident_partitioning": False,
            }
        )
        return expected
    if profile_id == DRONEGS_PRODUCTION_PROFILE_V1.profile_id:
        expected = {
            name: getattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
            for name in fields
            if hasattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
        }
        expected.update(
            {
                "capacity_mode": "fixed",
                "capacity_floor": DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
                "target_gaussian_spacing_pixels": 0.0,
                "resident_partitioning": False,
            }
        )
        return expected
    if profile_id in QUALITY_PROFILE_BY_ID:
        expected = {
            name: getattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
            for name in fields
            if hasattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
        }
        parameters = QUALITY_PROFILE_BY_ID[
            cast(QualityProfileId, profile_id)
        ].parameters
        expected.update(
            {
                "iterations": int(parameters["gs_iterations"]),
                "data_factor": int(parameters["gs_data_factor"]),
                "max_width": int(parameters["gs_max_width"]),
                "cap_max": int(parameters["gs_cap_max"]),
                "capacity_mode": str(parameters["gs_capacity_mode"]),
                "capacity_floor": int(parameters["gs_capacity_floor"]),
                "target_gaussian_spacing_pixels": float(
                    parameters["gs_target_gaussian_spacing_pixels"]
                ),
                "resident_partitioning": bool(
                    parameters["gs_resident_partitioning"]
                ),
            }
        )
        return expected
    return None


def _qualification_identity(policy_id: str) -> dict[str, float] | None:
    if policy_id == DRONEGS_QUALIFICATION_POLICY_ID:
        return {
            "canary_min_psnr": DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr,
            "canary_min_ssim": DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim,
        }
    if policy_id == FACADE_QUALIFICATION_POLICY_ID:
        return dict(FACADE_QUALIFICATION_THRESHOLDS)
    return None


def resolve_dronegs_config(
    params: Mapping[str, Any],
    *,
    facade_mode: bool,
    data_factor: int,
) -> tuple[DroneGsRunConfig, tuple[str, ...]]:
    profile_id = str(
        params.get("gs_production_profile", DRONEGS_PRODUCTION_PROFILE_V1.profile_id)
    )
    selected_profile = QUALITY_PROFILE_BY_ID.get(cast(QualityProfileId, profile_id))
    selected_parameters = selected_profile.parameters if selected_profile else {}
    cap_max = int(params.get("gs_cap_max", DRONEGS_PRODUCTION_PROFILE_V1.cap_max))
    qualification_policy_id = str(
        params.get("gs_qualification_policy", DRONEGS_QUALIFICATION_POLICY_ID)
    )
    coverage_policy = SpatialCoveragePolicy()
    config = DroneGsRunConfig(
        resolution=float(params.get("ortho_mesh_resolution", 0.02)),
        data_factor=data_factor,
        iterations=int(params.get("gs_iterations", DRONEGS_PRODUCTION_PROFILE_V1.iterations)),
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
        optimizer_profile=str(
            params.get("gs_optimizer_profile", DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile)
        ),
        pruning_policy=str(
            params.get("gs_pruning_policy", DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy)
        ),
        raster_profile=str(
            params.get("gs_raster_profile", DRONEGS_PRODUCTION_PROFILE_V1.raster_profile)
        ),
        sh_degree_interval=int(params.get("gs_sh_degree_interval", 1_000)),
        topology_cooldown=int(params.get("gs_topology_cooldown", 1_000)),
        photometric_finish=int(params.get("gs_photometric_finish", 1_000)),
        photometric_mse_percent=int(params.get("gs_photometric_mse_percent", 100)),
        checkpoint_every=int(params.get("gs_checkpoint_every", 2_000)),
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
        tile_mode=int(params.get("gs_tile_mode", DRONEGS_PRODUCTION_PROFILE_V1.tile_mode)),
        mip_filter_variance=float(params.get("gs_ortho_mip_filter_variance", 0.03)),
        mip_filter_compensation=bool(params.get("gs_ortho_mip_filter_compensation", True)),
        filter_enabled=bool(params.get("gs_filter_enabled", True)),
        filter_max_scale=float(
            params.get(
                "facade_filter_max_scale" if facade_mode else "gs_filter_max_scale",
                1.0 if facade_mode else 5.0,
            )
        ),
        filter_min_retained_ratio=(
            0.0
            if facade_mode
            else float(params.get("gs_filter_min_retained_ratio", 0.80))
        ),
        filter_dist=float(params.get("gs_filter_dist", 1.0)),
        filter_opacity=float(params.get("gs_filter_opacity", 0.005)),
        filter_needle=float(params.get("gs_filter_needle", 0.0)),
        filter_sor=bool(params.get("gs_filter_sor", False)),
        filter_sor_sigma=float(params.get("gs_filter_sor_sigma", 4.0)),
        filter_cc=bool(params.get("gs_filter_cc", False)),
        filter_z_floater=bool(params.get("gs_filter_z_floater", False)),
        coverage_gate_enabled=(
            False
            if facade_mode
            else bool(params.get("gs_coverage_gate_enabled", True))
        ),
        coverage_grid_size=int(
            params.get("gs_coverage_grid_size", coverage_policy.grid_size)
        ),
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
    if not 1 <= config.capacity_floor <= config.cap_max:
        raise ValueError("gs_capacity_floor must be positive and no greater than gs_cap_max")
    if config.capacity_mode == "adaptive" and config.target_gaussian_spacing_pixels <= 0:
        raise ValueError(
            "adaptive Gaussian capacity requires a positive target pixel spacing"
        )

    warnings: list[str] = []
    profile_identity = _profile_identity(config)
    expected_profile = _expected_profile_identity(config.profile_id, profile_identity)
    if expected_profile is not None and profile_identity != expected_profile:
        config = replace(config, profile_id="custom")
        warnings.append(
            "DroneGS expert overrides detected; the run is recorded as custom instead of its named profile."
        )

    expected_qualification = _qualification_identity(config.qualification_policy_id)
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
