"""Pure version-one mission stage contract shared by API and workers."""

from __future__ import annotations

from typing import Any, Literal

StageId = Literal[
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
]

STAGE_DAG_VERSION = 1
STAGE_ORDER: tuple[StageId, ...] = (
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
)
STAGE_DEPENDENCIES: dict[StageId, tuple[StageId, ...]] = {
    "reconstruction": (),
    "gaussian_training": ("reconstruction",),
    "gaussian_filtering": ("gaussian_training",),
    "rasterization": ("gaussian_filtering",),
    "detection": ("rasterization",),
}


def stage_dag_catalog() -> dict[str, Any]:
    return {
        "version": STAGE_DAG_VERSION,
        "stages": [
            {
                "id": stage,
                "dependencies": list(STAGE_DEPENDENCIES[stage]),
            }
            for stage in STAGE_ORDER
        ],
    }


def ordered_stages(stages: list[StageId]) -> list[StageId]:
    if len(set(stages)) != len(stages):
        raise ValueError("Mission phases must not contain duplicates")
    selected = set(stages)
    if not selected:
        raise ValueError("At least one mission phase must be selected")
    return [stage for stage in STAGE_ORDER if stage in selected]


def validate_stage_selection(
    stages: list[StageId],
    upstream_artifact_ids: dict[StageId, str],
) -> list[StageId]:
    ordered = ordered_stages(stages)
    selected = set(ordered)
    for stage in ordered:
        missing = [
            dependency
            for dependency in STAGE_DEPENDENCIES[stage]
            if dependency not in selected and dependency not in upstream_artifact_ids
        ]
        if missing:
            raise ValueError(
                f"Stage {stage} requires selected phase or exact artifact for: "
                + ", ".join(missing)
            )
    return ordered
