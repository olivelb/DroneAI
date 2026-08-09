"""Versioned end-to-end quality profiles shared by API, UI and workers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

QualityProfileId = Literal["fast-v1", "normal-v1", "high-quality-v1"]
DEFAULT_QUALITY_PROFILE_ID: QualityProfileId = "normal-v1"


@dataclass(frozen=True)
class QualityProfile:
    """An immutable, user-facing envelope for one complete mission."""

    profile_id: QualityProfileId
    name: str
    description: str
    parameters: MappingProxyType[str, Any]

    @property
    def version(self) -> int:
        return 1

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
                "gs_production_profile": profile_id,
            }
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
        "normal-v1",
        "Normal",
        "Balanced production profile for routine aerial missions.",
        image_size=2_400,
        features=4_096,
        iterations=15_000,
        gaussians=3_000_000,
        data_factor="4",
    ),
    _profile(
        "high-quality-v1",
        "High Quality",
        "Maximum-detail profile for qualified hardware and deliberate long runs.",
        image_size=4_096,
        features=16_384,
        iterations=30_000,
        gaussians=5_000_000,
        data_factor="1",
    ),
)
QUALITY_PROFILE_BY_ID = MappingProxyType(
    {profile.profile_id: profile for profile in QUALITY_PROFILES}
)


def quality_profile(profile_id: str) -> QualityProfile:
    try:
        return QUALITY_PROFILE_BY_ID[cast(QualityProfileId, profile_id)]
    except KeyError as error:
        supported = ", ".join(QUALITY_PROFILE_BY_ID)
        raise ValueError(f"unknown quality profile {profile_id!r}; expected one of: {supported}") from error


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
