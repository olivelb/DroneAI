"""Versioned end-to-end quality profiles shared by API, UI and workers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from shared.dronegs_profile import checkpoint_interval_for_iterations

QualityProfileId = Literal[
    "fast-v2",
    "normal-v3",
    "high-quality-v4",
]
DEFAULT_QUALITY_PROFILE_ID: QualityProfileId = "normal-v3"
IMMUTABLE_PROFILE_OVERRIDE_KEYS = frozenset({"gs_production_profile"})
PROFILE_INITIALIZATION_KEYS = frozenset(
    {
        "gs_initial_scale_policy",
        "gs_initial_max_projected_sigma_pixels",
        "gs_capacity_targeted_growth",
    }
)


@dataclass(frozen=True)
class QualityProfile:
    """An immutable, user-facing envelope for one complete mission."""

    profile_id: QualityProfileId
    name: str
    description: str
    parameters: MappingProxyType[str, Any]

    @property
    def version(self) -> int:
        return int(self.profile_id.rsplit("-v", 1)[1])

    def as_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
        }


def _profile(
    profile_id: QualityProfileId,
    name: str,
    description: str,
    *,
    image_size: int,
    features: int,
    iterations: int,
    gaussians: int,
    data_factor: str,
    capacity_mode: str = "fixed",
    capacity_floor: int | None = None,
    target_spacing_pixels: float = 0.0,
    resident_partitioning: bool = False,
    initial_scale_policy: str = "local-knn",
    initial_max_projected_sigma_pixels: float = 2.0,
    maximum_scale_growth_factor: float = 54.59815,
    capacity_targeted_growth: bool = False,
) -> QualityProfile:
    return QualityProfile(
        profile_id=profile_id,
        name=name,
        description=description,
        parameters=MappingProxyType(
            {
                "feature_max_image_size": str(image_size),
                "feature_max_num_features": str(features),
                "mvs_max_image_size": str(image_size),
                "gs_iterations": str(iterations),
                "gs_checkpoint_every": str(checkpoint_interval_for_iterations(iterations)),
                "gs_data_factor": data_factor,
                "gs_max_width": str(image_size),
                "gs_cap_max": str(gaussians),
                "gs_capacity_mode": capacity_mode,
                "gs_capacity_floor": str(capacity_floor or gaussians),
                "gs_target_gaussian_spacing_pixels": str(target_spacing_pixels),
                "gs_resident_partitioning": resident_partitioning,
                "gs_initial_scale_policy": initial_scale_policy,
                "gs_initial_max_projected_sigma_pixels": str(initial_max_projected_sigma_pixels),
                "gs_maximum_scale_growth_factor": str(maximum_scale_growth_factor),
                "gs_capacity_targeted_growth": capacity_targeted_growth,
                "gs_production_profile": profile_id,
            }
        ),
    )


QUALITY_PROFILES: tuple[QualityProfile, ...] = (
    _profile(
        "fast-v2",
        "Fast",
        "Qualified projected initialization with exact capacity growth.",
        image_size=1_600,
        features=2_048,
        iterations=7_500,
        gaussians=1_500_000,
        data_factor="8",
        initial_scale_policy="projected-knn",
        initial_max_projected_sigma_pixels=8.0,
        capacity_targeted_growth=True,
    ),
    _profile(
        "normal-v3",
        "Normal",
        "Qualified 8 GiB profile using geographic resident buffers and streamed cores.",
        image_size=2_400,
        features=4_096,
        iterations=15_000,
        gaussians=8_000_000,
        data_factor="4",
        capacity_mode="adaptive",
        capacity_floor=3_000_000,
        target_spacing_pixels=8.0,
        resident_partitioning=True,
    ),
    _profile(
        "high-quality-v4",
        "High Quality",
        "Qualified 30k profile with 6 M resident blocks and projected initialization.",
        image_size=4_096,
        features=16_384,
        iterations=30_000,
        gaussians=6_000_000,
        data_factor="1",
        capacity_mode="adaptive",
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        resident_partitioning=True,
        initial_scale_policy="projected-knn",
        initial_max_projected_sigma_pixels=8.0,
        capacity_targeted_growth=True,
    ),
)
QUALITY_PROFILE_BY_ID = MappingProxyType(
    {
        profile.profile_id: profile
        for profile in QUALITY_PROFILES
    }
)


def quality_profile(profile_id: str) -> QualityProfile:
    try:
        return QUALITY_PROFILE_BY_ID[cast(QualityProfileId, profile_id)]
    except KeyError as error:
        supported = ", ".join(QUALITY_PROFILE_BY_ID)
        raise ValueError(f"unknown quality profile {profile_id!r}; expected one of: {supported}") from error


def quality_profile_for_new_mission(profile_id: str) -> QualityProfile:
    """Resolve a supported production profile; retired runs are not replayable."""
    return quality_profile(profile_id)


def selectable_quality_profiles() -> tuple[QualityProfile, ...]:
    """Return the qualified profiles offered for new missions."""
    return QUALITY_PROFILES


def profile_overrides(
    profile_id: str,
    effective_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Return only effective profile-envelope values changed by the operator."""

    profile = quality_profile(profile_id)
    return {
        key: effective_parameters[key]
        for key, expected in profile.parameters.items()
        if key in effective_parameters and str(effective_parameters[key]) != str(expected)
    }


def profile_overrides_for_new_mission(
    profile_id: str,
    effective_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Validate expert overrides against create-time profile exposure policy."""

    overrides = profile_overrides(profile_id, effective_parameters)
    immutable = sorted(IMMUTABLE_PROFILE_OVERRIDE_KEYS.intersection(overrides))
    if immutable:
        raise ValueError("new missions cannot override immutable quality-profile identity: " + ", ".join(immutable))
    qualification_only = sorted(PROFILE_INITIALIZATION_KEYS.intersection(overrides))
    if qualification_only:
        raise ValueError(
            "qualified profile initialization cannot be overridden: " + ", ".join(qualification_only)
        )
    return overrides
