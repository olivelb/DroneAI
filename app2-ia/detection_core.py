"""Reusable YOLO OBB detection helpers.

The Kafka worker and the infrastructure-free local runner both use this
module. Heavy Ultralytics and Torch imports stay lazy so geometry and label
handling remain unit-testable in the lightweight development environment.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from shared.validation import validate_aerial_class_names


logger = logging.getLogger("app2-ia.detection-core")

YOLO_MODEL_ASSETS_DIR = Path(os.getenv("AERIAL_MODEL_DIR", "/opt/modelzoo"))
YOLO_BAKED_MODEL_DIR = Path(os.getenv("AERIAL_BAKED_MODEL_DIR", "/opt/modelzoo"))
YOLO_MODEL_IMAGE_SIZE = int(os.getenv("AERIAL_MODEL_IMGSZ", "1024"))
YOLO_MODEL_RELEASE = os.getenv("AERIAL_MODEL_RELEASE", "v8.4.0")

YOLO_MODEL_VARIANTS = {
    "yolo26l": {"checkpoint": "yolo26l-obb.pt"},
    "yolo26m": {"checkpoint": "yolo26m-obb.pt"},
    "yolo26s": {"checkpoint": "yolo26s-obb.pt"},
    "yolo26n": {"checkpoint": "yolo26n-obb.pt"},
    "yolo11l": {"checkpoint": "yolo11l-obb.pt"},
    "yolo11m": {"checkpoint": "yolo11m-obb.pt"},
    "yolo11s": {"checkpoint": "yolo11s-obb.pt"},
    "yolo11n": {"checkpoint": "yolo11n-obb.pt"},
}

YOLO_MODEL_ALIASES = {
    "best": "yolo26l",
    "l": "yolo26l",
    "m": "yolo26m",
    "s": "yolo26s",
    "n": "yolo26n",
    "tiny": "yolo26n",
    "26l": "yolo26l",
    "26m": "yolo26m",
    "26s": "yolo26s",
    "26n": "yolo26n",
    "11l": "yolo11l",
    "11m": "yolo11m",
    "11s": "yolo11s",
    "11n": "yolo11n",
}

REQUESTED_CLASS_MAP = {
    "car": {"small vehicle", "large vehicle"},
    "truck": {"large vehicle"},
    "bus": {"large vehicle"},
    "motorcycle": {"small vehicle"},
    "bicycle": {"small vehicle"},
    "airplane": {"plane"},
    "boat": {"ship"},
}

_yolo_models: dict[str, tuple[Any, list[str]]] = {}


def normalize_yolo_model_variant(value: str | None) -> str:
    normalized = (
        str(value or os.getenv("AERIAL_MODEL_VARIANT", "best")).strip().lower().replace("_", "").replace("-", "")
    )
    if normalized in YOLO_MODEL_VARIANTS:
        return normalized
    return YOLO_MODEL_ALIASES.get(normalized, "yolo26l")


def resolve_yolo_model_file(
    requested_variant: str | None = None,
) -> tuple[str, Path, str]:
    variant_name = normalize_yolo_model_variant(requested_variant)
    variant = YOLO_MODEL_VARIANTS[variant_name]
    configured_model = os.getenv("AERIAL_MODEL_FILE", "").strip()
    checkpoint_name = variant["checkpoint"]
    if configured_model:
        model_path = Path(configured_model)
        if model_path.name:
            checkpoint_name = model_path.name
    else:
        cache_path = YOLO_MODEL_ASSETS_DIR / checkpoint_name
        baked_path = YOLO_BAKED_MODEL_DIR / checkpoint_name
        model_path = baked_path if baked_path.exists() and not cache_path.exists() else cache_path
    return variant_name, model_path, checkpoint_name


def ensure_yolo_model_file(model_path: Path, checkpoint_name: str) -> Path:
    if model_path.exists():
        return model_path

    from ultralytics.utils.downloads import attempt_download_asset

    model_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading aerial detector checkpoint %s to %s",
        checkpoint_name,
        model_path,
    )
    downloaded_path = Path(
        attempt_download_asset(
            model_path,
            repo="ultralytics/assets",
            release=YOLO_MODEL_RELEASE,
        )
    )
    if downloaded_path.resolve() != model_path.resolve():
        shutil.copy2(downloaded_path, model_path)
    return model_path


def load_yolo_model(
    requested_variant: str | None = None,
) -> tuple[Any, list[str], str, str | int]:
    import torch
    from ultralytics import YOLO

    selected_variant, model_file_path, model_file_name = resolve_yolo_model_file(requested_variant)
    cache_key = str(model_file_path.resolve())
    cached_model = _yolo_models.get(cache_key)
    device: str | int = 0 if torch.cuda.is_available() else "cpu"
    if cached_model is not None:
        model, available_labels = cached_model
        return model, available_labels, selected_variant, device

    model_file_path = ensure_yolo_model_file(model_file_path, model_file_name)
    logger.info(
        "Loading YOLO aerial detector variant=%s checkpoint=%s device=%s imgsz=%s",
        selected_variant,
        model_file_path,
        device,
        YOLO_MODEL_IMAGE_SIZE,
    )
    model = YOLO(str(model_file_path))
    available_labels = list((model.names or {}).values())
    if not available_labels:
        raise RuntimeError(f"YOLO model did not expose class names: {model_file_path}")
    _yolo_models[cache_key] = (model, available_labels)
    return model, available_labels, selected_variant, device


def to_numpy(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    if hasattr(value, "tensor"):
        value = value.tensor
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def polygon_center(polygon: list[list[float]]) -> tuple[float, float]:
    if not polygon:
        return 0.0, 0.0
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def resolve_requested_labels(
    requested_classes: list[str],
    available_labels: list[str],
) -> list[str]:
    def normalize_label(label: str) -> str:
        return " ".join(str(label).strip().lower().replace("-", " ").replace("_", " ").split())

    validated_classes = validate_aerial_class_names(requested_classes or ["car"])
    resolved: set[str] = set()
    for requested in validated_classes:
        resolved.update(REQUESTED_CLASS_MAP[requested.strip().lower()])

    filtered = [label for label in available_labels if normalize_label(label) in resolved]
    if not filtered:
        raise RuntimeError(
            f"The selected YOLO model does not expose labels for the requested classes: {', '.join(validated_classes)}"
        )
    return filtered


def extract_obb_detections(
    raw_result: Any,
    requested_labels: list[str],
    min_confidence: float,
) -> list[dict]:
    detections = []
    requested = set(requested_labels)
    if raw_result is None:
        return detections

    oriented_boxes = getattr(raw_result, "obb", None)
    if oriented_boxes is None:
        return detections

    polygons = to_numpy(getattr(oriented_boxes, "xyxyxyxy", None))
    labels = to_numpy(getattr(oriented_boxes, "cls", None))
    scores = to_numpy(getattr(oriented_boxes, "conf", None))
    names = raw_result.names or {}
    if polygons is None or labels is None or scores is None:
        return detections

    for polygon, label_index, score in zip(polygons, labels, scores):
        class_id = int(label_index)
        class_name = names.get(class_id, str(class_id))
        if class_name not in requested or float(score) < min_confidence:
            continue
        polygon_points = [[float(x), float(y)] for x, y in np.asarray(polygon).reshape(-1, 2)]
        center_x, center_y = polygon_center(polygon_points)
        detections.append(
            {
                "polygon": polygon_points,
                "center_x": center_x,
                "center_y": center_y,
                "confidence": float(score),
                "class_id": class_id,
                "class_name": class_name,
            }
        )

    return detections


def run_yolo_detection(
    tile_path: str,
    requested_classes: list[str],
    requested_conf: float,
    requested_model_variant: str | None = None,
) -> tuple[list[dict], dict]:
    model, available_labels, selected_variant, device = load_yolo_model(requested_model_variant)
    requested_labels = resolve_requested_labels(
        requested_classes,
        available_labels,
    )
    fallback_conf = max(0.10, min(requested_conf, 0.20))
    raw_results = model.predict(
        source=tile_path,
        conf=fallback_conf,
        imgsz=YOLO_MODEL_IMAGE_SIZE,
        device=device,
        verbose=False,
    )
    raw_result = raw_results[0] if raw_results else None
    attempts = [
        {
            "conf": requested_conf,
            "label": f"YOLO primary pass conf={requested_conf:.2f}",
        },
        {
            "conf": fallback_conf,
            "label": "YOLO fallback pass with lower threshold",
        },
    ]

    best_detections = []
    best_attempt = attempts[0]
    for attempt in attempts:
        detections = extract_obb_detections(
            raw_result,
            requested_labels,
            attempt["conf"],
        )
        if len(detections) > len(best_detections):
            best_detections = detections
            best_attempt = attempt
        if detections:
            break
    best_attempt = {
        **best_attempt,
        "label": f"{best_attempt['label']} model={selected_variant}",
        "model_variant": selected_variant,
        "requested_labels": requested_labels,
    }
    return best_detections, best_attempt
