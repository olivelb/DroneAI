import sys
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

APP2_ROOT = Path(__file__).resolve().parents[1] / "app2-ia"
sys.path.insert(0, str(APP2_ROOT))

import detection_core  # noqa: E402
from detection_core import (  # noqa: E402
    REQUESTED_CLASS_MAP,
    extract_obb_detections,
    normalize_yolo_model_variant,
    resolve_requested_labels,
    resolve_yolo_model_file,
    run_yolo_detection,
)

from shared.validation import SUPPORTED_AERIAL_CLASSES


def test_model_variant_aliases_are_normalized():
    assert normalize_yolo_model_variant("26-n") == "yolo26n"
    assert normalize_yolo_model_variant("tiny") == "yolo26n"
    assert normalize_yolo_model_variant("unknown") == "yolo26l"


def test_baked_model_is_used_before_populating_the_writable_cache(
    tmp_path,
    monkeypatch,
):
    cache_dir = tmp_path / "cache"
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    baked_checkpoint = baked_dir / "yolo26l-obb.pt"
    baked_checkpoint.write_bytes(b"baked")
    monkeypatch.setattr(detection_core, "YOLO_MODEL_ASSETS_DIR", cache_dir)
    monkeypatch.setattr(detection_core, "YOLO_BAKED_MODEL_DIR", baked_dir)

    variant, model_path, checkpoint = resolve_yolo_model_file("yolo26l")

    assert variant == "yolo26l"
    assert model_path == baked_checkpoint
    assert checkpoint == "yolo26l-obb.pt"


def test_unbaked_model_variant_targets_the_writable_cache(
    tmp_path,
    monkeypatch,
):
    cache_dir = tmp_path / "cache"
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    monkeypatch.setattr(detection_core, "YOLO_MODEL_ASSETS_DIR", cache_dir)
    monkeypatch.setattr(detection_core, "YOLO_BAKED_MODEL_DIR", baked_dir)

    variant, model_path, checkpoint = resolve_yolo_model_file("yolo26n")

    assert variant == "yolo26n"
    assert model_path == cache_dir / "yolo26n-obb.pt"
    assert checkpoint == "yolo26n-obb.pt"


def test_car_request_maps_to_aerial_vehicle_labels():
    available = ["plane", "small vehicle", "large vehicle", "ship"]

    assert resolve_requested_labels(["car"], available) == [
        "small vehicle",
        "large vehicle",
    ]

    assert resolve_requested_labels(
        ["car"],
        ["small-vehicle", "large_vehicle"],
    ) == ["small-vehicle", "large_vehicle"]


def test_api_and_worker_share_the_same_supported_class_contract():
    assert set(REQUESTED_CLASS_MAP) == SUPPORTED_AERIAL_CLASSES


def test_unsupported_request_is_rejected_instead_of_using_vehicle_labels():
    with pytest.raises(ValueError, match="unsupported YOLO aerial classes: person"):
        resolve_requested_labels(
            ["person"],
            ["small vehicle", "large vehicle"],
        )


def test_missing_requested_model_label_is_rejected():
    with pytest.raises(RuntimeError, match="does not expose labels"):
        resolve_requested_labels(
            ["boat"],
            ["small vehicle", "large vehicle"],
        )


def test_obb_extraction_preserves_polygon_and_filters_confidence():
    oriented_boxes = SimpleNamespace(
        xyxyxyxy=np.array(
            [
                [[1, 2], [5, 2], [5, 6], [1, 6]],
                [[10, 12], [14, 12], [14, 16], [10, 16]],
            ],
            dtype=np.float32,
        ),
        cls=np.array([0, 1]),
        conf=np.array([0.85, 0.15]),
    )
    raw_result = SimpleNamespace(
        obb=oriented_boxes,
        names={0: "small vehicle", 1: "large vehicle"},
    )

    detections = extract_obb_detections(
        raw_result,
        ["small vehicle", "large vehicle"],
        min_confidence=0.20,
    )

    assert len(detections) == 1
    assert detections[0]["class_name"] == "small vehicle"
    assert detections[0]["center_x"] == 3
    assert detections[0]["center_y"] == 4


def test_yolo_result_records_weight_hash_runtime_and_inference_parameters(
    tmp_path,
    monkeypatch,
):
    weights = tmp_path / "yolo26l-obb.pt"
    weights.write_bytes(b"verified-weights")

    class FakeModel:
        @staticmethod
        def predict(**_kwargs):
            return []

    monkeypatch.setattr(
        detection_core,
        "load_yolo_model",
        lambda _variant: (
            FakeModel(),
            ["small vehicle", "large vehicle"],
            "yolo26l",
            "cpu",
            weights,
        ),
    )
    detection_core._yolo_model_hashes.clear()

    detections, attempt = run_yolo_detection(
        "tile.jpg",
        ["car"],
        0.35,
        "yolo26l",
    )

    assert detections == []
    manifest = attempt["model_manifest"]
    assert manifest["backend"] == "yolo"
    assert manifest["identity"]["artifact_sha256"] == hashlib.sha256(b"verified-weights").hexdigest()
    assert manifest["runtime"] == {"device": "cpu"}
    assert manifest["inference"]["requested_classes"] == ["car"]
    assert manifest["inference"]["primary_confidence"] == 0.35
