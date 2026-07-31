from typing import Any

from shared.dronegs_profile import DRONEGS_PRODUCTION_DEFAULTS

SAM3_BACKEND_ALIASES = {
    "sam",
    "sam3",
    "sam-3",
    "meta-sam3",
    "meta-sam-3",
    "segment-anything-3",
}


def normalize_ai_backend(value: str | None) -> str:
    normalized = (
        str(value or "yolo")
        .strip()
        .lower()
        .replace("_", "-")
        .replace(" ", "-")
    )
    return "sam3" if normalized in SAM3_BACKEND_ALIASES else "yolo"


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
        "projected_crs_mode": "auto-local",
        "projected_crs": "",
        "feature_type": "SIFT",
        "feature_max_image_size": "4000",
        "feature_num_threads": "-1",
        "feature_max_num_features": "8192",
        "sift_first_octave": "-1",
        "matcher_type": "STANDARD",
        "guided_matching": False,
        "matching_strategy": "spatial",
        "camera_model": "OPENCV",
        "alignment_engine": "ceres",
        "mapper_cmd": "mapper",
        "use_view_graph_calibrator": False,
        "read_orientation": False,
        "gps_pair_max_neighbors": "32",
        "gps_pair_min_neighbors": "8",
        "gps_pair_temporal_neighbors": "6",
        "gps_pair_max_distance_m": "0",
        "global_mapper_max_tracks": "2000000",
        "global_mapper_ba_iterations": "2",
        "global_mapper_skip_retriangulation": False,
        "global_mapper_ceres_iterations": "50",
        "global_mapper_random_seed": "42",
        "global_mapper_ba_min_track_length": "3",
        "global_mapper_tri_complete_max_reproj_error": "15.0",
        "global_mapper_tri_merge_max_reproj_error": "15.0",
        "global_mapper_tri_min_angle": "1.0",
        "minimum_registration_ratio": "0.97",
        "maximum_mean_reprojection_error_px": "2.0",
        "minimum_median_track_length": "3.0",
        "mapping_timeout_seconds": "3600",
        "rtk_refinement_enabled": False,
        "rtk_refinement_timeout_seconds": "900",
        "rtk_refinement_iterations": "25",
        "rtk_refinement_loss_scale": "7.82",
        "alignment_max_error": "10.0",
        "mvs_max_image_size": "4000",
        "ortho_mesh_resolution": "0.02",
        **DRONEGS_PRODUCTION_DEFAULTS,
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
        "projected_crs_mode": "auto-local",
        "projected_crs": "",
        "feature_type": "SIFT",
        "feature_max_image_size": "2400",
        "feature_num_threads": "-1",
        "feature_max_num_features": "4096",
        "sift_first_octave": "-1",
        "matcher_type": "STANDARD",
        "guided_matching": False,
        "matching_strategy": "gps_pairs",
        "camera_model": "SIMPLE_RADIAL",
        "alignment_engine": "auto",
        "mapper_cmd": "global_mapper",
        "use_view_graph_calibrator": True,
        "read_orientation": False,
        "gps_pair_max_neighbors": "32",
        "gps_pair_min_neighbors": "8",
        "gps_pair_temporal_neighbors": "6",
        "gps_pair_max_distance_m": "0",
        "global_mapper_max_tracks": "2000000",
        "global_mapper_ba_iterations": "2",
        "global_mapper_skip_retriangulation": False,
        "global_mapper_ceres_iterations": "50",
        "global_mapper_random_seed": "42",
        "global_mapper_ba_min_track_length": "3",
        "global_mapper_tri_complete_max_reproj_error": "15.0",
        "global_mapper_tri_merge_max_reproj_error": "15.0",
        "global_mapper_tri_min_angle": "1.0",
        "minimum_registration_ratio": "0.97",
        "maximum_mean_reprojection_error_px": "2.0",
        "minimum_median_track_length": "3.0",
        "mapping_timeout_seconds": "2400",
        "rtk_refinement_enabled": True,
        "rtk_refinement_timeout_seconds": "900",
        "rtk_refinement_iterations": "25",
        "rtk_refinement_loss_scale": "7.82",
        "alignment_max_error": "10.0",
        "mvs_max_image_size": "2400",
        "ortho_mesh_resolution": "0.02",
        **DRONEGS_PRODUCTION_DEFAULTS,
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
    "projected_crs_mode": {
        "label": "Projected CRS Policy",
        "description": (
            "auto-local uses an audited national engineering CRS when the complete "
            "mission fits, then falls back to UTM. france-cc selects CC42–CC50."
        ),
        "type": "select",
        "group": "Georeferencing",
        "options": ["auto-local", "france-cc", "utm", "custom"],
    },
    "projected_crs": {
        "label": "Custom Projected EPSG",
        "description": (
            "Used only with the custom policy, for example EPSG:3944 (RGF93 v1), "
            "EPSG:9844 (RGF93 v2b), or an official CRS for another country."
        ),
        "type": "text",
        "group": "Georeferencing",
    },
    "feature_type": {"label": "Feature Extractor", "type": "select", "group": "Features", "options": FEATURE_TYPES},
    "feature_max_image_size": {
        "label": "Feature Resolution (maximum side, px)",
        "description": (
            "2400 px is the Helenenschacht planimetric profile (one Autel "
            "dataset, five checkpoints); use 1600 px as the conservative "
            "starting point for unknown sensors or vertical accuracy."
        ),
        "type": "int",
        "group": "Features",
        "min": 256,
        "max": 12000,
        "step": 64,
    },
    "feature_num_threads": {
        "label": "Feature Threads (-1 = automatic)",
        "description": "Leave at -1 to let COLMAP select the CPU thread count.",
        "type": "int",
        "group": "Features",
        "min": -1,
        "max": 64,
        "step": 1,
    },
    "feature_max_num_features": {
        "label": "Maximum Features per Image",
        "description": (
            "4096 is the planimetric survey default; 2048 reduces extraction "
            "and matching time in the fast profile."
        ),
        "type": "int",
        "group": "Features",
        "min": 256,
        "max": 65536,
        "step": 256,
    },
    "sift_first_octave": {
        "label": "SIFT First Octave",
        "description": (
            "0 starts at native resolution; -1 upsamples the image and is much "
            "more expensive. The RTK 3D profile uses 0 with a 3200 px cap."
        ),
        "type": "int",
        "group": "Features",
        "min": -1,
        "max": 2,
        "step": 1,
    },
    "matcher_type": {
        "label": "Matcher",
        "description": "STANDARD uses the fast brute-force CUDA matcher; LightGlue is substantially slower on an 8 GB GPU.",
        "type": "select",
        "group": "Matching",
        "options": MATCHER_TYPES,
    },
    "guided_matching": {
        "label": "Guided Matching",
        "description": (
            "Runs a second geometry-guided correspondence pass. It increases "
            "matching time but can recover useful observations for precise surveys."
        ),
        "type": "bool",
        "group": "Matching",
    },
    "matching_strategy": {
        "label": "Pair Selection",
        "description": "gps_pairs bounds matching with camera positions and capture order.",
        "type": "select",
        "group": "Matching",
        "options": ["gps_pairs", "spatial", "sequential"],
    },
    "camera_model": {
        "label": "Camera Model",
        "description": "SIMPLE_RADIAL is the fast Mavic 3E default and remains compatible with the Caspar fallback.",
        "type": "select",
        "group": "Mapping",
        "options": ["SIMPLE_RADIAL", "PINHOLE", "OPENCV"],
    },
    "alignment_engine": {
        "label": "Alignment Engine",
        "description": "auto runs GLOMAP first and uses the remaining time budget for a compatible fallback.",
        "type": "select",
        "group": "Mapping",
        "options": ["auto", "glomap", "caspar", "ceres"],
    },
    "mapper_cmd": {"label": "Mapper Command", "type": "select", "group": "Mapping", "options": ["mapper", "global_mapper"]},
    "use_view_graph_calibrator": {
        "label": "Use View Graph Calibrator",
        "description": "Calibrates relative geometry before global mapping; recommended for GLOMAP.",
        "type": "bool",
        "group": "Mapping",
    },
    "read_orientation": {
        "label": "Orientation Priors (reserved)",
        "description": (
            "Reserved for a validated camera-frame IMU/gimbal conversion. It is "
            "currently not consumed by the reconstruction pipeline."
        ),
        "type": "bool",
        "group": "Mapping",
    },
    "gps_pair_max_neighbors": {
        "label": "GPS Pair Maximum Neighbors",
        "description": "Maximum spatial neighbors per image; larger graphs improve resilience but cost more matching time.",
        "type": "int",
        "group": "Matching",
        "min": 4,
        "max": 128,
        "step": 1,
    },
    "gps_pair_min_neighbors": {
        "label": "GPS Pair Minimum Neighbors",
        "description": "Minimum neighbors retained even when an automatic distance bound is active.",
        "type": "int",
        "group": "Matching",
        "min": 2,
        "max": 64,
        "step": 1,
    },
    "gps_pair_temporal_neighbors": {
        "label": "Temporal Pair Radius",
        "description": "Adds this many preceding and following captures to preserve flight-line connectivity.",
        "type": "int",
        "group": "Matching",
        "min": 0,
        "max": 32,
        "step": 1,
    },
    "gps_pair_max_distance_m": {
        "label": "GPS Pair Distance (0 = automatic)",
        "description": "Optional hard spatial radius. Leave at 0 to estimate it from acquisition spacing.",
        "type": "float",
        "group": "Matching",
        "min": 0,
        "max": 5000,
        "step": 5,
    },
    "global_mapper_max_tracks": {
        "label": "GLOMAP Maximum Tracks",
        "description": "Safety cap for tracks entering global positioning and bundle adjustment.",
        "type": "int",
        "group": "Mapping",
        "min": 10000,
        "max": 10000000,
        "step": 10000,
    },
    "global_mapper_ba_iterations": {
        "label": "Global BA Passes",
        "description": (
            "Two passes belong to the Helenenschacht planimetric profile; "
            "one pass is the conservative fast profile."
        ),
        "type": "int",
        "group": "Mapping",
        "min": 1,
        "max": 10,
        "step": 1,
    },
    "global_mapper_skip_retriangulation": {
        "label": "Skip Final Retriangulation",
        "description": (
            "Final retriangulation improved Helenenschacht planimetry but "
            "degraded its vertical checkpoints; enable only after validating "
            "the project accuracy contract."
        ),
        "type": "bool",
        "group": "Mapping",
    },
    "global_mapper_ceres_iterations": {
        "label": "Ceres Iterations per BA Pass",
        "description": "Maximum nonlinear iterations inside each global bundle-adjustment pass.",
        "type": "int",
        "group": "Mapping",
        "min": 10,
        "max": 200,
        "step": 10,
    },
    "global_mapper_random_seed": {
        "label": "GLOMAP Random Seed",
        "description": "Fixes stochastic mapping choices for reproducible A/B benchmarks.",
        "type": "int",
        "group": "Mapping",
        "min": 0,
        "max": 2147483647,
        "step": 1,
    },
    "global_mapper_ba_min_track_length": {
        "label": "BA Minimum Track Length",
        "description": "Only tracks observed in at least this many images enter global BA.",
        "type": "int",
        "group": "Mapping",
        "min": 2,
        "max": 20,
        "step": 1,
    },
    "global_mapper_tri_complete_max_reproj_error": {
        "label": "Retriangulation Completion Error (px)",
        "description": "Maximum reprojection error when completing existing tracks.",
        "type": "float",
        "group": "Mapping",
        "min": 0.5,
        "max": 20,
        "step": 0.5,
    },
    "global_mapper_tri_merge_max_reproj_error": {
        "label": "Retriangulation Merge Error (px)",
        "description": "Maximum reprojection error when merging tracks during retriangulation.",
        "type": "float",
        "group": "Mapping",
        "min": 0.5,
        "max": 20,
        "step": 0.5,
    },
    "global_mapper_tri_min_angle": {
        "label": "Minimum Triangulation Angle (deg)",
        "description": "Rejects weak-depth geometry below this triangulation angle.",
        "type": "float",
        "group": "Mapping",
        "min": 0.1,
        "max": 10,
        "step": 0.1,
    },
    "minimum_registration_ratio": {
        "label": "Minimum Registration Ratio",
        "description": "Rejects a reconstruction below this registered-image fraction before downstream processing.",
        "type": "float",
        "group": "Mapping",
        "min": 0.5,
        "max": 1,
        "step": 0.01,
    },
    "maximum_mean_reprojection_error_px": {
        "label": "Maximum Mean Reprojection Error (px)",
        "description": (
            "Rejects a visually unstable sparse model even when enough "
            "cameras were registered."
        ),
        "type": "float",
        "group": "Mapping",
        "min": 0.25,
        "max": 10,
        "step": 0.05,
    },
    "minimum_median_track_length": {
        "label": "Minimum Median Track Length",
        "description": (
            "Requires sparse points to be observed by enough cameras; "
            "short tracks are fragile for DroneGS."
        ),
        "type": "float",
        "group": "Mapping",
        "min": 2,
        "max": 20,
        "step": 0.5,
    },
    "mapping_timeout_seconds": {
        "label": "Shared Mapping Budget (seconds)",
        "description": "Total time shared by GLOMAP and any automatic fallback; prevents consecutive unbounded attempts.",
        "type": "int",
        "group": "Mapping",
        "min": 60,
        "max": 14400,
        "step": 60,
    },
    "rtk_refinement_enabled": {
        "label": "Covariance-aware RTK refinement",
        "description": (
            "When DJI MRK uncertainties cover at least 95% of the database, run "
            "one bounded robust pose-prior BA after fast global mapping. It is "
            "automatically skipped for standard GNSS datasets."
        ),
        "type": "bool",
        "group": "Georeferencing",
    },
    "rtk_refinement_timeout_seconds": {
        "label": "RTK refinement budget (seconds)",
        "description": (
            "Independent hard timeout for the optional Ceres GPU pose-prior BA; "
            "the verified GLOMAP/CASPAR model is retained on timeout."
        ),
        "type": "int",
        "group": "Georeferencing",
        "min": 30,
        "max": 3600,
        "step": 30,
    },
    "rtk_refinement_iterations": {
        "label": "RTK BA maximum iterations",
        "description": "One robust global pass is normally sufficient for corrected RTK/PPK positions.",
        "type": "int",
        "group": "Georeferencing",
        "min": 1,
        "max": 100,
        "step": 1,
    },
    "rtk_refinement_loss_scale": {
        "label": "RTK Cauchy loss scale",
        "description": (
            "7.82 keeps strong outlier rejection for general use. The validated "
            "Helenenschacht 3D preset uses 62.56 to weight complete corrected RTK "
            "priors more strongly; keep 7.82 for planimetry or unknown datasets."
        ),
        "type": "float",
        "group": "Georeferencing",
        "min": 0.1,
        "max": 1000,
        "step": 0.1,
    },
    "alignment_max_error": {
        "label": "GPS Alignment Max Error (m)",
        "description": "Robust camera-position tolerance. Keep about 10 m for standard DJI GNSS; tighten only for corrected RTK/PPK.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0.1,
        "max": 100,
        "step": 0.5,
    },
    "mvs_max_image_size": {"label": "Undistort Max Image Size", "type": "int", "group": "Undistortion", "min": 256, "max": 12000, "step": 64},
    "ortho_mesh_resolution": {
        "label": "Orthomosaic Resolution (m/px)",
        "description": "Requested ground pixel size for the rendered orthomosaic.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0.005,
        "max": 1,
        "step": 0.005,
    },
    "gs_backend": {
        "label": "Training Backend",
        "description": "Native bounded-memory Gaussian trainer used by DroneAI.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["dronegs"],
    },
    "gs_production_profile": {
        "label": "Production Recipe",
        "description": (
            "Immutable recipe identifier recorded in commands and manifests; "
            "individual expert overrides make the effective run custom."
        ),
        "type": "select",
        "group": "Orthomosaic",
        "options": ["DRONEGS_PRODUCTION_PROFILE_V1", "custom"],
    },
    "gs_iterations": {
        "label": "Training Iterations",
        "description": "Primary training budget; more steps improve convergence and increase runtime.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 5000,
        "max": 100000,
        "step": 5000,
    },
    "gs_data_factor": {
        "label": "Training Image Downscale",
        "description": "1 keeps full resolution; 4 or 8 reduce memory and training time.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["auto", "1", "2", "4", "8"],
    },
    "gs_max_width": {
        "label": "Maximum Training Width (px)",
        "description": "Hard image-width limit after the selected downscale factor.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 256,
        "max": 4096,
        "step": 64,
    },
    "gs_tile_mode": {
        "label": "Training Tile Mode",
        "description": "Splits training into 1, 2 or 4 spatial tiles to bound VRAM use.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["1", "2", "4"],
    },
    "gs_cap_max": {
        "label": "Maximum Gaussians",
        "description": "Upper scene-capacity and memory safety limit.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 1000000,
        "max": 10000000,
        "step": 1000000,
    },
    "gs_sh_degree": {
        "label": "Spherical Harmonics Degree",
        "description": "Higher degrees model view-dependent appearance at additional cost.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["1", "2", "3"],
    },
    "gs_seed": {
        "label": "Deterministic Seed",
        "description": "Keeps benchmark and production runs reproducible.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 2147483647,
        "step": 1,
    },
    "gs_optimizer_profile": {
        "label": "Optimizer Profile",
        "description": (
            "reference-absolute is the optimizer measured by the accepted "
            "Albagnac dev.45 production benchmark."
        ),
        "type": "select",
        "group": "Orthomosaic",
        "options": ["reference-absolute", "dev38-staged-rotation008-absgrad050-fastgs", "dronegs-dev16"],
    },
    "gs_pruning_policy": {
        "label": "Pruning Policy",
        "description": "spatial-bounds removes Gaussians outside the useful scene footprint.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["spatial-bounds", "original"],
    },
    "gs_raster_profile": {
        "label": "Rasterization Profile",
        "description": (
            "fastgs is the structural rasterizer measured by the accepted "
            "production benchmark; bounded remains available for diagnostics."
        ),
        "type": "select",
        "group": "Orthomosaic",
        "options": ["bounded", "fastgs", "auto"],
    },
    "gs_sh_degree_interval": {
        "label": "SH Activation Interval",
        "description": "Iterations between progressive spherical-harmonics degree activations.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 1,
        "max": 10000,
        "step": 100,
    },
    "gs_topology_cooldown": {
        "label": "Topology Cooldown",
        "description": "Final iterations with densification and pruning frozen.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 10000,
        "step": 100,
    },
    "gs_photometric_finish": {
        "label": "Photometric Finish",
        "description": "Final iterations reserved for appearance convergence.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 10000,
        "step": 100,
    },
    "gs_photometric_mse_percent": {
        "label": "Final MSE Weight (%)",
        "description": "Pixel-MSE contribution during the photometric finish.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "gs_checkpoint_every": {
        "label": "Checkpoint Interval",
        "description": "Save a resumable training checkpoint every N iterations; 0 disables it.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 50000,
        "step": 500,
    },
    "gs_test_every": {
        "label": "Held-out Image Interval",
        "description": "Uses every Nth image for PSNR/SSIM validation; 0 disables the split.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "gs_test_split": {
        "label": "Validation Split",
        "description": "Modulo preserves V1 comparability; spatial block measures geographic generalization.",
        "type": "select",
        "group": "Orthomosaic",
        "options": [
            {"value": "modulo", "label": "Modulo (production V1)"},
            {"value": "spatial-block", "label": "Spatial block"},
        ],
    },
    "gs_test_guard_percent": {
        "label": "Spatial Guard Ring (%)",
        "description": "Additional cameras around the held-out block excluded from training to prevent adjacent-view leakage.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "gs_canary_min_psnr": {
        "label": "Minimum Canary PSNR",
        "description": "Rejects completed training below this held-out image-quality threshold.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 100,
        "step": 0.1,
    },
    "gs_canary_min_ssim": {
        "label": "Minimum Canary SSIM",
        "description": "Structural-similarity acceptance threshold for held-out views.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "gs_filter_enabled": {
        "label": "Proximity + Opacity Filter",
        "description": "Enable the standard post-training spatial cleanup.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_filter_max_scale": {
        "label": "Maximum Scale per Axis",
        "description": "Removes abnormally large Gaussian primitives.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 10,
        "step": 0.1,
    },
    "gs_filter_dist": {
        "label": "Distance Multiplier",
        "description": "Controls the spatial-neighborhood rejection radius.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 5,
        "step": 0.1,
    },
    "gs_filter_opacity": {
        "label": "Opacity Threshold",
        "description": "Removes nearly transparent primitives below this opacity.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 0.1,
        "step": 0.001,
    },
    "gs_filter_needle": {
        "label": "Needle Anisotropy Threshold",
        "description": "Rejects extremely elongated splats; 0 disables the check.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 200,
        "step": 5,
    },
    "gs_filter_sor": {
        "label": "Statistical Outlier Removal",
        "description": "Removes primitives isolated from their spatial neighbors.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_filter_cc": {
        "label": "Connected-Component Filter",
        "description": "Keeps the dominant connected scene components.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_filter_z_floater": {
        "label": "Z-Floater Removal",
        "description": "Removes vertically detached Gaussian clusters.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_filter_sor_sigma": {
        "label": "SOR Sigma Multiplier",
        "description": "Higher values make statistical outlier removal more permissive.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 1,
        "max": 10,
        "step": 0.5,
    },
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
