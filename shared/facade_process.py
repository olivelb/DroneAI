"""Facade product contract and the benchmark-qualified production profile.

This module is the single source of truth shared by the worker, API and
dashboard. Generic pipeline defaults remain map-oriented; selecting the facade
process overlays this profile without preventing explicit quality overrides.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any
from collections.abc import Mapping

from shared.dronegs_profile import DRONEGS_PRODUCTION_PROFILE_V1

MAP_PROCESS_ID = "map"
FACADE_PROCESS_ID = "facade"
ORTHOPHOTO_MODES = frozenset({MAP_PROCESS_ID, FACADE_PROCESS_ID})
FACADE_PROCESS_PROFILE_ID = "FACADE_HD_V1"
FACADE_DRONEGS_PROFILE_ID = "DRONEGS_FACADE_HD_V1"
FACADE_QUALIFICATION_POLICY_ID = "FACADE_HD_QUALIFICATION_POLICY_V1"

FACADE_PARAMETER_DEFAULTS = MappingProxyType(
    {
        "facade_selection_mode": "all",
        "facade_excluded_image_ranges": "",
        "facade_max_abs_pitch_deg": "40",
        "facade_min_pass_images": "12",
        "facade_target_yaw_deg": "",
        "facade_yaw_tolerance_deg": "35",
        "facade_scale_mode": "gps-baseline",
        "facade_meters_per_model_unit": "1.0",
        "facade_texture_max_incidence_deg": "45",
        "facade_depth_iqr_multiplier": "1.0",
        "facade_seed_max_reprojection_error": "2.0",
        "facade_seed_min_track_length": "2",
        "facade_filter_max_scale": "1.0",
        "facade_canary_min_psnr": "18",
        "facade_canary_min_ssim": "0.25",
    }
)

# These invariants define a local facade product and cannot be re-enabled by a
# stale map preset. Quality parameters outside this set remain overridable.
FACADE_PROCESS_INVARIANTS = MappingProxyType(
    {
        "matching_strategy": "spatial",
        "rtk_refinement_enabled": False,
        "gcp_adjustment_enabled": False,
        "imu_gravity_enabled": False,
    }
)

# Generic HD values qualified on the Cahors reference campaign. Mission-specific
# detail sequences can be excluded to favour coverage distribution over raw
# sparse-point density; DroneGS performs the later densification.
FACADE_PROCESS_OVERRIDES: Mapping[str, Any] = MappingProxyType(
    {
        "orthophoto_mode": FACADE_PROCESS_ID,
        **FACADE_PARAMETER_DEFAULTS,
        "feature_type": "SIFT",
        "feature_max_image_size": "4200",
        "feature_max_num_features": "16384",
        "feature_max_num_matches": "16384",
        "sift_first_octave": "0",
        "matcher_type": "STANDARD",
        "guided_matching": True,
        **FACADE_PROCESS_INVARIANTS,
        "gps_pair_max_neighbors": "48",
        "gps_pair_min_neighbors": "16",
        "gps_pair_temporal_neighbors": "6",
        "camera_model": "SIMPLE_RADIAL",
        "alignment_engine": "caspar",
        "use_view_graph_calibrator": False,
        "global_mapper_ba_iterations": "2",
        "global_mapper_skip_retriangulation": False,
        "minimum_registration_ratio": "0.90",
        "mapping_timeout_seconds": "14400",
        "mvs_max_image_size": "4200",
        "mvs_num_threads": "12",
        "gs_iterations": "30000",
        "gs_data_factor": "1",
        "gs_max_width": "4096",
        "gs_tile_mode": "4",
        "gs_cap_max": "2000000",
        "gs_sh_degree": "3",
        "gs_production_profile": FACADE_DRONEGS_PROFILE_ID,
        "gs_qualification_policy": FACADE_QUALIFICATION_POLICY_ID,
        "ortho_mesh_resolution": "0.01",
    }
)

FACADE_DRONEGS_IDENTITY_PARAMETERS = MappingProxyType(
    {
        "iterations": int(FACADE_PROCESS_OVERRIDES["gs_iterations"]),
        "data_factor": int(FACADE_PROCESS_OVERRIDES["gs_data_factor"]),
        "max_width": int(FACADE_PROCESS_OVERRIDES["gs_max_width"]),
        "tile_mode": int(FACADE_PROCESS_OVERRIDES["gs_tile_mode"]),
        "cap_max": int(FACADE_PROCESS_OVERRIDES["gs_cap_max"]),
        "sh_degree": int(FACADE_PROCESS_OVERRIDES["gs_sh_degree"]),
        "seed": DRONEGS_PRODUCTION_PROFILE_V1.seed,
        "optimizer_profile": DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile,
        "pruning_policy": DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy,
        "raster_profile": DRONEGS_PRODUCTION_PROFILE_V1.raster_profile,
        "sh_degree_interval": DRONEGS_PRODUCTION_PROFILE_V1.sh_degree_interval,
        "topology_cooldown": DRONEGS_PRODUCTION_PROFILE_V1.topology_cooldown,
        "photometric_finish": DRONEGS_PRODUCTION_PROFILE_V1.photometric_finish,
        "photometric_mse_percent": (DRONEGS_PRODUCTION_PROFILE_V1.photometric_mse_percent),
        "checkpoint_every": DRONEGS_PRODUCTION_PROFILE_V1.checkpoint_every,
        "test_every": DRONEGS_PRODUCTION_PROFILE_V1.test_every,
        "test_split": DRONEGS_PRODUCTION_PROFILE_V1.test_split,
        "test_guard_percent": DRONEGS_PRODUCTION_PROFILE_V1.test_guard_percent,
    }
)

FACADE_QUALIFICATION_THRESHOLDS = MappingProxyType(
    {
        "canary_min_psnr": float(FACADE_PROCESS_OVERRIDES["facade_canary_min_psnr"]),
        "canary_min_ssim": float(FACADE_PROCESS_OVERRIDES["facade_canary_min_ssim"]),
    }
)


def normalize_orthophoto_mode(value: Any) -> str:
    """Return a supported product mode or raise a clear configuration error."""

    mode = str(value or MAP_PROCESS_ID).strip().lower()
    if mode not in ORTHOPHOTO_MODES:
        raise ValueError(f"Unsupported orthophoto_mode: {mode}")
    return mode


def explicit_parameter_names(mission_params: Mapping[str, Any] | None) -> set[str]:
    """Collect top-level and nested parameter names explicitly sent by a client."""

    mission_params = mission_params or {}
    names = set(mission_params)
    nested = mission_params.get("colmap_params")
    if isinstance(nested, Mapping):
        names.update(nested)
    return names


def apply_facade_process_profile(
    params: dict[str, Any],
    mission_params: Mapping[str, Any] | None = None,
) -> bool:
    """Apply facade defaults and local-frame invariants in place."""

    mode = normalize_orthophoto_mode(params.get("orthophoto_mode"))
    params["orthophoto_mode"] = mode
    if mode != FACADE_PROCESS_ID:
        return False

    explicit = explicit_parameter_names(mission_params)
    for name, value in FACADE_PROCESS_OVERRIDES.items():
        if name not in explicit:
            params[name] = value

    params.update(FACADE_PROCESS_INVARIANTS)
    params["minimum_registration_ratio"] = str(min(float(params.get("minimum_registration_ratio", 0.97)), 0.90))
    return True


def product_process_catalog() -> list[dict[str, Any]]:
    """Return dashboard-ready process definitions without duplicating values."""

    return [
        {
            "id": MAP_PROCESS_ID,
            "label": "Cartographie aérienne",
            "description": "Orthomosaïque géoréférencée, tuilage et détection IA.",
            "stages": ["COLMAP", "TILER", "IA"],
            "parameters": {"orthophoto_mode": MAP_PROCESS_ID},
        },
        {
            "id": FACADE_PROCESS_ID,
            "label": "Façade HD",
            "description": (
                "Orthophoto HD et profondeur en repère local, avec couverture "
                "prioritaire et sans chaîne de détection aérienne."
            ),
            "profile_id": FACADE_PROCESS_PROFILE_ID,
            "stages": ["COLMAP"],
            "parameters": dict(FACADE_PROCESS_OVERRIDES),
        },
    ]
