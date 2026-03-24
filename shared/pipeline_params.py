from typing import Any

PIPELINE_DEFAULTS: dict[str, dict[str, Any]] = {
    "legacy": {
        "feature_type": "SIFT",
        "feature_max_image_size": "4000",
        "feature_max_num_features": "8192",
        "matcher_type": "SIFT",
        "mapper_cmd": "mapper",
        "use_view_graph_calibrator": False,
        "read_orientation": False,
        "mvs_max_image_size": "4000",
        "mvs_gpu_index": "-1",
        "mvs_num_iterations": "3",
        "mvs_num_samples": "10",
        "mvs_window_step": "1",
        "mvs_filter_min_num_consistent": "2",
        "fusion_max_image_size": "4000",
        "fusion_min_num_pixels": "5",
        "fusion_cache_size": "32",
        "fusion_chunk_target_memory_gib": "16",
        "ortho_mesh_resolution": "0.02",
        "ortho_mesh_max_dimension": "12000",
        "ortho_mesh_rasterizer": "cuda",
        "texturing_image_max_size": "0",
        "ortho_mesh_simplify_ratio": "1.0",
        "ortho_mesh_simplify_max_faces": "0",
        "ortho_mesh_simplify_on_retry": True,
        "ortho_mesh_simplify_retry_ratio": "0.5",
        "ortho_texture_scale_factor": "0.5",
        "ortho_texturing_apply_color_correction": False,
        "ortho_texturing_num_threads": "4",
        "ortho_mesh_min_normal_cos": "0.5",
        "ortho_mesh_require_upward": True,
        "ortho_mesh_gap_fill_passes": "3",
        "use_mesh_ortho": True,
    },
    "modern": {
        "feature_type": "ALIKED_N16ROT",
        "feature_max_image_size": "4000",
        "feature_max_num_features": "2048",
        "matcher_type": "ALIKED_LIGHTGLUE",
        "mapper_cmd": "global_mapper",
        "use_view_graph_calibrator": True,
        "read_orientation": True,
        "mvs_max_image_size": "4000",
        "mvs_gpu_index": "-1",
        "mvs_num_iterations": "3",
        "mvs_num_samples": "15",
        "mvs_window_step": "1",
        "mvs_filter_min_num_consistent": "2",
        "fusion_max_image_size": "4000",
        "fusion_min_num_pixels": "5",
        "fusion_cache_size": "32",
        "fusion_chunk_target_memory_gib": "16",
        "ortho_mesh_resolution": "0.02",
        "ortho_mesh_max_dimension": "12000",
        "ortho_mesh_rasterizer": "cuda",
        "texturing_image_max_size": "0",
        "ortho_mesh_simplify_ratio": "1.0",
        "ortho_mesh_simplify_max_faces": "0",
        "ortho_mesh_simplify_on_retry": True,
        "ortho_mesh_simplify_retry_ratio": "0.5",
        "ortho_texture_scale_factor": "0.5",
        "ortho_texturing_apply_color_correction": False,
        "ortho_texturing_num_threads": "4",
        "ortho_mesh_min_normal_cos": "0.5",
        "ortho_mesh_require_upward": True,
        "ortho_mesh_gap_fill_passes": "3",
        "use_mesh_ortho": True,
    },
}

PARAMETER_METADATA: dict[str, dict[str, Any]] = {
    "feature_type": {"label": "Feature Type", "type": "select", "group": "Features", "options": ["SIFT", "ALIKED_N16ROT"]},
    "feature_max_image_size": {"label": "Feature Max Image Size", "type": "int", "group": "Features", "min": 256, "max": 12000, "step": 64},
    "feature_max_num_features": {"label": "Feature Max Features", "type": "int", "group": "Features", "min": 256, "max": 65536, "step": 256},
    "matcher_type": {"label": "Matcher Type", "type": "select", "group": "Matching", "options": ["SIFT", "ALIKED_LIGHTGLUE"]},
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
    "ortho_mesh_resolution": {"label": "Ortho Mesh Resolution", "type": "float", "group": "Orthomosaic", "min": 0.005, "max": 1, "step": 0.005},
    "ortho_mesh_max_dimension": {"label": "Ortho Max Dimension", "type": "int", "group": "Orthomosaic", "min": 512, "max": 40000, "step": 256},
    "ortho_mesh_rasterizer": {"label": "Ortho Rasterizer", "type": "select", "group": "Orthomosaic", "options": ["cuda", "cpu"]},
    "texturing_image_max_size": {"label": "Texturing Image Max Size", "type": "int", "group": "Orthomosaic", "min": 0, "max": 12000, "step": 64},
    "ortho_mesh_simplify_ratio": {"label": "Mesh Simplify Ratio", "type": "float", "group": "Orthomosaic", "min": 0.05, "max": 1, "step": 0.05},
    "ortho_mesh_simplify_max_faces": {"label": "Mesh Simplify Max Faces", "type": "int", "group": "Orthomosaic", "min": 0, "max": 100000000, "step": 100000},
    "ortho_mesh_simplify_on_retry": {"label": "Simplify Mesh On Texturing Retry", "type": "bool", "group": "Orthomosaic"},
    "ortho_mesh_simplify_retry_ratio": {"label": "Retry Mesh Simplify Ratio", "type": "float", "group": "Orthomosaic", "min": 0.05, "max": 1, "step": 0.05},
    "ortho_texture_scale_factor": {"label": "Texture Scale Factor", "type": "float", "group": "Orthomosaic", "min": 0.125, "max": 1, "step": 0.125},
    "ortho_texturing_apply_color_correction": {"label": "Apply Texture Color Correction", "type": "bool", "group": "Orthomosaic"},
    "ortho_texturing_num_threads": {"label": "Texturing Threads", "type": "int", "group": "Orthomosaic", "min": -1, "max": 64, "step": 1},
    "ortho_mesh_min_normal_cos": {"label": "Ortho Min Normal Cos", "type": "float", "group": "Orthomosaic", "min": 0, "max": 1, "step": 0.05},
    "ortho_mesh_require_upward": {"label": "Require Upward Faces", "type": "bool", "group": "Orthomosaic"},
    "ortho_mesh_gap_fill_passes": {"label": "Gap Fill Passes", "type": "int", "group": "Orthomosaic", "min": 0, "max": 16, "step": 1},
    "use_mesh_ortho": {"label": "Use Mesh Orthomosaic", "type": "bool", "group": "Orthomosaic"},
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