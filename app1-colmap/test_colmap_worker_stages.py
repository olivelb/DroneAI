import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


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
from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
)
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
        profile = quality_profile("high-quality-v1")
        config, warnings = dronegs_config.resolve_dronegs_config(
            profile.parameters,
            facade_mode=False,
            data_factor=int(profile.parameters["gs_data_factor"]),
        )

        self.assertEqual(config.profile_id, "high-quality-v1")
        self.assertEqual(config.iterations, 30_000)
        self.assertEqual(config.cap_max, 5_000_000)
        self.assertEqual(warnings, ())

        overridden, warnings = dronegs_config.resolve_dronegs_config(
            {**profile.parameters, "gs_cap_max": "4500000"},
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
                patch.object(gaussian_stage.storage, "upload_file", side_effect=OSError("offline")),
                patch.object(worker_runtime, "report_mission_progress") as report,
            ):
                callback(checkpoint, 10)

        self.assertIn("remains locally durable", report.call_args.kwargs["log"])

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
