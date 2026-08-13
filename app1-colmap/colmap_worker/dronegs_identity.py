"""Versioned DroneGS training and qualification identity contracts."""

from __future__ import annotations

from collections.abc import Mapping
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
    FACADE_PREVIOUS_DRONEGS_IDENTITY_PARAMETERS,
    FACADE_PREVIOUS_DRONEGS_PROFILE_ID,
    FACADE_PROCESS_OVERRIDES,
    FACADE_QUALIFICATION_POLICY_ID,
    FACADE_QUALIFICATION_THRESHOLDS,
)
from shared.quality_profiles import QUALITY_PROFILE_BY_ID, QualityProfileId


def expected_profile_identity(
    profile_id: str,
    fields: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve the immutable training identity declared by a profile ID."""

    if profile_id == FACADE_DRONEGS_PROFILE_ID:
        expected = dict(FACADE_DRONEGS_IDENTITY_PARAMETERS)
        expected.update(
            {
                "capacity_mode": str(FACADE_PROCESS_OVERRIDES["gs_capacity_mode"]),
                "capacity_floor": int(FACADE_PROCESS_OVERRIDES["gs_capacity_floor"]),
                "target_gaussian_spacing_pixels": float(FACADE_PROCESS_OVERRIDES["gs_target_gaussian_spacing_pixels"]),
                "resident_partitioning": bool(FACADE_PROCESS_OVERRIDES["gs_resident_partitioning"]),
                "initial_scale_policy": str(FACADE_PROCESS_OVERRIDES.get("gs_initial_scale_policy", "local-knn")),
                "initial_max_projected_sigma_pixels": float(
                    FACADE_PROCESS_OVERRIDES.get("gs_initial_max_projected_sigma_pixels", 2.0)
                ),
                "maximum_scale_growth_factor": float(
                    FACADE_PROCESS_OVERRIDES.get("gs_maximum_scale_growth_factor", 54.59815)
                ),
                "capacity_targeted_growth": bool(FACADE_PROCESS_OVERRIDES.get("gs_capacity_targeted_growth", False)),
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
                "initial_scale_policy": "local-knn",
                "initial_max_projected_sigma_pixels": 2.0,
                "maximum_scale_growth_factor": 54.59815,
                "capacity_targeted_growth": False,
            }
        )
        return expected
    if profile_id == FACADE_PREVIOUS_DRONEGS_PROFILE_ID:
        expected = dict(FACADE_PREVIOUS_DRONEGS_IDENTITY_PARAMETERS)
        expected.update(
            {
                "capacity_mode": "adaptive",
                "capacity_floor": 5_000_000,
                "target_gaussian_spacing_pixels": 3.6,
                "resident_partitioning": True,
                "initial_scale_policy": "local-knn",
                "initial_max_projected_sigma_pixels": 2.0,
                "maximum_scale_growth_factor": 54.59815,
                "capacity_targeted_growth": False,
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
                "initial_scale_policy": "local-knn",
                "initial_max_projected_sigma_pixels": 2.0,
                "maximum_scale_growth_factor": 54.59815,
                "capacity_targeted_growth": False,
            }
        )
        return expected
    if profile_id in QUALITY_PROFILE_BY_ID:
        expected = {
            name: getattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
            for name in fields
            if hasattr(DRONEGS_PRODUCTION_PROFILE_V1, name)
        }
        parameters = QUALITY_PROFILE_BY_ID[cast(QualityProfileId, profile_id)].parameters
        expected.update(
            {
                "iterations": int(parameters["gs_iterations"]),
                "data_factor": int(parameters["gs_data_factor"]),
                "max_width": int(parameters["gs_max_width"]),
                "cap_max": int(parameters["gs_cap_max"]),
                "capacity_mode": str(parameters["gs_capacity_mode"]),
                "capacity_floor": int(parameters["gs_capacity_floor"]),
                "target_gaussian_spacing_pixels": float(parameters["gs_target_gaussian_spacing_pixels"]),
                "resident_partitioning": bool(parameters["gs_resident_partitioning"]),
                "initial_scale_policy": str(parameters.get("gs_initial_scale_policy", "local-knn")),
                "initial_max_projected_sigma_pixels": float(
                    parameters.get("gs_initial_max_projected_sigma_pixels", 2.0)
                ),
                "maximum_scale_growth_factor": float(parameters.get("gs_maximum_scale_growth_factor", 54.59815)),
                "capacity_targeted_growth": bool(parameters.get("gs_capacity_targeted_growth", False)),
            }
        )
        return expected
    return None


def qualification_identity(policy_id: str) -> dict[str, float] | None:
    """Resolve acceptance thresholds independently of the training recipe."""

    if policy_id == DRONEGS_QUALIFICATION_POLICY_ID:
        return {
            "canary_min_psnr": DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr,
            "canary_min_ssim": DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim,
        }
    if policy_id == FACADE_QUALIFICATION_POLICY_ID:
        return dict(FACADE_QUALIFICATION_THRESHOLDS)
    return None
