import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

if "confluent_kafka" not in sys.modules:
    kafka_module = types.ModuleType("confluent_kafka")
    kafka_module.Consumer = MagicMock()
    kafka_module.Producer = MagicMock()
    sys.modules["confluent_kafka"] = kafka_module

if "exif" not in sys.modules:
    exif_module = types.ModuleType("exif")
    exif_module.Image = MagicMock()
    sys.modules["exif"] = exif_module

if "rasterio" not in sys.modules:
    rasterio_module = types.ModuleType("rasterio")
    rasterio_module.open = MagicMock()
    sys.modules["rasterio"] = rasterio_module

if "rasterio.transform" not in sys.modules:
    rasterio_transform_module = types.ModuleType("rasterio.transform")
    rasterio_transform_module.from_origin = MagicMock()
    sys.modules["rasterio.transform"] = rasterio_transform_module

import runtime_support
import pipeline_support
import worker_support
import main as app1_main


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

    def test_build_mission_context_normalizes_host_paths(self):
        mission_context = worker_support.build_mission_context(
            {
                "vol_id": "vol-7",
                "workspace_dir": "/tmp/workspaces",
                "input_dir": "data/input",
                "pipeline": "modern",
            }
        )

        self.assertEqual(mission_context.vol_id, "vol-7")
        self.assertEqual(mission_context.input_dir, "/host/data/input")
        self.assertEqual(mission_context.work_dir, "/host/tmp/workspaces/vol-7")

    def test_publish_next_stage_message_uses_normalized_backend(self):
        producer = MagicMock()

        worker_support.publish_next_stage_message(
            producer,
            "topic-out",
            "vol-3",
            "/tmp/orthomosaic.tif",
            {"ai_backend": "sam-3", "classes": ["truck"], "ai_confidence": 0.8, "sam_prompt": "vehicle"},
            lambda value: "sam3" if value == "sam-3" else value,
        )

        kwargs = producer.produce.call_args.kwargs
        self.assertEqual(kwargs["key"], "vol-3")
        self.assertEqual(kwargs["value"], '{"vol_id": "vol-3", "ortho_path": "/tmp/orthomosaic.tif", "classes": ["truck"], "ai_confidence": 0.8, "ai_backend": "sam3", "sam_prompt": "vehicle"}')
        producer.flush.assert_called_once()

    def test_mission_state_tracker_writes_state_and_history(self):
        tracker = worker_support.MissionStateTracker()

        with tempfile.TemporaryDirectory() as tmp_dir:
            mission_context = worker_support.MissionContext(
                mission={"pipeline": "modern"},
                vol_id="vol-11",
                input_dir="/host/data/in",
                work_dir=tmp_dir,
            )

            tracker.start_mission(mission_context)
            tracker.record_progress(
                "vol-11",
                "COPYING_IMAGES",
                5,
                log="Processed 10/20 images",
                details={
                    "event": "copy_progress",
                    "processed": 10,
                    "total": 20,
                    "copied": 8,
                    "skipped": 2,
                },
            )
            tracker.record_progress(
                "vol-11",
                "MATCHING",
                30,
                log="Executing matcher",
                details={"event": "command_started", "command": ["colmap", "spatial_matcher"]},
            )
            tracker.record_progress(
                "vol-11",
                "DONE",
                100,
                status="success",
                log="Pipeline complete!",
                details={"event": "command_finished", "command": ["colmap", "spatial_matcher"], "return_code": 0},
            )

            with open(os.path.join(tmp_dir, "mission_state.json"), "r", encoding="utf-8") as handle:
                state = json.load(handle)

            self.assertEqual(state["vol_id"], "vol-11")
            self.assertEqual(state["step"], "DONE")
            self.assertEqual(state["status"], "success")
            self.assertEqual(state["copy_progress"]["processed"], 10)
            self.assertIsNone(state["current_command"])
            self.assertEqual(state["last_command"]["event"], "command_finished")

            with open(os.path.join(tmp_dir, "mission_state_history.jsonl"), "r", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]

            self.assertEqual(events[0]["event"], "mission_started")
            self.assertEqual(events[-1]["status"], "success")

    def test_mission_state_tracker_preserves_previous_state_for_resume(self):
        tracker = worker_support.MissionStateTracker()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(os.path.join(tmp_dir, "mission_state.json"), "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "vol_id": "vol-12",
                        "status": "processing",
                        "step": "FUSION",
                        "progress": 90,
                        "updated_at": "2026-03-26T00:00:00+00:00",
                        "last_log": "Fusion chunk 2/3 still pending",
                    },
                    handle,
                )

            mission_context = worker_support.MissionContext(
                mission={"pipeline": "modern"},
                vol_id="vol-12",
                input_dir="/host/data/in",
                work_dir=tmp_dir,
            )

            previous_state = tracker.start_mission(mission_context)

            self.assertEqual(previous_state["step"], "FUSION")

            with open(os.path.join(tmp_dir, "mission_state.json"), "r", encoding="utf-8") as handle:
                state = json.load(handle)

            self.assertEqual(state["resume_info"]["resumed_from"]["step"], "FUSION")
            self.assertEqual(state["resume_info"]["resumed_from"]["progress"], 90)


class TestRuntimeSupport(unittest.TestCase):
    def test_build_fusion_chunks_splits_at_target_size(self):
        entries = ["img1.jpg", "img2.jpg", "img3.jpg"]
        with patch.object(runtime_support, "estimate_fusion_entry_bytes", side_effect=[3 * 1024**3, 3 * 1024**3, 1 * 1024**3]):
            chunks = runtime_support.build_fusion_chunks("/tmp/dense", entries, 4000, 4)

        self.assertEqual(chunks, [["img1.jpg"], ["img2.jpg", "img3.jpg"]])

    def test_run_chunked_fusion_single_chunk_delegates_to_run_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            stereo_dir = os.path.join(tmp_dir, "stereo")
            os.makedirs(stereo_dir, exist_ok=True)
            fusion_cfg_path = os.path.join(stereo_dir, "fusion.cfg")
            with open(fusion_cfg_path, "w", encoding="utf-8") as handle:
                handle.write("img1.jpg\nimg2.jpg\n")

            report_fn = MagicMock()
            run_command_fn = MagicMock()
            params = {"fusion_chunk_target_memory_gib": "16", "fusion_max_image_size": "4000", "fusion_cache_size": "32"}

            with patch.object(runtime_support, "build_fusion_chunks", return_value=[["img1.jpg", "img2.jpg"]]):
                runtime_support.run_chunked_fusion(tmp_dir, os.path.join(tmp_dir, "fused.ply"), "vol-9", params, 4, report_fn, run_command_fn)

        run_command_fn.assert_called_once()
        command = run_command_fn.call_args.args[0]
        self.assertEqual(command[0:2], ["colmap", "stereo_fusion"])
        report_fn.assert_not_called()


class TestPipelineSupport(unittest.TestCase):
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