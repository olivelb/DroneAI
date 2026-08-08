import importlib
import json
import sys
from pathlib import Path

import numpy as np


APP2_ROOT = Path(__file__).resolve().parents[1] / "app2-ia"
if str(APP2_ROOT) not in sys.path:
    sys.path.insert(0, str(APP2_ROOT))

sam3_backend = importlib.import_module("sam3_backend")
tile_workflow = importlib.import_module("tile_detection_workflow")


class FakeProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, *, key, value):
        self.messages.append((topic, key, json.loads(value)))

    @staticmethod
    def flush():
        return 0


class FakeCancellationRegistry:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled
        self.cleared = []

    def is_cancelled(self, *_args):
        return self.cancelled

    def clear(self, *args):
        self.cleared.append(args)


class FakeSam3Backend:
    @staticmethod
    def resolve_prompt(_tile_info):
        return "vehicle"

    @staticmethod
    def run(_tile_path, prompt, requested_confidence):
        return (
            [
                {
                    "polygon": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0]],
                    "center_x": 2.0,
                    "center_y": 3.0,
                    "confidence": requested_confidence,
                    "class_id": 0,
                    "class_name": prompt,
                }
            ],
            {
                "label": "fake SAM3",
                "model_manifest": {"backend": "sam3"},
            },
        )


def _workflow(tmp_path, *, producer=None, cancellation=None, progress=None):
    return tile_workflow.TileDetectionWorkflow(
        producer=producer or FakeProducer(),
        output_topic="tile-detections",
        cancellation_registry=cancellation or FakeCancellationRegistry(),
        progress_reporter=progress or (lambda *_args, **_kwargs: None),
        sam3_backend=FakeSam3Backend(),
        logger=importlib.import_module("logging").getLogger("test"),
        workspace_root=tmp_path,
    )


def test_coordinate_helpers_preserve_global_pixel_geometry():
    assert tile_workflow.translate_segment(
        [[1.0, 2.0], [3.0, 4.0]],
        10.0,
        20.0,
    ) == [[11.0, 22.0], [13.0, 24.0]]

    class Transformer:
        @staticmethod
        def transform(x, y):
            return x + 100.0, y + 200.0

    assert tile_workflow.transform_detection_coordinates(
        [5.0, 2.0, 0.0, 7.0, 0.0, 3.0],
        Transformer(),
        4.0,
        6.0,
    ) == (113.0, 225.0)


def test_sam_contour_falls_back_to_detector_box_for_empty_mask():
    fallback = [[1.0, 2.0], [5.0, 2.0], [5.0, 6.0], [1.0, 6.0]]

    polygon, center_x, center_y = sam3_backend.Sam3Backend.contour_to_polygon(
        np.zeros((8, 8), dtype=np.uint8),
        fallback,
    )

    assert polygon == fallback
    assert (center_x, center_y) == (3.0, 4.0)


def test_sam_backend_construction_keeps_heavy_runtime_lazy(monkeypatch):
    imported = []
    original_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name in {"torch", "transformers", "huggingface_hub"}:
            imported.append(name)
            raise AssertionError(f"unexpected eager import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)
    backend = sam3_backend.Sam3Backend(
        model_revision="3c879f39826c281e95690f02c7821c4de09afae7",
    )

    assert backend.device_type is None
    assert imported == []


def test_workflow_downloads_detects_and_publishes_one_tile(
    tmp_path,
    monkeypatch,
):
    producer = FakeProducer()
    progress = []
    workflow = _workflow(
        tmp_path,
        producer=producer,
        progress=lambda *args, **kwargs: progress.append((args, kwargs)),
    )

    def download(_key, destination):
        Path(destination).write_bytes(b"jpeg")

    monkeypatch.setattr(tile_workflow.storage, "download_file", download)
    workflow.process_tile(
        {
            "vol_id": "mission-1",
            "analysis_run_id": "run-1",
            "attempt": 2,
            "tile_index": 4,
            "tile_s3_key": "missions/mission-1/tiles/tile_4.jpg",
            "offset_x": 10,
            "offset_y": 20,
            "total_tiles": 0,
            "ai_backend": "sam3",
            "ai_confidence": 0.45,
            "correlation_id": "run-1",
            "event_id": "source-event",
        }
    )

    assert len(producer.messages) == 1
    topic, key, event = producer.messages[0]
    assert (topic, key, event["event_type"]) == (
        "tile-detections",
        "mission-1:run-1:tile:4",
        "tile_detection",
    )
    detection = event["detections"][0]
    assert detection["global_pixel_x"] == 12.0
    assert detection["global_pixel_y"] == 23.0
    assert detection["segment"] == [
        [11.0, 22.0],
        [13.0, 22.0],
        [13.0, 24.0],
    ]
    assert progress[0][0][1] == "DETECTING"


def test_cancelled_tile_is_not_downloaded_or_published(
    tmp_path,
    monkeypatch,
):
    producer = FakeProducer()
    workflow = _workflow(
        tmp_path,
        producer=producer,
        cancellation=FakeCancellationRegistry(cancelled=True),
    )
    monkeypatch.setattr(
        tile_workflow.storage,
        "download_file",
        lambda *_args: (_ for _ in ()).throw(AssertionError("download called")),
    )

    workflow.process_tile(
        {
            "vol_id": "mission-1",
            "tile_index": 0,
            "offset_x": 0,
            "offset_y": 0,
        }
    )

    assert producer.messages == []
