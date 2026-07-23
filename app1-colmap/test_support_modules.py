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
    rasterio_module = types.ModuleType("rasterio")
    rasterio_module.open = MagicMock()
    sys.modules["rasterio"] = rasterio_module
    rasterio_transform_module = types.ModuleType("rasterio.transform")
    rasterio_transform_module.from_origin = MagicMock()
    sys.modules["rasterio.transform"] = rasterio_transform_module

import main as app1_main
import pipeline_support
import worker_support


def session_context(session):
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = False
    return context


class TestWorkerSupport(unittest.TestCase):
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

    def test_build_mission_context_uses_s3_input_and_contained_work_path(self):
        drives = '[{"name":"system"},{"name":"drive-i"}]'
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
                        "work_drive": "drive-i",
                    }
                )

        self.assertEqual(mission_context.vol_id, "vol-007")
        self.assertEqual(mission_context.input_dir, "datasets/banyuls")
        self.assertEqual(mission_context.work_dir, "/work/drive-i/vol-007")

    def test_publish_next_stage_message_uses_current_contract(self):
        producer = MagicMock()
        producer.flush.return_value = 0

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
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["event_type"], "orthomosaic")
        self.assertEqual(payload["correlation_id"], "vol-3")
        self.assertTrue(payload["event_id"].startswith("orthomosaic:"))
        producer.flush.assert_called_once()

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
        mission = MagicMock(resume_info={})
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
        self.assertEqual(modern["mvs_max_image_size"], "4000")

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


class TestMainSupport(unittest.TestCase):
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
            ]:
                with open(file_path, "w", encoding="utf-8") as handle:
                    handle.write("x")

            with patch.object(app1_main, "report_mission_progress") as report_mock:
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
            report_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
