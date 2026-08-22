"""Pure version-one mission stage contract shared by API and workers."""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

from shared.sam3_capabilities import SAM3_MINIMUM_VRAM_GIB

StageId = Literal[
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
    "gaussian_viewer",
]

ResourceClassId = Literal[
    "cpu-standard",
    "cpu-high-memory",
    "gpu-standard",
    "gpu-geometry",
    "gpu-high-memory",
]

ResourceQuantityField = Literal[
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
    "ephemeral_storage_request",
    "ephemeral_storage_limit",
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


STAGE_DAG_VERSION = 2
STAGE_ORDER: tuple[StageId, ...] = (
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
    "gaussian_viewer",
)
STAGE_DEPENDENCIES: dict[StageId, tuple[StageId, ...]] = {
    "reconstruction": (),
    "gaussian_training": ("reconstruction",),
    "gaussian_filtering": ("gaussian_training",),
    "rasterization": ("gaussian_filtering",),
    "detection": ("rasterization",),
    "gaussian_viewer": ("gaussian_filtering",),
}

# These product branches preserve their own durable failure state and evidence,
# but do not invalidate scientific products produced by the core pipeline.
NON_BLOCKING_STAGES: frozenset[StageId] = frozenset({"gaussian_viewer"})

STAGE_ARTIFACT_KINDS: dict[StageId, str] = {
    "reconstruction": "reconstruction_workspace",
    "gaussian_training": "gaussian_training_workspace",
    "gaussian_filtering": "gaussian_filtering_workspace",
    "rasterization": "raster_product_workspace",
    "detection": "detection_workspace",
    "gaussian_viewer": "gaussian_viewer_bundle",
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
    "cpu-high-memory": {
        "cpu_request": "4",
        "cpu_limit": "12",
        "memory_request": "16Gi",
        "memory_limit": "64Gi",
        "ephemeral_storage_request": "40Gi",
        "ephemeral_storage_limit": "200Gi",
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
    "gaussian_viewer": "cpu-high-memory",
}

# The normal profile is capped at 3M Gaussians. Above that envelope, the
# raster finalization path can transiently materialize multi-gigabyte arrays
# and needs the 64 GiB host-memory class even though its steady-state VRAM use
# remains much lower.
RASTER_HIGH_MEMORY_GAUSSIAN_THRESHOLD = 3_000_000


def _rasterization_requires_high_memory(parameters: dict[str, Any]) -> bool:
    profile = str(parameters.get("quality_profile") or "").strip().lower()
    if profile.startswith("high-quality"):
        return True
    colmap = parameters.get("colmap_params") or {}
    if not isinstance(colmap, dict):
        return False
    try:
        gaussian_cap = int(colmap.get("gs_cap_max") or 0)
    except (TypeError, ValueError):
        return False
    return gaussian_cap > RASTER_HIGH_MEMORY_GAUSSIAN_THRESHOLD


def resource_class_meets_gpu_envelope(
    candidate: ResourceClassId,
    required: ResourceClassId,
) -> bool:
    """Return whether a class satisfies the required GPU/VRAM envelope."""
    candidate_spec = RESOURCE_CLASSES[candidate]
    required_spec = RESOURCE_CLASSES[required]
    return bool(
        candidate_spec["gpu_count"] >= required_spec["gpu_count"]
        and candidate_spec["minimum_vram_gib"] >= required_spec["minimum_vram_gib"]
    )


def _resource_quantity(value: str) -> float:
    suffixes = {"Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3}
    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    return float(value)


def resource_class_meets_envelope(
    candidate: ResourceClassId,
    required: ResourceClassId,
) -> bool:
    """Return whether every schedulable resource meets the required class."""

    candidate_spec = RESOURCE_CLASSES[candidate]
    required_spec = RESOURCE_CLASSES[required]
    quantities: tuple[ResourceQuantityField, ...] = (
        "cpu_request",
        "cpu_limit",
        "memory_request",
        "memory_limit",
        "ephemeral_storage_request",
        "ephemeral_storage_limit",
    )
    return bool(
        all(_resource_quantity(candidate_spec[name]) >= _resource_quantity(required_spec[name]) for name in quantities)
        and resource_class_meets_gpu_envelope(candidate, required)
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
        selector[f"droneai.io/gpu-vram-at-least-{minimum_vram_gib}gb"] = "true"
    return selector


def resource_class_for_stage(
    stage: StageId,
    parameters: dict[str, Any] | None = None,
) -> ResourceClassId:
    parameters = parameters or {}
    baseline_id = DEFAULT_STAGE_RESOURCE_CLASSES[stage]
    if stage == "rasterization" and _rasterization_requires_high_memory(parameters):
        baseline_id = "gpu-high-memory"
    ai = parameters.get("ai") or {}
    if stage == "detection" and ai.get("backend") == "sam3":
        compatible_classes = [
            resource_class
            for resource_class, spec in RESOURCE_CLASSES.items()
            if spec["gpu_count"] >= 1 and spec["minimum_vram_gib"] >= SAM3_MINIMUM_VRAM_GIB
        ]
        baseline_id = min(
            compatible_classes,
            key=lambda resource_class: RESOURCE_CLASSES[resource_class]["minimum_vram_gib"],
        )
    requested = parameters.get("resource_class")
    if requested is not None:
        if requested not in RESOURCE_CLASSES:
            raise ValueError(f"Unknown stage resource class: {requested}")
        requested_id = cast(ResourceClassId, requested)
        if not resource_class_meets_envelope(requested_id, baseline_id):
            raise ValueError(f"Resource class {requested} is below the {baseline_id} resource envelope")
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
                "failure_policy": ("independent" if stage in NON_BLOCKING_STAGES else "blocking"),
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
            raise ValueError(f"Stage {stage} requires selected phase or exact artifact for: " + ", ".join(missing))
    return ordered
