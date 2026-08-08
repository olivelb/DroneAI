"""Reusable YOLO OBB detection helpers.

The Kafka worker and the infrastructure-free local runner both use this
module. Heavy Ultralytics and Torch imports stay lazy so geometry and label
handling remain unit-testable in the lightweight development environment.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from hmac import compare_digest
from pathlib import Path
from typing import Any, TypedDict, cast

import numpy as np
from numpy.typing import NDArray

from shared.model_provenance import (
    build_model_manifest,
    installed_versions,
    sha256_file,
)
from shared.validation import validate_aerial_class_names


logger = logging.getLogger("app2-ia.detection-core")

YOLO_MODEL_ASSETS_DIR = Path(os.getenv("AERIAL_MODEL_DIR", "/opt/modelzoo"))
YOLO_BAKED_MODEL_DIR = Path(os.getenv("AERIAL_BAKED_MODEL_DIR", "/opt/modelzoo"))
YOLO_MODEL_IMAGE_SIZE = int(os.getenv("AERIAL_MODEL_IMGSZ", "1024"))
YOLO_MODEL_RELEASE = os.getenv("AERIAL_MODEL_RELEASE", "v8.4.0")

class YoloModelAsset(TypedDict):
    checkpoint: str
    repository: str
    release: str
    url: str
    sha256: str


def _official_yolo_asset(checkpoint: str, sha256: str) -> YoloModelAsset:
    repository = "ultralytics/assets"
    release = "v8.4.0"
    return {
        "checkpoint": checkpoint,
        "repository": repository,
        "release": release,
        "url": f"https://github.com/{repository}/releases/download/{release}/{checkpoint}",
        "sha256": sha256,
    }


YOLO_MODEL_REGISTRY: dict[str, YoloModelAsset] = {
    "yolo26l": _official_yolo_asset(
        "yolo26l-obb.pt",
        "8674b0c24bf68aab5eb45009e0ac3808ce432237edf8cb5c50ae2191cb263a2b",
    ),
    "yolo26m": _official_yolo_asset(
        "yolo26m-obb.pt",
        "23e0630f66857cf4b87535f6e705b065f1e8a33603640b8e61ace85b75312903",
    ),
    "yolo26s": _official_yolo_asset(
        "yolo26s-obb.pt",
        "38dbd72ef6804f9bbbea7ad20f486e6ca6e093c8cd9bc857207a846565bd6e0b",
    ),
    "yolo26n": _official_yolo_asset(
        "yolo26n-obb.pt",
        "6f51c78197aacda4a33be77294065a9001675fb893f56227a179731b53dbd2b0",
    ),
    "yolo11l": _official_yolo_asset(
        "yolo11l-obb.pt",
        "92dcf9face59a821cd4ee93828f4c19b51f6dee9b842b23c1dacab7aa89039fc",
    ),
    "yolo11m": _official_yolo_asset(
        "yolo11m-obb.pt",
        "41832a4349c08190335bbc11a8e64726750702eb49cf09abb262bc394a13498c",
    ),
    "yolo11s": _official_yolo_asset(
        "yolo11s-obb.pt",
        "43fa63102922e0701501241b307420d24fc55e080816888b18bf8c6f96b1a45a",
    ),
    "yolo11n": _official_yolo_asset(
        "yolo11n-obb.pt",
        "b62898ebf38940ca4df323863e45ee9d84a1a46d5d11ebdde529fb33aa9f3a32",
    ),
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
_yolo_model_hashes: dict[str, str] = {}
DetectionRecord = dict[str, Any]


class DetectionAttempt(TypedDict):
    conf: float
    label: str


def normalize_yolo_model_variant(value: str | None) -> str:
    normalized = (
        str(value or os.getenv("AERIAL_MODEL_VARIANT", "best")).strip().lower().replace("_", "").replace("-", "")
    )
    if normalized in YOLO_MODEL_REGISTRY:
        return normalized
    return YOLO_MODEL_ALIASES.get(normalized, "yolo26l")


def resolve_yolo_model_file(
    requested_variant: str | None = None,
) -> tuple[str, Path, str]:
    variant_name = normalize_yolo_model_variant(requested_variant)
    variant = YOLO_MODEL_REGISTRY[variant_name]
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


def resolve_yolo_model_integrity(
    selected_variant: str,
    model_path: Path,
) -> tuple[str, str, str]:
    """Return repository, revision and approved digest for one model path."""
    asset = YOLO_MODEL_REGISTRY[selected_variant]
    if model_path.name == asset["checkpoint"]:
        if asset["release"] != YOLO_MODEL_RELEASE:
            raise RuntimeError(
                f"unsupported AERIAL_MODEL_RELEASE={YOLO_MODEL_RELEASE!r}; "
                f"{selected_variant} is approved only for {asset['release']}"
            )
        return asset["repository"], asset["release"], asset["sha256"]

    expected = os.getenv("AERIAL_CUSTOM_MODEL_SHA256", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError(
            "A custom AERIAL_MODEL_FILE requires a 64-character "
            "AERIAL_CUSTOM_MODEL_SHA256 allowlist entry"
        )
    revision = os.getenv("AERIAL_CUSTOM_MODEL_REVISION", "custom").strip()
    if not revision:
        raise RuntimeError("AERIAL_CUSTOM_MODEL_REVISION must not be empty")
    return "custom", revision, expected


def verify_yolo_model_file(model_path: Path, expected_sha256: str) -> str:
    """Verify a checkpoint before Ultralytics or Torch can deserialize it."""
    digest = cast(str, sha256_file(model_path))
    if not compare_digest(digest, expected_sha256):
        raise RuntimeError(
            f"YOLO checkpoint checksum mismatch for {model_path}: "
            f"expected {expected_sha256}, got {digest}"
        )
    _yolo_model_hashes[str(model_path.resolve())] = digest
    return digest


def ensure_yolo_model_file(
    model_path: Path,
    checkpoint_name: str,
    selected_variant: str,
) -> Path:
    repository, release, expected_sha256 = resolve_yolo_model_integrity(
        selected_variant,
        model_path,
    )
    if model_path.exists():
        verify_yolo_model_file(model_path, expected_sha256)
        return model_path

    if repository == "custom":
        raise FileNotFoundError(f"Custom YOLO checkpoint does not exist: {model_path}")

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
            repo=repository,
            release=release,
        )
    )
    if downloaded_path.resolve() != model_path.resolve():
        shutil.copy2(downloaded_path, model_path)
    try:
        verify_yolo_model_file(model_path, expected_sha256)
    except RuntimeError:
        model_path.unlink(missing_ok=True)
        raise
    return model_path


def yolo_model_sha256(model_path: Path) -> str:
    cache_key = str(model_path.resolve())
    if cache_key not in _yolo_model_hashes:
        _yolo_model_hashes[cache_key] = sha256_file(model_path)
    return _yolo_model_hashes[cache_key]


def load_yolo_model(
    requested_variant: str | None = None,
) -> tuple[Any, list[str], str, str | int, Path]:
    import torch
    from ultralytics import YOLO

    selected_variant, model_file_path, model_file_name = resolve_yolo_model_file(requested_variant)
    cache_key = str(model_file_path.resolve())
    cached_model = _yolo_models.get(cache_key)
    device: str | int = 0 if torch.cuda.is_available() else "cpu"
    if cached_model is not None:
        model, available_labels = cached_model
        return model, available_labels, selected_variant, device, model_file_path

    model_file_path = ensure_yolo_model_file(
        model_file_path,
        model_file_name,
        selected_variant,
    )
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
    return model, available_labels, selected_variant, device, model_file_path


def to_numpy(value: Any) -> NDArray[Any] | None:
    if value is None:
        return None
    if hasattr(value, "tensor"):
        value = value.tensor
    if hasattr(value, "detach"):
        return cast(NDArray[Any], value.detach().cpu().numpy())
    return cast(NDArray[Any], np.asarray(value))


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
) -> list[DetectionRecord]:
    detections: list[DetectionRecord] = []
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

    for polygon, label_index, score in zip(polygons, labels, scores, strict=False):
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
) -> tuple[list[DetectionRecord], dict[str, Any]]:
    model, available_labels, selected_variant, device, model_path = load_yolo_model(requested_model_variant)
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
    attempts: list[DetectionAttempt] = [
        {
            "conf": requested_conf,
            "label": f"YOLO primary pass conf={requested_conf:.2f}",
        },
        {
            "conf": fallback_conf,
            "label": "YOLO fallback pass with lower threshold",
        },
    ]

    best_detections: list[DetectionRecord] = []
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
    repository, revision, _expected_sha256 = resolve_yolo_model_integrity(
        selected_variant,
        model_path,
    )
    attempt_details: dict[str, Any] = {
        **best_attempt,
        "label": f"{best_attempt['label']} model={selected_variant}",
        "model_variant": selected_variant,
        "requested_labels": requested_labels,
        "model_manifest": build_model_manifest(
            backend="yolo",
            repository=repository,
            revision=revision,
            artifact=model_path.name,
            artifact_sha256=yolo_model_sha256(model_path),
            libraries=installed_versions("ultralytics", "torch"),
            runtime={"device": str(device)},
            inference={
                "model_variant": selected_variant,
                "image_size": YOLO_MODEL_IMAGE_SIZE,
                "requested_classes": requested_classes,
                "resolved_labels": requested_labels,
                "primary_confidence": requested_conf,
                "fallback_confidence": fallback_conf,
            },
        ),
    }
    return best_detections, attempt_details
