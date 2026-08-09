"""Approved YOLO OBB artifacts and their complete selectable class map."""

from __future__ import annotations

import os
from typing import TypedDict


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
    "yolo26l": _official_yolo_asset("yolo26l-obb.pt", "8674b0c24bf68aab5eb45009e0ac3808ce432237edf8cb5c50ae2191cb263a2b"),
    "yolo26m": _official_yolo_asset("yolo26m-obb.pt", "23e0630f66857cf4b87535f6e705b065f1e8a33603640b8e61ace85b75312903"),
    "yolo26s": _official_yolo_asset("yolo26s-obb.pt", "38dbd72ef6804f9bbbea7ad20f486e6ca6e093c8cd9bc857207a846565bd6e0b"),
    "yolo26n": _official_yolo_asset("yolo26n-obb.pt", "6f51c78197aacda4a33be77294065a9001675fb893f56227a179731b53dbd2b0"),
    "yolo11l": _official_yolo_asset("yolo11l-obb.pt", "92dcf9face59a821cd4ee93828f4c19b51f6dee9b842b23c1dacab7aa89039fc"),
    "yolo11m": _official_yolo_asset("yolo11m-obb.pt", "41832a4349c08190335bbc11a8e64726750702eb49cf09abb262bc394a13498c"),
    "yolo11s": _official_yolo_asset("yolo11s-obb.pt", "43fa63102922e0701501241b307420d24fc55e080816888b18bf8c6f96b1a45a"),
    "yolo11n": _official_yolo_asset("yolo11n-obb.pt", "b62898ebf38940ca4df323863e45ee9d84a1a46d5d11ebdde529fb33aa9f3a32"),
}

YOLO_NATIVE_CLASSES: tuple[str, ...] = (
    "plane",
    "ship",
    "storage tank",
    "baseball diamond",
    "tennis court",
    "basketball court",
    "ground track field",
    "harbor",
    "bridge",
    "large vehicle",
    "small vehicle",
    "helicopter",
    "roundabout",
    "soccer ball field",
    "swimming pool",
)

YOLO_CLASS_MAP: dict[str, frozenset[str]] = {
    **{label: frozenset({label}) for label in YOLO_NATIVE_CLASSES},
    "airplane": frozenset({"plane"}),
    "bicycle": frozenset({"small vehicle"}),
    "boat": frozenset({"ship"}),
    "bus": frozenset({"large vehicle"}),
    "car": frozenset({"small vehicle", "large vehicle"}),
    "motorcycle": frozenset({"small vehicle"}),
    "truck": frozenset({"large vehicle"}),
}
SUPPORTED_AERIAL_CLASSES = frozenset(YOLO_CLASS_MAP)


def _configured_variants(raw_value: str | None = None) -> set[str]:
    raw = os.getenv("AERIAL_AVAILABLE_MODEL_VARIANTS", "") if raw_value is None else raw_value
    if not raw.strip():
        return set(YOLO_MODEL_REGISTRY)
    return {
        value.strip().lower()
        for value in raw.split(",")
        if value.strip().lower() in YOLO_MODEL_REGISTRY
    }


def yolo_model_catalog(raw_variants: str | None = None) -> list[dict[str, object]]:
    available = _configured_variants(raw_variants)
    return [
        {
            "id": model_id,
            "label": f"{model_id[:-1].upper()}-{model_id[-1].upper()}",
            "available": model_id in available,
            "artifact": asset["checkpoint"],
            "repository": asset["repository"],
            "revision": asset["release"],
            "artifact_sha256": asset["sha256"],
            "classes": list(YOLO_NATIVE_CLASSES),
            "selectable_classes": sorted(SUPPORTED_AERIAL_CLASSES),
        }
        for model_id, asset in YOLO_MODEL_REGISTRY.items()
    ]


def yolo_model_manifest(
    model_id: str,
    raw_variants: str | None = None,
) -> dict[str, object]:
    for manifest in yolo_model_catalog(raw_variants):
        if manifest["id"] == model_id:
            if not manifest["available"]:
                raise ValueError(f"YOLO model {model_id!r} is not available in this deployment")
            return manifest
    raise ValueError(f"unknown YOLO model {model_id!r}")
