from typing import Any


FEATURE_TYPES = ["SIFT", "ALIKED_N16ROT", "ALIKED_N32"]
MATCHER_TYPES = ["STANDARD", "LIGHTGLUE"]


def normalize_feature_type(value: Any) -> str:
    normalized = str(value or "SIFT").strip().upper().replace("-", "_")
    if normalized in FEATURE_TYPES:
        return normalized
    if normalized in {"ALIKE", "ALIKED", "ALIKED_N16", "ALIKED_N16_ROT"}:
        return "ALIKED_N16ROT"
    return "SIFT"


def normalize_matcher_type(value: Any) -> str:
    normalized = str(value or "STANDARD").strip().upper().replace("-", "_")
    if normalized in MATCHER_TYPES:
        return normalized
    if normalized in {"SIFT", "SIFT_BRUTEFORCE", "ALIKED_BRUTEFORCE", "BRUTEFORCE", "BRUTE_FORCE"}:
        return "STANDARD"
    if normalized in {"SIFT_LIGHTGLUE", "ALIKED_LIGHTGLUE"}:
        return "LIGHTGLUE"
    return "STANDARD"

PIPELINE_DEFAULTS: dict[str, dict[str, Any]] = {
    "legacy": {
        "feature_type": "SIFT",
        "feature_max_image_size": "4000",
        "feature_num_threads": "-1",
        "feature_max_num_features": "8192",
        "matcher_type": "STANDARD",
        "mapper_cmd": "mapper",
        "use_view_graph_calibrator": False,
        "read_orientation": False,
        "mvs_max_image_size": "4000",
        "mvs_gpu_index": "0",
        "mvs_num_iterations": "3",
        "mvs_num_samples": "10",
        "mvs_window_step": "1",
        "mvs_filter_min_num_consistent": "2",
        "fusion_max_image_size": "4000",
        "fusion_min_num_pixels": "5",
        "fusion_cache_size": "32",
        "fusion_chunk_target_memory_gib": "16",
        "ortho_mesh_resolution": "0.02",
        "use_mesh_ortho": True,
    },
    "modern": {
        "feature_type": "ALIKED_N16ROT",
        "feature_max_image_size": "1600",
        "feature_num_threads": "1",
        "feature_max_num_features": "2048",
        "matcher_type": "LIGHTGLUE",
        "mapper_cmd": "global_mapper",
        "use_view_graph_calibrator": True,
        "read_orientation": True,
        "mvs_max_image_size": "4000",
        "mvs_gpu_index": "0",
        "mvs_num_iterations": "3",
        "mvs_num_samples": "15",
        "mvs_window_step": "1",
        "mvs_filter_min_num_consistent": "2",
        "fusion_max_image_size": "4000",
        "fusion_min_num_pixels": "5",
        "fusion_cache_size": "32",
        "fusion_chunk_target_memory_gib": "16",
        "ortho_mesh_resolution": "0.02",
        "use_mesh_ortho": True,
    },
}

PARAMETER_METADATA: dict[str, dict[str, Any]] = {
    "feature_type": {"label": "Feature Extractor", "type": "select", "group": "Features", "options": FEATURE_TYPES},
    "feature_max_image_size": {"label": "Feature Max Image Size", "type": "int", "group": "Features", "min": 256, "max": 12000, "step": 64},
    "feature_num_threads": {"label": "Feature Threads", "type": "int", "group": "Features", "min": 1, "max": 64, "step": 1},
    "feature_max_num_features": {"label": "Feature Max Features", "type": "int", "group": "Features", "min": 256, "max": 65536, "step": 256},
    "matcher_type": {"label": "Matcher", "type": "select", "group": "Matching", "options": MATCHER_TYPES},
    "mapper_cmd": {"label": "Mapper Command", "type": "select", "group": "Mapping", "options": ["mapper", "global_mapper"]},
    "use_view_graph_calibrator": {"label": "Use View Graph Calibrator", "type": "bool", "group": "Mapping"},
    "read_orientation": {"label": "Read Orientation", "type": "bool", "group": "Mapping"},
    "mvs_max_image_size": {"label": "MVS Max Image Size", "type": "int", "group": "Dense Stereo", "min": 256, "max": 12000, "step": 64},
    "mvs_gpu_index": {"label": "MVS GPU Index", "type": "text", "group": "Dense Stereo"},
    "mvs_num_iterations": {"label": "MVS Iterations", "type": "int", "group": "Dense Stereo", "min": 1, "max": 20, "step": 1},
    "mvs_num_samples": {"label": "MVS Samples", "type": "int", "group": "Dense Stereo", "min": 1, "max": 64, "step": 1},
    "mvs_window_step": {"label": "MVS Window Step", "type": "int", "group": "Dense Stereo", "min": 1, "max": 8, "step": 1},
    "mvs_filter_min_num_consistent": {"label": "MVS Min Consistent", "type": "int", "group": "Dense Stereo", "min": 1, "max": 16, "step": 1},
    "fusion_max_image_size": {"label": "Fusion Max Image Size", "type": "int", "group": "Fusion", "min": 256, "max": 12000, "step": 64},
    "fusion_min_num_pixels": {"label": "Fusion Min Num Pixels", "type": "int", "group": "Fusion", "min": 1, "max": 32, "step": 1},
    "fusion_cache_size": {"label": "Fusion Cache Size (GiB)", "type": "float", "group": "Fusion", "min": 1, "max": 256, "step": 0.5},
    "fusion_chunk_target_memory_gib": {"label": "Fusion Chunk Target Memory (GiB)", "type": "float", "group": "Fusion", "min": 4, "max": 256, "step": 1},
    "ortho_mesh_resolution": {"label": "Ortho Resolution", "type": "float", "group": "Orthomosaic", "min": 0.005, "max": 1, "step": 0.005},
    "use_mesh_ortho": {"label": "Use True Ortho DSM", "type": "bool", "group": "Orthomosaic"},
}

PARAM_OVERRIDE_KEYS = sorted(PIPELINE_DEFAULTS["legacy"].keys() | PIPELINE_DEFAULTS["modern"].keys())

FUSION_BYTES_PER_PIXEL = 19
FUSION_CHUNK_BYTES_PER_PIXEL = 38
TARGET_CACHED_IMAGES = 8
RECOMMENDED_IMAGE_SIZES = [512, 768, 1024, 1536, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 6000]


def coerce_param_value(template_value: Any, value: Any) -> Any:
    if isinstance(template_value, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if value is None:
        return template_value
    return str(value)


def merge_pipeline_params(pipeline_mode: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    pipeline_key = pipeline_mode if pipeline_mode in PIPELINE_DEFAULTS else "modern"
    params = dict(PIPELINE_DEFAULTS[pipeline_key])
    for key, value in (overrides or {}).items():
        if key in params and value is not None:
            params[key] = coerce_param_value(params[key], value)
    params["feature_type"] = normalize_feature_type(params.get("feature_type"))
    params["matcher_type"] = normalize_matcher_type(params.get("matcher_type"))
    return params


def merge_mission_pipeline_params(pipeline_mode: str, mission_params: dict[str, Any] | None = None) -> dict[str, Any]:
    mission_params = mission_params or {}
    merged = merge_pipeline_params(pipeline_mode, mission_params.get("colmap_params"))
    top_level_overrides = {
        key: mission_params[key]
        for key in PARAM_OVERRIDE_KEYS
        if key in mission_params and mission_params[key] is not None
    }
    return merge_pipeline_params(pipeline_mode, {**merged, **top_level_overrides})