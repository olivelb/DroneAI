import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

APP2_ROOT = Path(__file__).resolve().parents[1] / "app2-ia"
sys.path.insert(0, str(APP2_ROOT))

from detection_core import (  # noqa: E402
    extract_obb_detections,
    normalize_yolo_model_variant,
    resolve_requested_labels,
)


def test_model_variant_aliases_are_normalized():
    assert normalize_yolo_model_variant("26-n") == "yolo26n"
    assert normalize_yolo_model_variant("tiny") == "yolo26n"
    assert normalize_yolo_model_variant("unknown") == "yolo26l"


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
