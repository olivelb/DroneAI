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
        "ortho_mesh_resolution": "0.02",
        "gs_iterations": "30000",
        "gs_data_factor": "auto",
        "gs_cap_max": "5000000",
        "gs_sh_degree": "3",
        "gs_filter_enabled": True,
        "gs_filter_max_scale": "1.0",
        "gs_filter_dist": "1.0",
        "gs_filter_opacity": "0.005",
        "gs_filter_needle": "0.0",
        "gs_filter_sor": False,
        "gs_filter_cc": False,
        "gs_filter_z_floater": False,
        "gs_filter_sor_sigma": "4.0",
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
        "ortho_mesh_resolution": "0.02",
        "gs_iterations": "30000",
        "gs_data_factor": "auto",
        "gs_cap_max": "5000000",
        "gs_sh_degree": "3",
        "gs_filter_enabled": True,
        "gs_filter_max_scale": "1.0",
        "gs_filter_dist": "1.0",
        "gs_filter_opacity": "0.005",
        "gs_filter_needle": "0.0",
        "gs_filter_sor": False,
        "gs_filter_cc": False,
        "gs_filter_z_floater": False,
        "gs_filter_sor_sigma": "4.0",
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
    "mvs_max_image_size": {"label": "Undistort Max Image Size", "type": "int", "group": "Undistortion", "min": 256, "max": 12000, "step": 64},
    "ortho_mesh_resolution": {"label": "Ortho Resolution (m/px)", "type": "float", "group": "Orthomosaic", "min": 0.005, "max": 1, "step": 0.005},
    "gs_iterations": {"label": "GS Training Iterations (LichtFeld MRNF)", "type": "int", "group": "Orthomosaic", "min": 5000, "max": 100000, "step": 5000},
    "gs_data_factor": {"label": "GS Training Image Scale", "type": "select", "group": "Orthomosaic", "options": ["auto", "1", "2", "4", "8"]},
    "gs_cap_max": {"label": "GS Max Gaussians (MRNF)", "type": "int", "group": "Orthomosaic", "min": 1000000, "max": 10000000, "step": 1000000},
    "gs_sh_degree": {"label": "GS Spherical Harmonics Degree", "type": "select", "group": "Orthomosaic", "options": ["1", "2", "3"]},
    "gs_filter_enabled": {"label": "GS Spatial Filter (proximity + opacity)", "type": "bool", "group": "Orthomosaic"},
    "gs_filter_max_scale": {"label": "GS Max Scale per Axis", "type": "float", "group": "Orthomosaic", "min": 0, "max": 10, "step": 0.1},
    "gs_filter_dist": {"label": "GS Distance Multiplier", "type": "float", "group": "Orthomosaic", "min": 0, "max": 5, "step": 0.1},
    "gs_filter_opacity": {"label": "GS Opacity Threshold", "type": "float", "group": "Orthomosaic", "min": 0, "max": 0.1, "step": 0.001},
    "gs_filter_needle": {"label": "GS Needle Anisotropy Threshold", "type": "float", "group": "Orthomosaic", "min": 0, "max": 200, "step": 5},
    "gs_filter_sor": {"label": "GS Statistical Outlier Removal", "type": "bool", "group": "Orthomosaic"},
    "gs_filter_cc": {"label": "GS Connected-Component Filter", "type": "bool", "group": "Orthomosaic"},
    "gs_filter_z_floater": {"label": "GS Z-Floater Removal", "type": "bool", "group": "Orthomosaic"},
    "gs_filter_sor_sigma": {"label": "GS SOR Sigma Multiplier", "type": "float", "group": "Orthomosaic", "min": 1, "max": 10, "step": 0.5},
}

PARAM_OVERRIDE_KEYS = sorted(PIPELINE_DEFAULTS["legacy"].keys() | PIPELINE_DEFAULTS["modern"].keys())


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