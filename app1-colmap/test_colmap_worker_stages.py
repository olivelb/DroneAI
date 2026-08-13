import os
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
for module_path in (APP_DIR, ROOT_DIR):
    if str(module_path) not in sys.path:
        sys.path.append(str(module_path))

from colmap_worker import dronegs_config
from colmap_worker import runtime as worker_runtime
from colmap_worker import sparse_mapping
from colmap_worker.stages import gaussian as gaussian_stage
from colmap_worker.stages import preparation as preparation_stage
from colmap_worker.stages import publication as publication_stage
from gaussian_ortho import phase_artifacts
from gaussian_ortho.capacity_planning import (
    GaussianCapacityPlan,
    GaussianDensityAssessment,
)
from gaussian_ortho.generate_gaussian_orthophoto import (
    GaussianFilteredPartition,
    GaussianFilteringPhaseState,
    GaussianPartitionModel,
    GaussianTrainingState,
)
from gaussian_ortho.partition import CellBounds
from gaussian_ortho.render_geometry import GaussianRenderGeometry
from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
)
from shared.facade_process import FACADE_PARAMETER_DEFAULTS
from shared.quality_profiles import quality_profile


class TestColmapStageHelpers(unittest.TestCase):
    def test_non_facade_asset_selection_delegates_to_generic_discovery(self):
        images = [Path("/input/image.jpg")]
        sidecars = [Path("/input/image.MRK")]
        with patch.object(
            preparation_stage,
            "discover_input_assets",
            return_value=(images, sidecars),
        ) as discover:
            selected = preparation_stage._select_input_assets(
                "/input",
                "/workspace",
                {},
                False,
                "vol-map",
            )

        self.assertEqual(selected, (images, sidecars, "/workspace/facade_selection_report.json"))
        discover.assert_called_once_with("/input")

    def test_copy_input_assets_checks_cancellation_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_dir = os.path.join(tmp_dir, "raw")
            clean_dir = os.path.join(tmp_dir, "clean")
            os.makedirs(raw_dir)
            os.makedirs(clean_dir)
            source = Path(raw_dir, "image.jpg")
            source.write_bytes(b"image")

            with (
                patch.object(
                    worker_runtime,
                    "ensure_not_cancelled",
                    side_effect=worker_runtime.PipelineCancelledError("cancelled"),
                ),
                patch.object(worker_runtime, "report_mission_progress"),
            ):
                with self.assertRaises(worker_runtime.PipelineCancelledError):
                    preparation_stage._copy_input_assets(
                        [source],
                        [],
                        raw_dir,
                        clean_dir,
                        "vol-cancel-copy",
                    )

            self.assertTrue(os.path.isdir(raw_dir))
            self.assertFalse(os.path.exists(os.path.join(clean_dir, "image.jpg")))

    def test_reconstruction_cache_invalidates_changed_inputs(self):
        requested = {"fingerprint": "current"}
        with (
            patch.object(preparation_stage, "build_colmap_cache_config", return_value=requested),
            patch.object(preparation_stage, "load_colmap_cache_config", return_value=requested),
            patch.object(preparation_stage, "detect_existing_pipeline", return_value=None),
            patch.object(preparation_stage, "save_colmap_cache_config") as save,
            patch.object(preparation_stage, "invalidate_pipeline_artifacts") as invalidate,
            patch.object(preparation_stage.os.path, "exists", return_value=False),
        ):
            preparation_stage._validate_reconstruction_cache(
                params={},
                feature_family="SIFT",
                feature_type="SIFT",
                copied_count=1,
                clean_images_dir="/workspace/images",
                workspace_dir="/workspace",
                db_path="/workspace/database.db",
                sparse_path="/workspace/sparse",
                dense_path="/workspace/dense",
                geo_data_file="/workspace/geo_data.txt",
                vol_id="vol-cache",
            )

        self.assertIn("Input images changed", invalidate.call_args.args[-1])
        save.assert_called_once_with("/workspace", requested)

    def test_sparse_quality_gate_accepts_only_complete_thresholds(self):
        gate = sparse_mapping.SparseQualityGate(
            total_images=10,
            minimum_registered_images=9,
            maximum_reprojection_error=2.0,
            minimum_track_length=3.0,
        )
        quality = {
            "registered_images": 9,
            "points3D": 1,
            "mean_reprojection_error_px": 2.0,
            "median_track_length": 3.0,
        }

        self.assertTrue(gate.accepts(quality))
        self.assertFalse(gate.accepts({**quality, "registered_images": 8}))
        self.assertFalse(gate.accepts({**quality, "mean_reprojection_error_px": None}))

    def test_mapping_budget_reports_remaining_time_and_exhaustion(self):
        budget = sparse_mapping.MappingBudget(timeout_seconds=30.0, started_at=100.0)

        self.assertEqual(budget.remaining(now=110.0), 20.0)
        with self.assertRaisesRegex(TimeoutError, "shared 30s mapping budget"):
            budget.remaining(now=130.0)

    def test_dronegs_named_profile_becomes_custom_only_after_override(self):
        params = DRONEGS_PRODUCTION_PROFILE_V1.pipeline_defaults()
        config, warnings = dronegs_config.resolve_dronegs_config(
            params,
            facade_mode=False,
            data_factor=DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
        )

        self.assertEqual(config.profile_id, DRONEGS_PRODUCTION_PROFILE_V1.profile_id)
        self.assertEqual(config.qualification_policy_id, DRONEGS_QUALIFICATION_POLICY_ID)
        self.assertEqual(config.filter_max_scale, 5.0)
        self.assertEqual(config.filter_min_retained_ratio, 0.80)
        self.assertTrue(config.coverage_gate_enabled)
        self.assertEqual(config.coverage_grid_size, 16)
        self.assertEqual(config.coverage_min_valid_ratio, 0.50)
        self.assertEqual(warnings, ())

        facade_config, _ = dronegs_config.resolve_dronegs_config(
            params,
            facade_mode=True,
            data_factor=DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
        )
        self.assertEqual(facade_config.filter_max_scale, 1.0)
        self.assertEqual(facade_config.filter_min_retained_ratio, 0.0)
        self.assertFalse(facade_config.coverage_gate_enabled)

        overridden, warnings = dronegs_config.resolve_dronegs_config(
            {**params, "gs_iterations": "123"},
            facade_mode=False,
            data_factor=DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
        )
        self.assertEqual(overridden.profile_id, "custom")
        self.assertTrue(warnings)

    def test_versioned_quality_profile_becomes_custom_after_training_override(self):
        profile = quality_profile("high-quality-v3")
        config, warnings = dronegs_config.resolve_dronegs_config(
            profile.parameters,
            facade_mode=False,
            data_factor=int(profile.parameters["gs_data_factor"]),
        )

        self.assertEqual(config.profile_id, "high-quality-v3")
        self.assertEqual(config.iterations, 30_000)
        self.assertEqual(config.cap_max, 12_000_000)
        self.assertEqual(config.capacity_mode, "adaptive")
        self.assertEqual(config.capacity_floor, 5_000_000)
        self.assertEqual(config.target_gaussian_spacing_pixels, 3.6)
        self.assertTrue(config.resident_partitioning)
        self.assertEqual(warnings, ())

        legacy_profile = quality_profile("high-quality-v2")
        legacy_config, legacy_warnings = dronegs_config.resolve_dronegs_config(
            {
                **legacy_profile.parameters,
                "gs_resident_partitioning": "false",
            },
            facade_mode=False,
            data_factor=int(legacy_profile.parameters["gs_data_factor"]),
        )
        self.assertFalse(legacy_config.resident_partitioning)
        self.assertEqual(legacy_warnings, ())

        overridden, warnings = dronegs_config.resolve_dronegs_config(
            {**profile.parameters, "gs_cap_max": "11000000"},
            facade_mode=False,
            data_factor=int(profile.parameters["gs_data_factor"]),
        )
        self.assertEqual(overridden.profile_id, "custom")
        self.assertTrue(warnings)

    def test_dronegs_qualification_override_is_recorded_as_custom(self):
        params = {
            **DRONEGS_PRODUCTION_PROFILE_V1.pipeline_defaults(),
            "gs_canary_min_psnr": "19.0",
        }
        config, warnings = dronegs_config.resolve_dronegs_config(
            params,
            facade_mode=False,
            data_factor=DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
        )

        self.assertEqual(config.profile_id, DRONEGS_PRODUCTION_PROFILE_V1.profile_id)
        self.assertEqual(config.qualification_policy_id, "custom")
        self.assertEqual(len(warnings), 1)

    def test_projected_initialization_overrides_apply_to_map_and_facade(self):
        params = {
            **quality_profile("normal-v3").parameters,
            "gs_initial_scale_policy": "projected-knn",
            "gs_initial_max_projected_sigma_pixels": "1.5",
            "gs_maximum_scale_growth_factor": "8",
            "gs_capacity_targeted_growth": "true",
        }

        for facade_mode in (False, True):
            config, warnings = dronegs_config.resolve_dronegs_config(
                params,
                facade_mode=facade_mode,
                data_factor=4,
            )
            self.assertEqual(config.initial_scale_policy, "projected-knn")
            self.assertEqual(config.initial_max_projected_sigma_pixels, 1.5)
            self.assertEqual(config.maximum_scale_growth_factor, 8.0)
            self.assertTrue(config.capacity_targeted_growth)
            self.assertEqual(config.profile_id, "custom")
            self.assertTrue(warnings)

    def test_checkpoint_store_reuses_local_state_without_downloading(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint_root = os.path.join(tmp_dir, "checkpoints")
            mission_dir = os.path.join(checkpoint_root, "vol-existing")
            os.makedirs(mission_dir)
            Path(mission_dir, "checkpoint.ply").write_bytes(b"checkpoint")
            with (
                patch.dict(os.environ, {"DRONEGS_CHECKPOINT_ROOT": checkpoint_root}),
                patch.object(gaussian_stage.storage, "download_directory") as download,
            ):
                result = gaussian_stage._prepare_checkpoint_store(
                    os.path.join(tmp_dir, "workspace"),
                    "missions/vol-existing",
                    "vol-existing",
                )

        self.assertEqual(result, (mission_dir, "missions/vol-existing/gaussian-checkpoints"))
        download.assert_not_called()

    def test_checkpoint_callback_reports_failed_remote_sync(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            checkpoint = Path(tmp_dir, "iteration_10.ply")
            checkpoint.write_bytes(b"checkpoint")
            callback = gaussian_stage._checkpoint_callback(
                tmp_dir,
                "missions/vol/gaussian-checkpoints",
                "vol",
            )
            with (
                patch.object(
                    gaussian_stage.storage,
                    "upload_file",
                    side_effect=OSError("offline"),
                ),
                patch.object(worker_runtime, "report_mission_progress") as report,
            ):
                callback(checkpoint, 10)

        self.assertIn("remains locally durable", report.call_args.kwargs["log"])

    def test_gaussian_product_run_resolves_one_reusable_typed_recipe(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dense_path = os.path.join(tmp_dir, "dense")
            os.makedirs(dense_path)
            params = {
                **DRONEGS_PRODUCTION_PROFILE_V1.pipeline_defaults(),
                **FACADE_PARAMETER_DEFAULTS,
                "ortho_mesh_resolution": "0.025",
            }
            preparation = types.SimpleNamespace(
                params=params,
                facade_mode=False,
                orthophoto_mode="map",
                mission_s3_prefix="missions/vol-recipe",
                dense_path=dense_path,
            )
            reconstruction = types.SimpleNamespace(utm_crs="EPSG:32631")
            alignment = types.SimpleNamespace(alignment_transform_path=None)
            checkpoint_dir = os.path.join(tmp_dir, "checkpoints")
            with (
                patch.object(gaussian_stage, "dense_sparse_model_ready", return_value=True),
                patch.object(
                    gaussian_stage,
                    "_prepare_checkpoint_store",
                    return_value=(
                        checkpoint_dir,
                        "missions/vol-recipe/gaussian-checkpoints",
                    ),
                ),
                patch.object(gaussian_stage, "_checkpoint_callback", return_value=lambda *_: None),
            ):
                product_run = gaussian_stage.prepare_gaussian_product_run(
                    preparation,
                    reconstruction,
                    alignment,
                    tmp_dir,
                    "vol-recipe",
                )

            config = product_run.config
            self.assertEqual(config.dense_path, dense_path)
            self.assertEqual(config.ortho_file, os.path.join(tmp_dir, "orthomosaic.tif"))
            self.assertEqual(config.utm_crs, "EPSG:32631")
            self.assertEqual(config.resolution, 0.025)
            self.assertEqual(config.cap_max, DRONEGS_PRODUCTION_PROFILE_V1.cap_max)
            self.assertEqual(config.checkpoint_dir, checkpoint_dir)
            self.assertEqual(product_run.trainer_backend, "dronegs")
            self.assertEqual(
                product_run.checkpoint_s3_prefix,
                "missions/vol-recipe/gaussian-checkpoints",
            )

            model_path = Path(tmp_dir, "training", "final.ply")
            model_path.parent.mkdir()
            model_path.write_bytes(b"ply")
            phase = types.SimpleNamespace(
                backend_name="dronegs",
                trainer_binary_sha256="a" * 64,
                scene_state=types.SimpleNamespace(
                    transform_data={"scale": 1.0},
                    mean_exif_alt=123.0,
                    colmap_to_meters=1.0,
                    scale_source="geographic-sim3",
                    facade_frame=None,
                    registered_cameras=[object(), object()],
                    texture_camera_count=2,
                    texture_filter_applied=False,
                    minimum_sparse_observations=20,
                    seed_max_error=1.0,
                    seed_min_track=3,
                    gaussian_seed_point_count=50_000,
                ),
                training_state=types.SimpleNamespace(
                    merged_model=types.SimpleNamespace(num_gaussians=1_500_000),
                    facade_subset_result=None,
                ),
                capacity_plan=GaussianCapacityPlan(
                    mode="adaptive",
                    requested_cap=1_500_000,
                    capacity_floor=1_000_000,
                    target_spacing_pixels=8.0,
                    robust_ground_area_m2=2_500.0,
                    requested_gsd_m=0.025,
                    target_output_pixels=4_000_000,
                    surface_target=100_000,
                    free_vram_bytes=None,
                    total_vram_bytes=None,
                    vram_cap=None,
                    resident_cap=1_000_000,
                    partition_overlap=0.2,
                    buffer_capacity_factor=1.96,
                    required_cell_count=1,
                    cells_sufficient=True,
                    effective_scene_cap=1_000_000,
                    effective_cell_cap=1_000_000,
                    cell_count=1,
                    estimated_capacity_bytes=1_280_000_000,
                ),
            )
            phase_artifacts.write_training_artifact(
                tmp_dir,
                config,
                phase,
                model_path=model_path,
            )
            artifact = phase_artifacts.read_training_artifact(tmp_dir, config)
            self.assertEqual(artifact.model_path, model_path)
            self.assertEqual(artifact.gaussian_count, 1_500_000)
            self.assertEqual(artifact.capacity_plan, phase.capacity_plan)
            with self.assertRaisesRegex(ValueError, "config identity"):
                phase_artifacts.read_training_artifact(
                    tmp_dir,
                    replace(config, cap_max=config.cap_max + 1),
                )

            partition_path = Path(tmp_dir, "training", "cell-0-0.ply")
            partition_path.write_bytes(b"core-ply")
            bounds = CellBounds(
                core_x_min=0.0,
                core_x_max=10.0,
                core_y_min=0.0,
                core_y_max=10.0,
                buffer_x_min=-2.0,
                buffer_x_max=12.0,
                buffer_y_min=-2.0,
                buffer_y_max=12.0,
                row=0,
                col=0,
                include_core_x_max=True,
                include_core_y_max=True,
            )
            partition_phase = types.SimpleNamespace(
                backend_name="dronegs",
                trainer_binary_sha256="a" * 64,
                scene_state=phase.scene_state,
                training_state=GaussianTrainingState(
                    merged_model=None,
                    final_ply=None,
                    facade_subset_result=None,
                    partition_models=(
                        GaussianPartitionModel(
                            bounds=bounds,
                            model_path=str(partition_path),
                            gaussian_count=1_200_000,
                            core_gaussian_count=1_200_000,
                        ),
                    ),
                ),
                capacity_plan=phase.capacity_plan,
            )
            phase_artifacts.write_training_artifact(
                tmp_dir,
                config,
                partition_phase,
                model_path=None,
            )
            partition_artifact = phase_artifacts.read_training_artifact(
                tmp_dir,
                config,
            )
            self.assertIsNone(partition_artifact.model_path)
            self.assertEqual(partition_artifact.gaussian_count, 1_200_000)
            self.assertEqual(len(partition_artifact.partition_models), 1)
            self.assertEqual(partition_artifact.partition_models[0].bounds, bounds)

            partition_geometry = GaussianRenderGeometry(
                geo_origin=np.array([600_000.0, 4_900_000.0, 120.0]),
                frame_origin=None,
                rotation_geo=None,
                sh_direction_rotation=np.eye(3),
                facade_depth_bounds_model=None,
                render_extent=(0.0, 10.0, 0.0, 10.0, 0.0, 8.0),
                local_gsd=0.025,
                resolution_units="metres",
                coverage_camera_positions=np.array([[1.0, 1.0, 10.0]]),
            )
            partition_filtering = GaussianFilteringPhaseState(
                render_state=None,
                input_gaussians=1_200_000,
                output_gaussians=1_100_000,
                density_assessment=None,
                partition_geometry=partition_geometry,
                partition_models=(
                    GaussianFilteredPartition(
                        bounds=bounds,
                        model_path=str(partition_path),
                        gaussian_count=1_150_000,
                        core_gaussian_count=1_100_000,
                        render_extent=partition_geometry.render_extent,
                        facade_depth_bounds_model=(-0.2, 0.4),
                    ),
                ),
            )
            phase_artifacts.write_filtering_artifact(
                tmp_dir,
                config,
                partition_phase,
                partition_filtering,
                model_path=None,
            )
            partition_filter_artifact = phase_artifacts.read_filtering_artifact(
                tmp_dir,
                config,
            )
            hydrated_partitions = phase_artifacts.hydrate_partitioned_filtering_phase(partition_filter_artifact)
            self.assertIsNone(partition_filter_artifact.model_path)
            self.assertEqual(len(hydrated_partitions.partition_models), 1)
            self.assertEqual(hydrated_partitions.output_gaussians, 1_100_000)
            self.assertEqual(
                hydrated_partitions.partition_models[0].facade_depth_bounds_model,
                (-0.2, 0.4),
            )

            filtered_model_path = Path(tmp_dir, "filtering", "filtered.ply")
            filtered_model_path.parent.mkdir()
            filtered_model_path.write_bytes(b"filtered-ply")
            filtering_phase = types.SimpleNamespace(
                input_gaussians=1_500_000,
                output_gaussians=1_200_000,
                render_state=types.SimpleNamespace(
                    geo_origin=np.array([600_000.0, 4_900_000.0, 120.0]),
                    frame_origin=None,
                    rotation_geo=np.eye(3),
                    sh_direction_rotation=np.eye(3),
                    facade_depth_bounds_model=None,
                    render_extent=(-10.0, 10.0, -5.0, 5.0, 0.0, 8.0),
                    local_gsd=0.025,
                    resolution_units="metres",
                    coverage_camera_positions=np.array([[0.0, 0.0, 10.0], [2.0, 1.0, 11.0]]),
                ),
                density_assessment=GaussianDensityAssessment(
                    robust_ground_area_m2=2_500.0,
                    requested_gsd_m=0.025,
                    target_spacing_pixels=8.0,
                    actual_gaussian_count=1_200_000,
                    required_gaussian_count=62_500,
                    achieved_spacing_m=0.04564,
                    achieved_spacing_pixels=1.8256,
                    minimum_compatible_gsd_m=0.005705,
                    accepted=True,
                ),
            )
            phase_artifacts.write_filtering_artifact(
                tmp_dir,
                config,
                phase,
                filtering_phase,
                model_path=filtered_model_path,
            )
            filtered = phase_artifacts.read_filtering_artifact(tmp_dir, config)
            self.assertEqual(filtered.model_path, filtered_model_path)
            self.assertEqual(filtered.output_gaussians, 1_200_000)
            self.assertEqual(
                filtered.density_assessment,
                filtering_phase.density_assessment,
            )
            self.assertEqual(filtered.render_extent, (-10.0, 10.0, -5.0, 5.0, 0.0, 8.0))
            self.assertEqual(filtered.scene_summary.registered_camera_count, 2)
            loaded_model = types.SimpleNamespace(num_gaussians=1_200_000)
            hydrated = phase_artifacts.hydrate_filtering_phase(filtered, loaded_model)
            self.assertIs(hydrated.render_state.merged_model, loaded_model)
            np.testing.assert_array_equal(
                hydrated.render_state.coverage_camera_positions,
                filtering_phase.render_state.coverage_camera_positions,
            )

    def test_product_verification_requires_matching_canary_manifests(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ortho = os.path.join(tmp_dir, "orthomosaic.tif")
            height = os.path.join(tmp_dir, "height.tif")
            final_ply = os.path.join(tmp_dir, "final.ply")
            checkpoint_dir = os.path.join(tmp_dir, "checkpoint")
            os.makedirs(checkpoint_dir)
            for artifact in (ortho, height, final_ply):
                Path(artifact).write_bytes(b"artifact")
            Path(checkpoint_dir, "trainer_run.json").write_text("{}", encoding="utf-8")
            preparation = types.SimpleNamespace(
                params={"gcp_adjustment_enabled": False},
                facade_mode=False,
                facade_selection_report_path=os.path.join(tmp_dir, "facade.json"),
            )
            rtk_state = types.SimpleNamespace(report_path=os.path.join(tmp_dir, "rtk.json"))
            alignment_state = types.SimpleNamespace(alignment_transform_path=None)
            gaussian_state = types.SimpleNamespace(
                ortho_file=ortho,
                result={
                    "height_file": height,
                    "final_ply": final_ply,
                    "checkpoint_dir": checkpoint_dir,
                },
            )

            with patch.object(publication_stage, "convert_to_cog"):
                with self.assertRaisesRegex(FileNotFoundError, "canary qualification"):
                    publication_stage._verify_product_assets(
                        preparation,
                        rtk_state,
                        alignment_state,
                        gaussian_state,
                        tmp_dir,
                    )
