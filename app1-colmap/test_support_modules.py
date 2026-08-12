import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

try:
    import confluent_kafka  # noqa: F401
except ImportError:
    kafka_module = types.ModuleType("confluent_kafka")
    kafka_module.Consumer = MagicMock()
    kafka_module.Producer = MagicMock()
    sys.modules["confluent_kafka"] = kafka_module

try:
    import rasterio  # noqa: F401
    import rasterio.transform  # noqa: F401
except ImportError:
    previous_rasterio_modules = {name: sys.modules.get(name) for name in ("rasterio", "rasterio.transform")}
    rasterio_module = types.ModuleType("rasterio")
    rasterio_module.open = MagicMock()
    sys.modules["rasterio"] = rasterio_module
    rasterio_transform_module = types.ModuleType("rasterio.transform")
    rasterio_transform_module.from_origin = MagicMock()
    sys.modules["rasterio.transform"] = rasterio_transform_module
else:
    previous_rasterio_modules = None

import main as app1_main
import pipeline_support
import worker_support
from colmap_worker import mission_runner
from colmap_worker import runtime as worker_runtime
from colmap_worker.stages import gaussian as gaussian_stage
from colmap_worker.stages import reconstruction as reconstruction_stage

if previous_rasterio_modules is not None:
    for module_name, previous_module in previous_rasterio_modules.items():
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module


def session_context(session):
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = False
    return context


