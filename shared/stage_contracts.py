"""Pure version-one mission stage contract shared by API and workers."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

StageId = Literal[
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
]

ResourceClassId = Literal[
    "cpu-standard",
    "gpu-standard",
    "gpu-geometry",
    "gpu-high-memory",
]


class ResourceClassSpec(TypedDict):
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    ephemeral_storage_request: str
    ephemeral_storage_limit: str
    gpu_count: int
    minimum_vram_gib: int

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

STAGE_ARTIFACT_KINDS: dict[StageId, str] = {
    "reconstruction": "reconstruction_workspace",
    "gaussian_training": "gaussian_training_workspace",
    "gaussian_filtering": "gaussian_filtering_workspace",
    "rasterization": "raster_product_workspace",
    "detection": "detection_workspace",
}

RESOURCE_CLASSES: dict[ResourceClassId, ResourceClassSpec] = {
    "cpu-standard": {
        "cpu_request": "2",
        "cpu_limit": "8",
        "memory_request": "4Gi",
        "memory_limit": "16Gi",
        "ephemeral_storage_request": "10Gi",
        "ephemeral_storage_limit": "50Gi",
        "gpu_count": 0,
        "minimum_vram_gib": 0,
    },
    "gpu-standard": {
        "cpu_request": "2",
        "cpu_limit": "8",
        "memory_request": "8Gi",
        "memory_limit": "24Gi",
        "ephemeral_storage_request": "20Gi",
        "ephemeral_storage_limit": "60Gi",
        "gpu_count": 1,
        "minimum_vram_gib": 8,
    },
    "gpu-geometry": {
        "cpu_request": "4",
        "cpu_limit": "12",
        "memory_request": "16Gi",
        "memory_limit": "32Gi",
        "ephemeral_storage_request": "40Gi",
        "ephemeral_storage_limit": "120Gi",
        "gpu_count": 1,
        "minimum_vram_gib": 12,
    },
    "gpu-high-memory": {
        "cpu_request": "4",
        "cpu_limit": "12",
        "memory_request": "24Gi",
        "memory_limit": "64Gi",
        "ephemeral_storage_request": "40Gi",
        "ephemeral_storage_limit": "120Gi",
        "gpu_count": 1,
        "minimum_vram_gib": 24,
    },
}

DEFAULT_STAGE_RESOURCE_CLASSES: dict[StageId, ResourceClassId] = {
    "reconstruction": "gpu-geometry",
    "gaussian_training": "gpu-high-memory",
    "gaussian_filtering": "gpu-high-memory",
    "rasterization": "gpu-standard",
    "detection": "gpu-standard",
}


def resource_class_meets_gpu_envelope(
    candidate: ResourceClassId,
    required: ResourceClassId,
) -> bool:
    """Return whether a class satisfies the required GPU/VRAM envelope."""
    candidate_spec = RESOURCE_CLASSES[candidate]
    required_spec = RESOURCE_CLASSES[required]
    return bool(
        candidate_spec["gpu_count"] >= required_spec["gpu_count"]
        and candidate_spec["minimum_vram_gib"]
        >= required_spec["minimum_vram_gib"]
    )


def resource_class_node_selector(
    resource_class: ResourceClassId,
) -> dict[str, str]:
    """Derive cumulative GPU capability labels required by a resource class."""

    spec = RESOURCE_CLASSES[resource_class]
    if spec["gpu_count"] < 1:
        return {}
    selector = {"nvidia.com/gpu.present": "true"}
    minimum_vram_gib = spec["minimum_vram_gib"]
    if minimum_vram_gib > 0:
        selector[
            f"droneai.io/gpu-vram-at-least-{minimum_vram_gib}gb"
        ] = "true"
    return selector


def resource_class_for_stage(
    stage: StageId,
    parameters: dict[str, Any] | None = None,
) -> ResourceClassId:
    parameters = parameters or {}
    baseline_id = DEFAULT_STAGE_RESOURCE_CLASSES[stage]
    ai = parameters.get("ai") or {}
    if stage == "detection" and ai.get("backend") == "sam3":
        baseline_id = "gpu-high-memory"
    requested = parameters.get("resource_class")
    if requested is not None:
        if requested not in RESOURCE_CLASSES:
            raise ValueError(f"Unknown stage resource class: {requested}")
        requested_id = cast(ResourceClassId, requested)
        if not resource_class_meets_gpu_envelope(requested_id, baseline_id):
            raise ValueError(
                f"Resource class {requested} is below the {baseline_id} GPU envelope"
            )
        return requested_id
    return baseline_id


def stage_dag_catalog() -> dict[str, Any]:
    return {
        "version": STAGE_DAG_VERSION,
        "stages": [
            {
                "id": stage,
                "dependencies": list(STAGE_DEPENDENCIES[stage]),
                "artifact_kind": STAGE_ARTIFACT_KINDS[stage],
                "resource_class": DEFAULT_STAGE_RESOURCE_CLASSES[stage],
            }
            for stage in STAGE_ORDER
        ],
        "resource_classes": RESOURCE_CLASSES,
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
