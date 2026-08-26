"""Declarative defaults and dashboard metadata for pipeline parameters."""

from typing import Any

from shared.dronegs_profile import DRONEGS_PRODUCTION_DEFAULTS
from shared.quality_profiles import QUALITY_PROFILE_BY_ID
from shared.facade_process import (
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_PARAMETER_DEFAULTS,
    FACADE_PREVIOUS_DRONEGS_PROFILE_ID,
    FACADE_QUALIFICATION_POLICY_ID,
)

SAM3_BACKEND_ALIASES = {
    "sam",
    "sam3",
    "sam-3",
    "meta-sam3",
    "meta-sam-3",
    "segment-anything-3",
}

FEATURE_TYPES = ["SIFT", "ALIKED_N16ROT", "ALIKED_N32"]
MATCHER_TYPES = ["STANDARD", "LIGHTGLUE"]


_BASE_PIPELINE_DEFAULTS: dict[str, Any] = {
    "orthophoto_mode": "map",
    **FACADE_PARAMETER_DEFAULTS,
    "projected_crs_mode": "auto-local",
    "projected_crs": "",
    "feature_type": "SIFT",
    "feature_max_image_size": "4000",
    "feature_num_threads": "-1",
    "feature_max_num_features": "8192",
    "feature_max_num_matches": "32768",
    "sift_first_octave": "-1",
    "matcher_type": "STANDARD",
    "guided_matching": False,
    "matching_strategy": "spatial",
    "camera_model": "OPENCV",
    "alignment_engine": "ceres",
    "mapper_cmd": "mapper",
    "use_view_graph_calibrator": False,
    "read_orientation": False,
    "imu_gravity_enabled": False,
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
    "rtk_minimum_point_ratio": "0.90",
    "rtk_maximum_reprojection_degradation_px": "0.10",
    "rtk_maximum_track_length_loss_ratio": "0.25",
    "rtk_maximum_focal_length_change_ratio": "0.02",
    "gcp_adjustment_enabled": False,
    "gcp_horizontal_accuracy_m": "0.02",
    "gcp_vertical_accuracy_m": "0.03",
    "gcp_image_accuracy_px": "1.0",
    "gcp_robust_loss_scale": "3.0",
    "gcp_require_checkpoints": False,
    "gcp_min_checkpoint_count": "1",
    "gcp_max_checkpoint_horizontal_rmse_m": "0.10",
    "gcp_max_checkpoint_vertical_rmse_m": "0.20",
    "gcp_max_checkpoint_normalized_error_sigma": "5.0",
    "gcp_min_adjustment_baseline_m": "5.0",
    "alignment_max_error": "10.0",
    "mvs_max_image_size": "4000",
    "mvs_num_threads": "12",
    "ortho_mesh_resolution": "0.02",
    **DRONEGS_PRODUCTION_DEFAULTS,
    "gs_capacity_mode": "fixed",
    "gs_capacity_floor": str(DRONEGS_PRODUCTION_DEFAULTS["gs_cap_max"]),
    "gs_target_gaussian_spacing_pixels": "0.0",
    "gs_resident_partitioning": False,
    "gs_initial_scale_policy": "local-knn",
    "gs_initial_max_projected_sigma_pixels": "2.0",
    "gs_maximum_scale_growth_factor": "54.59815",
    "gs_capacity_targeted_growth": False,
    "gs_ortho_mip_filter_variance": "0.03",
    "gs_ortho_mip_filter_compensation": True,
    "gs_filter_enabled": True,
    "gs_filter_max_scale": "5.0",
    "gs_filter_min_retained_ratio": "0.80",
    "gs_filter_dist": "1.0",
    "gs_filter_opacity": "0.005",
    "gs_filter_needle": "0.0",
    "gs_filter_sor": False,
    "gs_filter_cc": False,
    "gs_filter_z_floater": False,
    "gs_filter_sor_sigma": "4.0",
    "gs_coverage_gate_enabled": True,
    "gs_coverage_grid_size": "16",
    "gs_coverage_min_valid_ratio": "0.50",
    "gs_coverage_cell_threshold": "0.25",
    "gs_coverage_min_covered_cells_ratio": "0.75",
    "gs_coverage_min_worst_cell_ratio": "0.01",
    "gs_coverage_min_camera_cell_ratio": "0.10",
}