class TestWorkerSupport(unittest.TestCase):
    def test_colmap_consumer_replays_uncommitted_initial_work(self):
        consumer = MagicMock()
        with patch.object(worker_support, "Consumer", return_value=consumer) as factory:
            result = worker_support.create_consumer("kafka:9092", "vols-bruts")

        self.assertIs(result, consumer)
        config = factory.call_args.args[0]
        self.assertEqual(config["auto.offset.reset"], "earliest")
        self.assertFalse(config["enable.auto.commit"])
        self.assertFalse(config["enable.auto.offset.store"])
        consumer.subscribe.assert_called_once_with(["vols-bruts"])

    def test_worker_cancellation_state_lifecycle(self):
        state = worker_support.WorkerCancellationState()

        state.start_mission("vol-1")
        self.assertTrue(state.should_cancel("vol-1"))
        self.assertFalse(state.should_cancel("vol-2"))

        state.on_cancel("vol-1")
        with self.assertRaisesRegex(RuntimeError, "Mission cancelled by user"):
            state.ensure_not_cancelled()

        state.clear()
        state.ensure_not_cancelled()
        self.assertFalse(state.should_cancel("vol-1"))
    def test_runtime_dependencies_are_lazy_and_explicit(self):
        worker_runtime.reset_worker_runtime()
        self.addCleanup(worker_runtime.reset_worker_runtime)

        with self.assertRaisesRegex(RuntimeError, "runtime is not configured"):
            worker_runtime.require_producer()

        reporter = MagicMock()
        producer = object()
        worker_runtime.configure_worker_runtime(producer, reporter)

        self.assertIs(worker_runtime.require_producer(), producer)
        with patch.object(worker_runtime.mission_state_tracker, "record_progress") as record:
            worker_runtime.report_mission_progress(
                "vol-runtime",
                "TESTING",
                42,
                details={"source": "unit-test"},
            )

        record.assert_called_once_with(
            "vol-runtime",
            "TESTING",
            42,
            status="processing",
            log=None,
            details={"source": "unit-test"},
        )
        reporter.assert_called_once_with(
            "vol-runtime",
            "TESTING",
            42,
            status="processing",
            log=None,
            details={"source": "unit-test"},
        )

    def test_build_mission_context_uses_s3_input_and_contained_work_path(self):
        drives = '[{"name":"system"},{"name":"fast-storage"}]'
        with patch.dict(
            os.environ,
            {"WORK_DRIVES": drives, "WORK_DRIVE_DEFAULT": "system"},
            clear=False,
        ):
            with patch("pathlib.Path.is_dir", return_value=True):
                mission_context = worker_support.build_mission_context(
                    {
                        "vol_id": "vol-007",
                        "input_dataset": "datasets/banyuls",
                        "pipeline": "modern",
                        "work_drive": "fast-storage",
                    }
                )

        self.assertEqual(mission_context.vol_id, "vol-007")
        self.assertEqual(mission_context.input_dir, "datasets/banyuls")
        self.assertEqual(mission_context.work_dir, "/work/fast-storage/vol-007")

    def test_build_mission_context_rejects_disappeared_work_drive(self):
        drives = '[{"name":"fast-storage"}]'
        with patch.dict(
            os.environ,
            {"WORK_DRIVES": drives, "WORK_DRIVE_DEFAULT": "fast-storage"},
            clear=False,
        ):
            with patch("pathlib.Path.is_dir", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "is not mounted"):
                    worker_support.build_mission_context(
                        {
                            "vol_id": "vol-008",
                            "input_dataset": "datasets/banyuls",
                        }
                    )

    def test_publish_next_stage_message_uses_current_contract(self):
        producer = MagicMock()

        def confirm_delivery(_timeout):
            producer.produce.call_args.kwargs["on_delivery"](None, None)
            return 0

        producer.poll.side_effect = confirm_delivery

        worker_support.publish_next_stage_message(
            producer,
            "topic-out",
            "vol-3",
            "missions/vol-3/orthomosaic.tif",
            {
                "ai_backend": "sam-3",
                "classes": ["truck"],
                "ai_confidence": 0.8,
                "sam_prompt": "vehicle",
                "tile_size": 2048,
                "attempt": 3,
            },
            lambda value: "sam3" if value == "sam-3" else value,
        )

        kwargs = producer.produce.call_args.kwargs
        payload = json.loads(kwargs["value"])
        self.assertEqual(kwargs["key"], "vol-3")
        self.assertEqual(payload["vol_id"], "vol-3")
        self.assertEqual(payload["ortho_s3_key"], "missions/vol-3/orthomosaic.tif")
        self.assertEqual(payload["classes"], ["truck"])
        self.assertEqual(payload["ai_confidence"], 0.8)
        self.assertEqual(payload["ai_backend"], "sam3")
        self.assertEqual(payload["ai_model_variant"], "yolo26l")
        self.assertEqual(payload["sam_prompt"], "vehicle")
        self.assertEqual(payload["tile_size"], 2048)
        self.assertEqual(payload["attempt"], 3)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["event_type"], "orthomosaic")
        self.assertEqual(payload["correlation_id"], "vol-3")
        self.assertTrue(payload["event_id"].startswith("orthomosaic:"))
        producer.poll.assert_called_once()
        producer.flush.assert_not_called()

    def test_mission_state_tracker_loads_database_state(self):
        mission = MagicMock(
            vol_id="vol-11",
            status="processing",
            current_step="MATCHING",
            progress=30,
            updated_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
            error_message=None,
            params={"pipeline": "modern"},
            resume_info={"copy_progress": {"processed": 10}},
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = mission

        with patch.object(worker_support, "get_session", return_value=session_context(session)):
            state = worker_support.MissionStateTracker().load_state("vol-11")

        self.assertEqual(state["vol_id"], "vol-11")
        self.assertEqual(state["step"], "MATCHING")
        self.assertEqual(state["resume_info"]["copy_progress"]["processed"], 10)

    def test_mission_state_tracker_preserves_resume_metadata(self):
        previous_state = {
            "status": "processing",
            "step": "FUSION",
            "progress": 90,
            "updated_at": "2026-03-26T00:00:00+00:00",
            "last_log": "Fusion interrupted",
        }
        mission = MagicMock(
            resume_info={},
            vol_id="vol-012",
            organization_id="legacy-unassigned",
            workspace_prefix="missions/vol-012",
        )
        tracker = worker_support.MissionStateTracker()
        mission_context = worker_support.MissionContext(
            mission={
                "pipeline": "modern",
                "input_dataset": "datasets/banyuls",
            },
            vol_id="vol-012",
            input_dir="datasets/banyuls",
            work_dir="/work/system/vol-012",
        )

        with patch.object(tracker, "load_state", return_value=previous_state):
            with patch.object(worker_support, "get_session", return_value=session_context(MagicMock())):
                with patch.object(worker_support, "get_or_create_mission", return_value=mission):
                    result = tracker.start_mission(mission_context)

        self.assertEqual(result, previous_state)
        self.assertEqual(mission.status, "processing")
        self.assertEqual(mission.current_step, "STARTING")
        self.assertEqual(mission.resume_info["resumed_from"]["step"], "FUSION")


class TestPipelineSupport(unittest.TestCase):
    def test_merge_pipeline_params_has_expected_defaults(self):
        legacy = pipeline_support.merge_pipeline_params("legacy", {})
        modern = pipeline_support.merge_pipeline_params("modern", {})

        self.assertEqual(legacy["mvs_max_image_size"], "4000")
        self.assertEqual(modern["feature_max_image_size"], "2400")
        self.assertEqual(modern["feature_max_num_features"], "4096")
        self.assertEqual(modern["sift_first_octave"], "-1")
        self.assertFalse(modern["guided_matching"])
        self.assertEqual(modern["global_mapper_ba_iterations"], "2")
        self.assertFalse(modern["global_mapper_skip_retriangulation"])
        self.assertEqual(modern["global_mapper_random_seed"], "42")
        self.assertEqual(
            modern["global_mapper_tri_complete_max_reproj_error"],
            "15.0",
        )
        self.assertEqual(modern["mvs_max_image_size"], "2400")
        self.assertFalse(legacy["rtk_refinement_enabled"])
        self.assertTrue(modern["rtk_refinement_enabled"])
        self.assertEqual(modern["rtk_refinement_iterations"], "25")
        self.assertEqual(modern["rtk_refinement_loss_scale"], "7.82")

    def test_plan_clean_image_copy_skips_when_manifest_matches_hash(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_path = os.path.join(tmp_dir, "source.jpg")
            dst_path = os.path.join(tmp_dir, "dest.jpg")

            with open(src_path, "wb") as handle:
                handle.write(b"original-image-payload")
            with open(dst_path, "wb") as handle:
                handle.write(b"sanitized-image-payload-with-different-size")

            source_hash = pipeline_support.compute_file_sha256(src_path)
            manifest_entry = {
                "size": 1,
                "mtime_ns": 1,
                "sha256": source_hash,
            }

            needs_copy, descriptor = pipeline_support.plan_clean_image_copy(src_path, dst_path, manifest_entry)

            self.assertFalse(needs_copy)
            self.assertEqual(descriptor["sha256"], source_hash)
            self.assertEqual(descriptor["size"], os.path.getsize(src_path))

    def test_copy_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = {
                "image1.jpg": {
                    "size": 123,
                    "mtime_ns": 456,
                    "sha256": "abc",
                }
            }

            pipeline_support.save_copy_manifest(tmp_dir, manifest)

            self.assertEqual(pipeline_support.load_copy_manifest(tmp_dir), manifest)

    def test_colmap_cache_fingerprint_tracks_reconstruction_parameters(self):
        baseline_params = pipeline_support.merge_pipeline_params("modern", {})
        baseline = pipeline_support.build_colmap_cache_config(baseline_params)
        changed = pipeline_support.build_colmap_cache_config(
            {
                **baseline_params,
                "feature_max_image_size": "4096",
                "feature_max_num_features": "8192",
            }
        )

        self.assertNotEqual(baseline["fingerprint"], changed["fingerprint"])
        self.assertEqual(
            pipeline_support.changed_colmap_cache_parameters(baseline, changed),
            ["feature_max_image_size", "feature_max_num_features"],
        )
        changed_rtk_scale = pipeline_support.build_colmap_cache_config(
            {
                **baseline_params,
                "rtk_refinement_loss_scale": "62.56",
            }
        )
        self.assertNotEqual(
            baseline["fingerprint"],
            changed_rtk_scale["fingerprint"],
        )
        self.assertEqual(
            pipeline_support.changed_colmap_cache_parameters(
                baseline,
                changed_rtk_scale,
            ),
            ["rtk_refinement_loss_scale"],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            pipeline_support.save_colmap_cache_config(tmp_dir, changed)
            self.assertEqual(
                pipeline_support.load_colmap_cache_config(tmp_dir),
                changed,
            )


class TestMainSupport(unittest.TestCase):
    def test_feature_extraction_command_preserves_aliked_safety_clamp(self):
        preparation = types.SimpleNamespace(
            params={
                "feature_num_threads": "4",
                "feature_max_image_size": "4096",
                "feature_type": "ALIKED_N32",
                "feature_max_num_features": "8192",
            },
            db_path="/tmp/database.db",
            clean_images_dir="/tmp/images",
            image_reader_camera_model="SIMPLE_RADIAL",
            image_reader_camera_params=None,
            feature_family="ALIKED",
            feature_type="ALIKED_N32",
            feature_gpu_index="0",
        )

        with (
            patch.dict(
                os.environ,
                {
                    "ALIKED_SAFE_MAX_IMAGE_SIZE": "1600",
                    "COLMAP_MODEL_DIR": "/models",
                },
                clear=False,
            ),
            patch.object(worker_runtime, "report_mission_progress") as report,
        ):
            command = reconstruction_stage._build_feature_extraction_command(
                preparation,
                "vol-aliked",
            )

        max_size_index = command.index("--FeatureExtraction.max_image_size")
        self.assertEqual(command[max_size_index + 1], "1600")
        self.assertIn("/models/aliked-n32.onnx", command)
        report.assert_called_once()

    def test_matching_command_keeps_selected_sequential_strategy(self):
        preparation = types.SimpleNamespace(
            params={
                "matching_strategy": "sequential",
                "guided_matching": True,
                "feature_max_num_matches": "32768",
            },
            db_path="/tmp/database.db",
            geo_data_file="/tmp/geo_data.txt",
            resolved_matcher_type="SIFT_LIGHTGLUE",
            feature_gpu_index="0",
        )

        with patch.dict(os.environ, {"COLMAP_MODEL_DIR": "/models"}, clear=False):
            command, model_options, strategy = reconstruction_stage._build_matching_command(
                preparation,
                "/tmp/workspace",
                "vol-sequential",
                gps_done=False,
            )

        self.assertEqual(command[:2], ["colmap", "sequential_matcher"])
        self.assertEqual(strategy, "sequential")
        self.assertEqual(
            model_options,
            ["--SiftMatching.lightglue_model_path", "/models/sift-lightglue.onnx"],
        )
        guided_index = command.index("--FeatureMatching.guided_matching")
        self.assertEqual(command[guided_index + 1], "1")

    def test_auto_dronegs_data_factor_uses_source_resolution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = os.path.join(tmp_dir, "images")
            os.makedirs(images_dir)
            gaussian_stage.PILImage.new("RGB", (640, 480)).save(
                os.path.join(images_dir, "frame.jpg")
            )
            with (
                patch.object(
                    gaussian_stage,
                    "choose_dronegs_data_factor",
                    return_value=2,
                ) as choose_factor,
                patch.object(worker_runtime, "report_mission_progress") as report,
            ):
                data_factor = gaussian_stage._resolve_data_factor(
                    {"gs_data_factor": "auto", "gs_max_width": 320},
                    tmp_dir,
                    "vol-gaussian",
                )

        self.assertEqual(data_factor, 2)
        choose_factor.assert_called_once_with(640, 320)
        report.assert_called_once()

    def test_prepare_sparse_bootstrap_reuses_facade_dense_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dense_path = os.path.join(tmp_dir, "dense")
            dense_sparse_path = os.path.join(dense_path, "sparse")
            os.makedirs(dense_sparse_path)
            os.makedirs(os.path.join(dense_path, "images"))
            for filename in ("cameras.bin", "images.bin", "points3D.bin"):
                with open(
                    os.path.join(dense_sparse_path, filename),
                    "wb",
                ) as handle:
                    handle.write(b"model")

            preparation = types.SimpleNamespace(
                facade_mode=True,
                clean_images_dir=os.path.join(tmp_dir, "images"),
                geo_data_file=os.path.join(tmp_dir, "geo_data.txt"),
                dense_path=dense_path,
                projected_crs_mode="auto_utm",
                requested_projected_crs="",
            )
            with (
                patch.object(reconstruction_stage, "sanitize_exif_for_colmap"),
                patch.object(worker_runtime, "report_mission_progress"),
            ):
                state = app1_main.prepare_sparse_bootstrap(
                    preparation,
                    tmp_dir,
                    "vol-facade-cache",
                )

        self.assertIsNone(state.utm_crs)
        self.assertIsNone(state.alignment_transform_path)
        self.assertTrue(state.ortho_only_ready)
        self.assertFalse(state.gps_done)

    def test_undistort_and_align_facade_returns_local_frame(self):
        preparation = types.SimpleNamespace(
            params={},
            facade_mode=True,
            clean_images_dir="/tmp/images",
            dense_path="/tmp/dense",
            geo_data_file="/tmp/geo_data.txt",
            gcp_path=None,
            gcp_accuracy_path=None,
        )
        reconstruction = types.SimpleNamespace(
            utm_crs=None,
            alignment_transform_path="/tmp/stale-alignment.json",
        )
        rtk_state = types.SimpleNamespace(
            ortho_only_ready=True,
            active_sparse_model_path="/tmp/sparse/0",
        )

        with patch.object(worker_runtime, "report_mission_progress"):
            state = app1_main.undistort_and_align_colmap(
                preparation,
                reconstruction,
                rtk_state,
                "/tmp/workspace",
                "vol-facade",
            )

        self.assertIsNone(state.alignment_transform_path)

    def test_run_colmap_pipeline_cancellation_still_cleans_workspace(self):
        cleanup = MagicMock(return_value=True)
        with (
            patch.object(
                mission_runner,
                "prepare_colmap_pipeline_run",
                side_effect=worker_runtime.PipelineCancelledError("cancelled"),
            ),
            patch.object(worker_runtime, "report_mission_progress") as report,
            patch.object(
                mission_runner,
                "cleanup_pipeline_workspace",
                cleanup,
            ),
        ):
            mission_runner.run_colmap_pipeline(
                "/tmp/cancelled-workspace",
                "datasets/mission",
                "vol-cancelled",
                {},
            )

        report.assert_called_once_with(
            "vol-cancelled",
            "CANCELLED",
            0,
            status="cancelled",
            log="🚫 cancelled",
            details={
                "event": "mission_cancelled",
                "terminal": True,
                "workspace_cleanup_succeeded": True,
            },
        )
        cleanup.assert_called_once_with(
            "/tmp/cancelled-workspace",
            "vol-cancelled",
            final_pass=True,
        )

    def test_run_colmap_pipeline_preserves_modular_stage_order(self):
        calls = []
        states = {
            "prepare": types.SimpleNamespace(facade_mode=False),
            "reconstruct": types.SimpleNamespace(),
            "rtk": types.SimpleNamespace(),
            "align": types.SimpleNamespace(),
            "gaussian": types.SimpleNamespace(),
            "publish": types.SimpleNamespace(),
        }

        def stage(name):
            def run(*_args, **_kwargs):
                calls.append(name)
                return states.get(name)

            return run

        with (
            patch.object(
                mission_runner,
                "prepare_colmap_pipeline_run",
                side_effect=stage("prepare"),
            ),
            patch.object(
                mission_runner,
                "reconstruct_colmap_sparse",
                side_effect=stage("reconstruct"),
            ),
            patch.object(
                mission_runner,
                "refine_colmap_rtk",
                side_effect=stage("rtk"),
            ),
            patch.object(
                mission_runner,
                "undistort_and_align_colmap",
                side_effect=stage("align"),
            ),
            patch.object(
                mission_runner,
                "run_gaussian_product",
                side_effect=stage("gaussian"),
            ),
            patch.object(
                mission_runner,
                "publish_colmap_products",
                side_effect=stage("publish"),
            ),
            patch.object(
                mission_runner,
                "complete_colmap_pipeline",
                side_effect=stage("complete"),
            ),
            patch.object(
                mission_runner,
                "cleanup_pipeline_workspace",
                side_effect=stage("cleanup"),
            ),
        ):
            mission_runner.run_colmap_pipeline(
                "/tmp/mission-workspace",
                "datasets/mission",
                "vol-stage-order",
                {"pipeline": "modern"},
            )

        self.assertEqual(
            calls,
            [
                "prepare",
                "reconstruct",
                "rtk",
                "align",
                "gaussian",
                "publish",
                "complete",
                "cleanup",
            ],
        )

    def test_dense_sparse_model_ready_requires_all_dense_sparse_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dense_path = os.path.join(tmp_dir, "dense")
            sparse_dir = os.path.join(dense_path, "sparse")
            os.makedirs(sparse_dir, exist_ok=True)

            self.assertFalse(app1_main.dense_sparse_model_ready(dense_path))

            for filename in ["cameras.bin", "images.bin", "points3D.bin"]:
                with open(os.path.join(sparse_dir, filename), "wb") as handle:
                    handle.write(b"x")

            self.assertTrue(app1_main.dense_sparse_model_ready(dense_path))

    def test_normalize_gpu_index_defaults_and_clamps_single_visible_device(self):
        with patch.dict(os.environ, {}, clear=False):
            self.assertEqual(app1_main.normalize_gpu_index(None), "0")
            self.assertEqual(app1_main.normalize_gpu_index("-1"), "0")

        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "1"}, clear=False):
            self.assertEqual(app1_main.normalize_gpu_index("1"), "0")
            self.assertEqual(app1_main.normalize_gpu_index("3"), "0")

        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0,1"}, clear=False):
            self.assertEqual(app1_main.normalize_gpu_index("1"), "1")

    def test_invalidate_pipeline_artifacts_removes_cached_outputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            clean_images_dir = os.path.join(tmp_dir, "clean_images")
            sparse_path = os.path.join(tmp_dir, "sparse")
            dense_path = os.path.join(tmp_dir, "dense")
            geo_data_file = os.path.join(tmp_dir, "geo_data.txt")
            db_path = os.path.join(tmp_dir, "database.db")

            os.makedirs(clean_images_dir, exist_ok=True)
            os.makedirs(sparse_path, exist_ok=True)
            os.makedirs(dense_path, exist_ok=True)
            os.makedirs(os.path.join(tmp_dir, "sparse_geo"), exist_ok=True)

            for file_path in [
                db_path,
                f"{db_path}-shm",
                f"{db_path}-wal",
                geo_data_file,
                f"{geo_data_file}.crs",
                os.path.join(clean_images_dir, ".colmap_exif_sanitized"),
                os.path.join(tmp_dir, "alignment_transform.json"),
                os.path.join(tmp_dir, "orthomosaic.tif"),
                os.path.join(tmp_dir, ".colmap_pipeline_config.json"),
            ]:
                with open(file_path, "w", encoding="utf-8") as handle:
                    handle.write("x")

            with patch.object(worker_runtime, "report_mission_progress") as report_mock:
                removed_paths = app1_main.invalidate_pipeline_artifacts(
                    clean_images_dir,
                    tmp_dir,
                    db_path,
                    sparse_path,
                    dense_path,
                    geo_data_file,
                    "vol-13",
                    "Cache invalidation test.",
                )

            self.assertGreaterEqual(len(removed_paths), 10)
            self.assertFalse(os.path.exists(db_path))
            self.assertFalse(os.path.exists(f"{db_path}-shm"))
            self.assertFalse(os.path.exists(f"{db_path}-wal"))
            self.assertFalse(os.path.exists(sparse_path))
            self.assertFalse(os.path.exists(dense_path))
            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "sparse_geo")))
            self.assertFalse(os.path.exists(geo_data_file))
            self.assertFalse(os.path.exists(f"{geo_data_file}.crs"))
            self.assertFalse(os.path.exists(os.path.join(clean_images_dir, ".colmap_exif_sanitized")))
            self.assertFalse(os.path.exists(os.path.join(tmp_dir, ".colmap_pipeline_config.json")))
            report_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
