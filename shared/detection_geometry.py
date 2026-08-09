"""Shared tile planning and spatial deduplication for AI detections."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import cv2
import numpy as np


DetectionRecord = dict[str, Any]


def build_tile_starts(full_size: int, tile_size: int, overlap: int) -> list[int]:
    if full_size <= 0:
        raise ValueError("full_size must be positive")
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be between 0 and tile_size - 1")
    if full_size <= tile_size:
        return [0]
    stride = tile_size - overlap
    starts = list(range(0, full_size - tile_size + 1, stride))
    last_start = full_size - tile_size
    if starts[-1] != last_start:
        distance_to_edge = last_start - starts[-1]
        if len(starts) > 1 and overlap > 0 and distance_to_edge < overlap:
            starts[-1] = last_start
        else:
            starts.append(last_start)
    return starts


def polygon_area(points: list[list[float]]) -> float:
    if not points or len(points) < 3:
        return 0.0
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    return float(abs(cv2.contourArea(contour)))


def polygon_bbox(
    points: list[list[float]],
) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def polygon_contains_point(
    points: list[list[float]],
    point_x: float,
    point_y: float,
) -> bool:
    if not points or len(points) < 3:
        return False
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    return (
        cv2.pointPolygonTest(
            contour,
            (float(point_x), float(point_y)),
            False,
        )
        >= 0
    )


def polygon_centroid(
    points: list[list[float]],
) -> tuple[float, float] | None:
    if not points or len(points) < 3:
        return None
    contour = np.asarray(points, dtype=np.float32).reshape((-1, 1, 2))
    moments = cv2.moments(contour)
    if moments["m00"]:
        return (
            float(moments["m10"] / moments["m00"]),
            float(moments["m01"] / moments["m00"]),
        )
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def bbox_iou(
    left_bbox: tuple[float, float, float, float],
    right_bbox: tuple[float, float, float, float],
) -> float:
    left_x1, left_y1, left_x2, left_y2 = left_bbox
    right_x1, right_y1, right_x2, right_y2 = right_bbox
    inter_x1 = max(left_x1, right_x1)
    inter_y1 = max(left_y1, right_y1)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    left_area = max(0.0, left_x2 - left_x1) * max(
        0.0,
        left_y2 - left_y1,
    )
    right_area = max(0.0, right_x2 - right_x1) * max(
        0.0,
        right_y2 - right_y1,
    )
    union_area = left_area + right_area - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def are_duplicate_detections(
    candidate: DetectionRecord,
    kept: DetectionRecord,
    center_threshold: float,
    iou_threshold: float,
) -> bool:
    if candidate.get("class_name") != kept.get("class_name"):
        return False
    candidate_segment = candidate.get("_segment") or []
    kept_segment = kept.get("_segment") or []
    candidate_centroid = polygon_centroid(candidate_segment)
    if candidate_centroid and polygon_contains_point(
        kept_segment,
        candidate_centroid[0],
        candidate_centroid[1],
    ):
        return True
    if any(
        polygon_contains_point(kept_segment, point_x, point_y)
        for point_x, point_y in candidate_segment
    ):
        return True
    delta_x = float(candidate["global_pixel_x"]) - float(kept["global_pixel_x"])
    delta_y = float(candidate["global_pixel_y"]) - float(kept["global_pixel_y"])
    if abs(delta_x) > center_threshold or abs(delta_y) > center_threshold:
        return False
    return bbox_iou(candidate["_bbox"], kept["_bbox"]) >= iou_threshold


class _DetectionGrid:
    """Index kept detections by class and bounding-box grid cells."""

    _MAX_CELLS_PER_DETECTION = 4096

    def __init__(self, cell_size: float) -> None:
        self._cell_size = max(float(cell_size), 1.0)
        self._entries: list[DetectionRecord] = []
        self._cells: dict[tuple[str, int, int], list[int]] = {}
        self._global_indices: list[int] = []

    def _cell_bounds(
        self,
        bbox: tuple[float, float, float, float],
    ) -> tuple[int, int, int, int] | None:
        if not all(math.isfinite(value) for value in bbox):
            return None
        min_x, min_y, max_x, max_y = bbox
        min_cell_x = math.floor(min_x / self._cell_size)
        min_cell_y = math.floor(min_y / self._cell_size)
        max_cell_x = math.floor(max_x / self._cell_size)
        max_cell_y = math.floor(max_y / self._cell_size)
        cell_count = (max_cell_x - min_cell_x + 1) * (
            max_cell_y - min_cell_y + 1
        )
        if cell_count > self._MAX_CELLS_PER_DETECTION:
            return None
        return min_cell_x, min_cell_y, max_cell_x, max_cell_y

    @staticmethod
    def _class_key(detection: DetectionRecord) -> str:
        return str(detection.get("class_name"))

    def add(self, detection: DetectionRecord) -> None:
        index = len(self._entries)
        self._entries.append(detection)
        bounds = self._cell_bounds(detection["_bbox"])
        if bounds is None:
            self._global_indices.append(index)
            return
        min_x, min_y, max_x, max_y = bounds
        class_key = self._class_key(detection)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                self._cells.setdefault((class_key, cell_x, cell_y), []).append(
                    index
                )

    def candidates(self, detection: DetectionRecord) -> list[DetectionRecord]:
        bounds = self._cell_bounds(detection["_bbox"])
        if bounds is None:
            return self._entries
        indices = set(self._global_indices)
        min_x, min_y, max_x, max_y = bounds
        class_key = self._class_key(detection)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                indices.update(self._cells.get((class_key, cell_x, cell_y), ()))
        return [self._entries[index] for index in sorted(indices)]


def dedupe_mission_detections(
    detections: Iterable[DetectionRecord],
    center_threshold: float = 40.0,
    iou_threshold: float = 0.05,
) -> list[DetectionRecord]:
    prepared: list[DetectionRecord] = []
    for detection in detections:
        segment = detection.get("segment") or []
        if len(segment) < 3:
            prepared.append(detection)
            continue
        enriched = dict(detection)
        enriched["_area"] = polygon_area(segment)
        enriched["_bbox"] = polygon_bbox(segment)
        enriched["_segment"] = segment
        prepared.append(enriched)
    kept: list[DetectionRecord] = []
    spatial_index = _DetectionGrid(center_threshold)
    for detection in sorted(
        prepared,
        key=lambda item: (
            item.get("_area", 0.0),
            float(item.get("confidence", 0.0)),
        ),
        reverse=True,
    ):
        if "_bbox" not in detection:
            kept.append(detection)
            continue
        if any(
            are_duplicate_detections(
                detection,
                existing,
                center_threshold,
                iou_threshold,
            )
            for existing in spatial_index.candidates(detection)
        ):
            continue
        kept.append(detection)
        spatial_index.add(detection)
    deduped: list[DetectionRecord] = []
    for detection in kept:
        cleaned = dict(detection)
        for private_key in ("_area", "_bbox", "_segment"):
            cleaned.pop(private_key, None)
        deduped.append(cleaned)
    return deduped
