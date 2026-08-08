"""Quality gates for post-training Gaussian cleanup."""

from __future__ import annotations


def require_minimum_filter_retention(
    initial_count: int,
    retained_count: int,
    minimum_ratio: float,
) -> float:
    """Return the retained fraction or reject destructive cleanup.

    The view-space DroneGS canary validates novel-view appearance before the
    map-space filters run. A separate retention gate prevents an overly small
    absolute scale threshold from producing a sparse, washed-out orthomosaic
    after an otherwise successful training run.
    """

    if initial_count <= 0:
        raise ValueError("Gaussian filtering requires a non-empty input model")
    if retained_count < 0 or retained_count > initial_count:
        raise ValueError("retained Gaussian count must be within the input model")
    if not 0.0 <= minimum_ratio <= 1.0:
        raise ValueError("minimum Gaussian filter retention ratio must be in [0, 1]")

    retained_ratio = retained_count / initial_count
    if retained_ratio < minimum_ratio:
        raise ValueError(
            "Gaussian filtering retained "
            f"{retained_count}/{initial_count} primitives ({retained_ratio:.1%}), "
            f"below the required {minimum_ratio:.1%}; increase "
            "gs_filter_max_scale or lower gs_filter_min_retained_ratio explicitly"
        )
    return retained_ratio