# A new parameter is added once to the base contract. Product/profile-specific
# differences remain explicit below, which prevents the dashboard, worker and
# local runners from drifting as the contract grows.
PIPELINE_DEFAULTS: dict[str, dict[str, Any]] = {
    "legacy": dict(_BASE_PIPELINE_DEFAULTS),
    "modern": {
        **_BASE_PIPELINE_DEFAULTS,
        "feature_max_image_size": "2400",
        "feature_max_num_features": "4096",
        "matching_strategy": "gps_pairs",
        "camera_model": "SIMPLE_RADIAL",
        "alignment_engine": "auto",
        "mapper_cmd": "global_mapper",
        "use_view_graph_calibrator": True,
        "mapping_timeout_seconds": "2400",
        "rtk_refinement_enabled": True,
        "mvs_max_image_size": "2400",
    },
}

PARAMETER_METADATA: dict[str, dict[str, Any]] = {
    "orthophoto_mode": {
        "label": "Orthophoto Type",
        "description": "map produces a georeferenced aerial map; facade produces a vertical orthophoto in a local frame without CRS.",
        "type": "select",
        "group": "Product",
        "options": ["map", "facade"],
    },
    "facade_selection_mode": {
        "label": "Facade Image Selection",
        "description": "all keeps every unique image for complete SfM coverage; auto is an optional attitude/pass filter for noisy datasets.",
        "type": "select",
        "group": "Facade",
        "options": ["auto", "all"],
    },
    "facade_excluded_image_ranges": {
        "label": "Excluded Facade Image Ranges",
        "description": "Optional inclusive basename ranges (START..END;START..END) for coherent detail sequences that should not drive the sparse solve.",
        "type": "text",
        "group": "Facade",
    },
    "facade_max_abs_pitch_deg": {
        "label": "Maximum Absolute Gimbal Pitch",
        "description": "Largest angle from horizontal retained by automatic facade selection.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 89,
        "step": 1,
    },
    "facade_min_pass_images": {
        "label": "Minimum Images per Pass",
        "description": "Shorter attitude runs are treated as detail/manual shots and excluded.",
        "type": "int",
        "group": "Facade",
        "min": 3,
        "max": 1000,
        "step": 1,
    },
    "facade_target_yaw_deg": {
        "label": "Target Facade Gimbal Yaw",
        "description": "Optional DJI gimbal yaw in degrees; leave blank for an articulated facade or set it to isolate one wall.",
        "type": "text",
        "group": "Facade",
    },
    "facade_yaw_tolerance_deg": {
        "label": "Facade Yaw Tolerance",
        "description": "Maximum circular yaw difference around the target; 35 degrees retains useful oblique views.",
        "type": "float",
        "group": "Facade",
        "min": 1,
        "max": 180,
        "step": 1,
    },
    "facade_scale_mode": {
        "label": "Facade Scale",
        "description": "GPS baseline uses only relative distances; manual uses a surveyed scale; model-units is unscaled.",
        "type": "select",
        "group": "Facade",
        "options": ["gps-baseline", "manual", "model-units"],
    },
    "facade_meters_per_model_unit": {
        "label": "Manual Metres per Model Unit",
        "description": "Used only when facade scale is manual.",
        "type": "float",
        "group": "Facade",
        "min": 0.000001,
        "max": 1000000,
        "step": 0.000001,
    },
    "facade_texture_max_incidence_deg": {
        "label": "Maximum Texture Incidence",
        "description": "Registered oblique views still constrain geometry, but only cameras within this angle train facade texture when enough remain.",
        "type": "float",
        "group": "Facade",
        "min": 5,
        "max": 89,
        "step": 1,
    },
    "facade_depth_iqr_multiplier": {
        "label": "Facade Front Depth Window (IQR)",
        "description": "Keeps the camera-facing depth band tight; zero disables all facade depth filtering.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 10,
        "step": 0.25,
    },
    "facade_depth_rear_iqr_multiplier": {
        "label": "Facade Rear Depth Window (IQR)",
        "description": "Extends the retained volume behind the wall so doors, windows, arches and recesses are not clipped.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 10,
        "step": 0.25,
    },
    "facade_seed_max_reprojection_error": {
        "label": "Facade Seed Reprojection Error",
        "description": "Maximum COLMAP point error admitted to the Gaussian seed; 2 px favors complete thin borders.",
        "type": "float",
        "group": "Facade",
        "min": 0.1,
        "max": 10,
        "step": 0.1,
    },
    "facade_seed_min_track_length": {
        "label": "Facade Seed Minimum Views",
        "description": "Minimum number of registered images observing a Gaussian seed point; two preserves facade margins.",
        "type": "int",
        "group": "Facade",
        "min": 2,
        "max": 20,
        "step": 1,
    },
    "facade_filter_max_scale": {
        "label": "Facade Maximum Scale per Axis",
        "description": "Removes oversized facade Gaussians in the optimized local model frame.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 10,
        "step": 0.1,
    },
    "facade_canary_min_psnr": {
        "label": "Minimum Facade Canary PSNR",
        "description": "Held-out acceptance threshold calibrated for close-range, multi-scale facade imagery.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 100,
        "step": 0.1,
    },
    "facade_canary_min_ssim": {
        "label": "Minimum Facade Canary SSIM",
        "description": "Held-out structural-similarity threshold for facade training.",
        "type": "float",
        "group": "Facade",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
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
            "4096 is the planimetric survey default; 2048 reduces extraction and matching time in the fast profile."
        ),
        "type": "int",
        "group": "Features",
        "min": 256,
        "max": 65536,
        "step": 256,
    },
    "feature_max_num_matches": {
        "label": "Maximum Matched Features per Image",
        "description": (
            "Caps the quadratic GPU matcher problem without discarding the "
            "extra extracted features; the 8 GB facade preset uses 16384."
        ),
        "type": "int",
        "group": "Matching",
        "min": 1024,
        "max": 32768,
        "step": 1024,
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
        "description": "auto uses Caspar first for facades and GLOMAP first for maps, then spends the remaining time budget on a compatible fallback.",
        "type": "select",
        "group": "Mapping",
        "options": ["auto", "glomap", "caspar", "ceres"],
    },
    "mapper_cmd": {
        "label": "Mapper Command",
        "type": "select",
        "group": "Mapping",
        "options": ["mapper", "global_mapper"],
    },
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
    "imu_gravity_enabled": {
        "label": "Use IMU/gimbal gravity when available",
        "description": (
            "Convert complete Autel/DJI gimbal pitch and roll metadata to a "
            "COLMAP camera-frame gravity vector. It is used only by GLOMAP "
            "global rotation averaging, only above 95% coverage; visual bundle "
            "adjustment remains free to refine every camera rotation."
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
            "Two passes belong to the Helenenschacht planimetric profile; one pass is the conservative fast profile."
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
        "description": ("Rejects a visually unstable sparse model even when enough cameras were registered."),
        "type": "float",
        "group": "Mapping",
        "min": 0.25,
        "max": 10,
        "step": 0.05,
    },
    "minimum_median_track_length": {
        "label": "Minimum Median Track Length",
        "description": (
            "Requires sparse points to be observed by enough cameras; short tracks are fragile for DroneGS."
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
    "rtk_minimum_point_ratio": {
        "label": "RTK minimum sparse-point ratio",
        "description": "Rejects the RTK candidate if it retains too few 3D points relative to the visual baseline.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "rtk_maximum_reprojection_degradation_px": {
        "label": "RTK maximum reprojection degradation (px)",
        "description": "Maximum allowed increase in mean reprojection error before rolling back to the visual model.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0,
        "max": 10,
        "step": 0.01,
    },
    "rtk_maximum_track_length_loss_ratio": {
        "label": "RTK maximum track-length loss ratio",
        "description": "Maximum allowed proportional loss of median sparse-track length.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "rtk_maximum_focal_length_change_ratio": {
        "label": "RTK maximum focal-length change ratio",
        "description": "Rejects an RTK candidate whose median refined focal length drifts excessively from the visual baseline.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0,
        "max": 1,
        "step": 0.005,
    },
    "gcp_adjustment_enabled": {
        "label": "Use surveyed GCP for weighted adjustment",
        "description": (
            "Fit the final georeferencing transform from triangulated GCP using "
            "their declared covariance. Upload gcp_list.txt; optionally upload "
            "gcp_accuracy.csv to set per-point accuracy and adjustment/checkpoint roles."
        ),
        "type": "bool",
        "group": "Georeferencing",
    },
    "gcp_horizontal_accuracy_m": {
        "label": "Default GCP horizontal accuracy (1σ, m)",
        "description": (
            "Fallback horizontal standard deviation when a point is absent from "
            "gcp_accuracy.csv. Smaller values give the control more influence."
        ),
        "type": "float",
        "group": "Georeferencing",
        "min": 0.001,
        "max": 10,
        "step": 0.001,
    },
    "gcp_vertical_accuracy_m": {
        "label": "Default GCP vertical accuracy (1σ, m)",
        "description": "Fallback vertical standard deviation for GCP adjustment.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0.001,
        "max": 20,
        "step": 0.001,
    },
    "gcp_image_accuracy_px": {
        "label": "Default GCP image marking accuracy (1σ, px)",
        "description": (
            "Uncertainty of the annotated target centre. It is propagated through "
            "ray intersection before weighting the georeferencing transform."
        ),
        "type": "float",
        "group": "Georeferencing",
        "min": 0.05,
        "max": 20,
        "step": 0.05,
    },
    "gcp_robust_loss_scale": {
        "label": "GCP robust loss scale (σ)",
        "description": (
            "Cauchy transition in normalized standard deviations; 3σ protects "
            "the fit from a misidentified or badly marked control point."
        ),
        "type": "float",
        "group": "Georeferencing",
        "min": 0.5,
        "max": 20,
        "step": 0.5,
    },
    "gcp_require_checkpoints": {
        "label": "Require independent GCP checkpoints",
        "description": (
            "Rejects GCP promotion when no independently held-out checkpoint is "
            "available. When disabled, the result is explicitly marked unverified."
        ),
        "type": "bool",
        "group": "Georeferencing",
    },
    "gcp_min_checkpoint_count": {
        "label": "Minimum GCP checkpoints",
        "description": "Minimum independent checkpoints when checkpoint observations exist.",
        "type": "int",
        "group": "Georeferencing",
        "min": 1,
        "max": 100,
        "step": 1,
    },
    "gcp_max_checkpoint_horizontal_rmse_m": {
        "label": "Maximum checkpoint horizontal RMSE (m)",
        "description": "Rejects a GCP transform whose independent planimetric RMSE exceeds this value.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0.001,
        "max": 100,
        "step": 0.001,
    },
    "gcp_max_checkpoint_vertical_rmse_m": {
        "label": "Maximum checkpoint vertical RMSE (m)",
        "description": "Rejects a GCP transform whose independent vertical RMSE exceeds this value.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0.001,
        "max": 100,
        "step": 0.001,
    },
    "gcp_max_checkpoint_normalized_error_sigma": {
        "label": "Maximum checkpoint normalized error (σ)",
        "description": "Rejects checkpoints inconsistent with their declared survey and marking uncertainty.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0.1,
        "max": 100,
        "step": 0.1,
    },
    "gcp_min_adjustment_baseline_m": {
        "label": "Minimum GCP adjustment baseline (m)",
        "description": "Rejects spatially concentrated controls using their maximum surveyed XY separation.",
        "type": "float",
        "group": "Georeferencing",
        "min": 0,
        "max": 10000,
        "step": 1,
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
    "mvs_max_image_size": {
        "label": "Undistort Max Image Size",
        "type": "int",
        "group": "Undistortion",
        "min": 256,
        "max": 12000,
        "step": 64,
    },
    "mvs_num_threads": {
        "label": "Undistortion Threads",
        "description": "Bounds concurrent high-resolution image warps to prevent WSL or container memory failures.",
        "type": "int",
        "group": "Undistortion",
        "min": 1,
        "max": 64,
        "step": 1,
    },
    "ortho_mesh_resolution": {
        "label": "Orthomosaic Resolution (m/px)",
        "description": "Requested ground pixel size for the rendered orthomosaic.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0.001,
        "max": 1,
        "step": 0.001,
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
        "options": [
            "DRONEGS_PRODUCTION_PROFILE_V1",
            FACADE_DRONEGS_PROFILE_ID,
            FACADE_PREVIOUS_DRONEGS_PROFILE_ID,
            *QUALITY_PROFILE_BY_ID,
            "custom",
        ],
    },
    "gs_qualification_policy": {
        "label": "Qualification Policy",
        "description": (
            "Independent acceptance-policy identifier for held-out PSNR/SSIM "
            "thresholds; changing it does not rename the training recipe."
        ),
        "type": "select",
        "group": "Orthomosaic",
        "options": [
            "DRONEGS_QUALIFICATION_POLICY_V1",
            FACADE_QUALIFICATION_POLICY_ID,
            "custom",
        ],
    },
    "gs_iterations": {
        "label": "Training Iterations",
        "description": (
            "Primary training budget; topology, position noise and final "
            "convergence schedules scale to the selected duration."
        ),
        "type": "int",
        "group": "Orthomosaic",
        "min": 5000,
        "max": 100000,
        "step": 500,
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
    "gs_ortho_mip_filter_variance": {
        "label": "Orthographic Mip filter variance (px²)",
        "description": (
            "Pixel-space low-pass variance used only for the final ortho. "
            "0.03 is the Helenenschacht sharpness/anti-aliasing compromise; "
            "it is sharper than the former uncompensated 0.3 dilation."
        ),
        "type": "float",
        "group": "Orthomosaic",
        "min": 0.01,
        "max": 1.0,
        "step": 0.01,
    },
    "gs_ortho_mip_filter_compensation": {
        "label": "Compensate Mip filter opacity",
        "description": (
            "Preserve each Gaussian's integrated opacity after pixel-space "
            "filtering instead of dilating and blurring the splat."
        ),
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_tile_mode": {
        "label": "Training Tile Mode",
        "description": "Auto selects the fastest 1, 2 or 4-view split that fits detected VRAM; explicit values are expert overrides.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["auto", "1", "2", "4"],
    },
    "gs_cap_max": {
        "label": "Maximum Gaussians",
        "description": "Operator ceiling for one GPU-resident model; resident-block profiles may exceed it across the complete terrain.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 1000000,
        "max": 20000000,
        "step": 1000000,
    },
    "gs_capacity_mode": {
        "label": "Gaussian Capacity Mode",
        "description": "Adaptive mode sizes the scene from its robust ground area, requested GSD and detected GPU memory.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["fixed", "adaptive"],
    },
    "gs_capacity_floor": {
        "label": "Minimum Gaussian Capacity",
        "description": "Lower bound used by adaptive profiles before resolving scene density and the resident GPU ceiling.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 1000000,
        "max": 20000000,
        "step": 1000000,
    },
    "gs_target_gaussian_spacing_pixels": {
        "label": "Target Gaussian Spacing (px)",
        "description": "Approximate output-pixel spacing used to derive adaptive scene capacity; lower values request more detail.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0.0,
        "max": 64.0,
        "step": 1.0,
    },
    "gs_resident_partitioning": {
        "label": "Planar Resident Blocks",
        "description": "Train, filter and raster map or facade core/buffer blocks one at a time instead of merging the full product on one GPU.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_initial_scale_policy": {
        "label": "Initial Gaussian Scale",
        "description": (
            "projected-knn limits each sparse seed using its actual native "
            "crop, training tile and image scale; local-knn preserves the "
            "legacy 3D-only initialization."
        ),
        "type": "select",
        "group": "Orthomosaic",
        "options": ["projected-knn", "local-knn"],
    },
    "gs_initial_max_projected_sigma_pixels": {
        "label": "Initial Maximum Splat Sigma (px)",
        "description": (
            "Maximum initial one-sigma footprint in the most detailed "
            "training crop; lower values favour detail and require more "
            "Gaussians."
        ),
        "type": "float",
        "group": "Orthomosaic",
        "min": 0.25,
        "max": 16.0,
        "step": 0.25,
    },
    "gs_maximum_scale_growth_factor": {
        "label": "Maximum Scale Growth",
        "description": (
            "Maximum multiplicative growth of a Gaussian axis above the "
            "largest initialized sparse scale. Lower values prevent broad "
            "blur splats; raise only when sparse coverage is genuinely poor."
        ),
        "type": "float",
        "group": "Orthomosaic",
        "min": 1.0,
        "max": 128.0,
        "step": 1.0,
    },
    "gs_capacity_targeted_growth": {
        "label": "Reach Gaussian Capacity",
        "description": (
            "Derive the split rate from the requested cap and remaining "
            "topology windows. Resident adaptive profiles enable this "
            "automatically; it is useful for short custom previews."
        ),
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_sh_degree": {
        "label": "Spherical Harmonics Degree",
        "description": "Higher degrees model view-dependent appearance at additional cost.",
        "type": "select",
        "group": "Orthomosaic",
        "options": ["1", "2", "3"],
    },
    "gs_opacity_sh_enabled": {
        "label": "View-dependent Opacity SH",
        "description": (
            "Expert opt-in for directional opacity-logit residuals. Keep disabled "
            "when geometric consistency is more important than view-dependent effects."
        ),
        "type": "bool",
        "group": "Orthomosaic",
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
            "reference-absolute is the optimizer measured by the accepted Albagnac dev.45 production benchmark."
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
        "description": "Save a resumable checkpoint every N iterations; DroneAI raises smaller values to cap standard runs at 1/2/3 saves (7.5k/15k/30k). Zero disables it.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 50000,
        "step": 500,
    },
    "gs_host_image_cache_mib": {
        "label": "Host Image Cache (MiB)",
        "description": (
            "Decoded-image cache ceiling. Zero auto-sizes from host/cgroup memory while preserving "
            "system headroom; DroneGS allocates at most the image working set."
        ),
        "type": "int",
        "group": "Orthomosaic",
        "min": 0,
        "max": 65536,
        "step": 256,
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
        "description": "Removes abnormally large Gaussian primitives from map products, in projected metres.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 10,
        "step": 0.1,
    },
    "gs_filter_min_retained_ratio": {
        "label": "Minimum Filter Retention",
        "description": "Rejects a product when spatial cleanup removes too much of the trained Gaussian model.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.05,
    },
    "gs_coverage_gate_enabled": {
        "label": "Spatial Coverage Gate",
        "description": "Rejects an aerial product when DSM validity does not cover the projected camera footprint.",
        "type": "bool",
        "group": "Orthomosaic",
    },
    "gs_coverage_grid_size": {
        "label": "Coverage Grid Size",
        "description": "Number of rows and columns used to detect localized holes in the projected footprint.",
        "type": "int",
        "group": "Orthomosaic",
        "min": 4,
        "max": 64,
        "step": 1,
    },
    "gs_coverage_min_valid_ratio": {
        "label": "Minimum Valid DSM Ratio",
        "description": "Minimum fraction of valid pixels across the expected footprint cells.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "gs_coverage_cell_threshold": {
        "label": "Covered Cell Threshold",
        "description": "Valid-pixel ratio required for one footprint cell to count as covered.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "gs_coverage_min_covered_cells_ratio": {
        "label": "Minimum Covered Cells",
        "description": "Minimum fraction of expected footprint cells that must pass the cell threshold.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "gs_coverage_min_worst_cell_ratio": {
        "label": "Minimum Worst Interior Cell",
        "description": "Rejects a completely missing interior footprint cell while allowing partial NoData cells where an irregular footprint crosses its boundary.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
    },
    "gs_coverage_min_camera_cell_ratio": {
        "label": "Minimum Camera-Area Coverage",
        "description": "Minimum tenth-percentile validity in grid cells containing registered camera centres.",
        "type": "float",
        "group": "Orthomosaic",
        "min": 0,
        "max": 1,
        "step": 0.01,
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
