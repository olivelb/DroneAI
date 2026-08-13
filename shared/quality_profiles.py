"""Versioned end-to-end quality profiles shared by API, UI and workers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

QualityProfileId = Literal[
    "fast-v1",
    "fast-v2",
    "normal-v1",
    "high-quality-v1",
    "normal-v2",
    "high-quality-v2",
    "normal-v3",
    "normal-v4",
    "high-quality-v3",
    "high-quality-v4",
]
DEFAULT_QUALITY_PROFILE_ID: QualityProfileId = "normal-v3"
QUALITY_PROFILE_CANDIDATES_FLAG = "DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED"


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


_LEGACY_QUALITY_PROFILES: tuple[QualityProfile, ...] = (
    _profile(
        "normal-v1",
        "Normal (legacy)",
        "Original fixed-cap routine profile retained for mission replay.",
        image_size=2_400,
        features=4_096,
        iterations=15_000,
        gaussians=3_000_000,
        data_factor="4",
    ),
    _profile(
        "high-quality-v1",
        "High Quality (legacy)",
        "Original fixed-cap high-quality profile retained for mission replay.",
        image_size=4_096,
        features=16_384,
        iterations=30_000,
        gaussians=5_000_000,
        data_factor="1",
    ),
    _profile(
        "normal-v2",
        "Normal (legacy)",
        "Adaptive monolithic profile retained for mission replay.",
        image_size=2_400,
        features=4_096,
        iterations=15_000,
        gaussians=8_000_000,
        data_factor="4",
        capacity_mode="adaptive",
        capacity_floor=3_000_000,
        target_spacing_pixels=16.0,
    ),
    _profile(
        "high-quality-v2",
        "High Quality (legacy)",
        "Qualified 12 M profile retained only for exact mission replay.",
        image_size=4_096,
        features=16_384,
        iterations=30_000,
        gaussians=12_000_000,
        data_factor="1",
        capacity_mode="adaptive",
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
    ),
    _profile(
        "high-quality-v3",
        "High Quality (legacy resident candidate)",
        "Historical 12 M resident candidate retained only for exact mission replay.",
        image_size=4_096,
        features=16_384,
        iterations=30_000,
        gaussians=12_000_000,
        data_factor="1",
        capacity_mode="adaptive",
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        resident_partitioning=True,
    ),
)


QUALITY_PROFILES: tuple[QualityProfile, ...] = (
    _profile(
        "fast-v1",
        "Fast",
        "Fast coverage and pipeline validation with a bounded compute budget.",
        image_size=1_600,
        features=2_048,
        iterations=7_500,
        gaussians=1_500_000,
        data_factor="8",
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
)

_CANDIDATE_QUALITY_PROFILES: tuple[QualityProfile, ...] = (
    _profile(
        "fast-v2",
        "Fast (projected candidate)",
        "Preview candidate with crop-aware projected initialization and exact capacity growth.",
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
        "normal-v4",
        "Normal (projected candidate)",
        "8 GiB candidate with 3 M resident blocks and crop-aware projected initialization.",
        image_size=2_400,
        features=4_096,
        iterations=15_000,
        gaussians=3_000_000,
        data_factor="4",
        capacity_mode="adaptive",
        capacity_floor=3_000_000,
        target_spacing_pixels=8.0,
        resident_partitioning=True,
        initial_scale_policy="projected-knn",
        initial_max_projected_sigma_pixels=8.0,
        capacity_targeted_growth=True,
    ),
    _profile(
        "high-quality-v4",
        "High Quality (projected candidate)",
        "Unqualified 30k candidate with 6 M resident blocks and projected initialization.",
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
        for profile in (
            *_LEGACY_QUALITY_PROFILES,
            *QUALITY_PROFILES,
            *_CANDIDATE_QUALITY_PROFILES,
        )
    }
)


def quality_profile(profile_id: str) -> QualityProfile:
    try:
        return QUALITY_PROFILE_BY_ID[cast(QualityProfileId, profile_id)]
    except KeyError as error:
        supported = ", ".join(QUALITY_PROFILE_BY_ID)
        raise ValueError(f"unknown quality profile {profile_id!r}; expected one of: {supported}") from error


def selectable_quality_profiles(*, include_candidates: bool = False) -> tuple[QualityProfile, ...]:
    """Return profiles offered for new missions in the operator catalog."""

    if include_candidates:
        return (*QUALITY_PROFILES, *_CANDIDATE_QUALITY_PROFILES)
    return QUALITY_PROFILES


def quality_profile_candidates_enabled() -> bool:
    """Resolve the strict opt-in for candidate catalog exposure."""

    raw = os.getenv(QUALITY_PROFILE_CANDIDATES_FLAG, "false").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError(f"{QUALITY_PROFILE_CANDIDATES_FLAG} must be true or false")
    return raw == "true"


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
