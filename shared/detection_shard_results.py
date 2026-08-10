"""Validated shard-result and fan-in contracts for raster detection."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, cast

from shared.detection_geometry import DetectionRecord, dedupe_mission_detections
from shared.detection_sharding import DetectionShardPlan
from shared.model_provenance import validate_model_manifest

SHARD_RESULT_SCHEMA_VERSION = 1
MAX_SHARD_RAW_DETECTIONS = 100_000
MAX_AGGREGATE_RAW_DETECTIONS = 1_000_000


@dataclass(frozen=True)
class DetectionShardResult:
    plan_checksum_sha256: str
    shard_index: int
    tile_count: int
    model_manifest: dict[str, Any]
    detections: tuple[DetectionRecord, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SHARD_RESULT_SCHEMA_VERSION,
            "plan_checksum_sha256": self.plan_checksum_sha256,
            "shard_index": self.shard_index,
            "tile_count": self.tile_count,
            "model_manifest": self.model_manifest,
            "detections": list(self.detections),
        }


@dataclass(frozen=True)
class DetectionAggregate:
    model_manifest: dict[str, Any]
    raw_detections: tuple[DetectionRecord, ...]
    detections: tuple[DetectionRecord, ...]
    shard_count: int
    tile_count: int


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Detection {field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"Detection {field} must be a finite number")
    return normalized


def _normalize_detection(
    raw: object,
    *,
    first_tile_index: int,
    end_tile_index: int,
) -> DetectionRecord:
    if not isinstance(raw, dict):
        raise ValueError("Shard detections must be objects")
    detection = cast(dict[str, Any], raw)
    tile_index = detection.get("tile_index")
    if isinstance(tile_index, bool) or not isinstance(tile_index, int):
        raise ValueError("Detection tile_index must be an integer")
    if not first_tile_index <= tile_index < end_tile_index:
        raise ValueError("Detection tile_index is outside its declared shard")
    class_id = detection.get("class_id")
    if isinstance(class_id, bool) or not isinstance(class_id, int):
        raise ValueError("Detection class_id must be an integer")
    class_name = detection.get("class_name")
    if not isinstance(class_name, str) or not class_name.strip() or len(class_name) > 128:
        raise ValueError("Detection class_name must contain 1 to 128 characters")
    confidence = _finite_number(detection.get("confidence"), "confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Detection confidence must be between 0 and 1")
    raw_segment = detection.get("segment") or []
    if not isinstance(raw_segment, list):
        raise ValueError("Detection segment must be a list")
    segment: list[list[float]] = []
    for raw_point in raw_segment:
        if not isinstance(raw_point, list) or len(raw_point) != 2:
            raise ValueError("Detection segment points must be coordinate pairs")
        segment.append(
            [
                _finite_number(raw_point[0], "segment coordinate"),
                _finite_number(raw_point[1], "segment coordinate"),
            ]
        )
    return {
        "global_pixel_x": _finite_number(
            detection.get("global_pixel_x"),
            "global_pixel_x",
        ),
        "global_pixel_y": _finite_number(
            detection.get("global_pixel_y"),
            "global_pixel_y",
        ),
        "confidence": confidence,
        "class_id": class_id,
        "class_name": class_name.strip(),
        "segment": segment,
        "tile_index": tile_index,
    }


def parse_detection_shard_result(
    payload: object,
    plan: DetectionShardPlan,
    *,
    maximum_raw_detections: int = MAX_SHARD_RAW_DETECTIONS,
) -> DetectionShardResult:
    """Validate one untrusted decoded shard-result payload against its plan."""

    if not 1 <= maximum_raw_detections <= MAX_SHARD_RAW_DETECTIONS:
        raise ValueError("Shard detection limit is outside its safety bound")
    if not isinstance(payload, dict):
        raise ValueError("Detection shard result must be an object")
    raw = cast(dict[str, Any], payload)
    if raw.get("schema_version") != SHARD_RESULT_SCHEMA_VERSION:
        raise ValueError("Unsupported detection shard result schema version")
    if raw.get("plan_checksum_sha256") != plan.checksum_sha256:
        raise ValueError("Detection shard result does not match its plan checksum")
    shard_index = raw.get("shard_index")
    if isinstance(shard_index, bool) or not isinstance(shard_index, int):
        raise ValueError("Detection shard index must be an integer")
    shard = plan.shard(shard_index)
    if raw.get("tile_count") != shard.tile_count:
        raise ValueError("Detection shard result tile count does not match its plan")
    raw_detections = raw.get("detections")
    if not isinstance(raw_detections, list):
        raise ValueError("Detection shard result detections must be a list")
    if len(raw_detections) > maximum_raw_detections:
        raise ValueError("Detection shard result exceeds its detection safety limit")
    return DetectionShardResult(
        plan_checksum_sha256=plan.checksum_sha256,
        shard_index=shard_index,
        tile_count=shard.tile_count,
        model_manifest=validate_model_manifest(raw.get("model_manifest")),
        detections=tuple(
            _normalize_detection(
                detection,
                first_tile_index=shard.first_tile_index,
                end_tile_index=shard.end_tile_index,
            )
            for detection in raw_detections
        ),
    )


def canonical_detection_shard_result(result: DetectionShardResult) -> bytes:
    return json.dumps(
        result.payload(),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def aggregate_detection_shards(
    plan: DetectionShardPlan,
    results: list[DetectionShardResult],
    *,
    maximum_raw_detections: int = MAX_AGGREGATE_RAW_DETECTIONS,
) -> DetectionAggregate:
    """Validate a complete fan-in and deduplicate detections across shards."""

    if not 1 <= maximum_raw_detections <= MAX_AGGREGATE_RAW_DETECTIONS:
        raise ValueError("Aggregate detection limit is outside its safety bound")
    result_by_index: dict[int, DetectionShardResult] = {}
    for result in results:
        if result.plan_checksum_sha256 != plan.checksum_sha256:
            raise ValueError("Detection shard result does not match its plan checksum")
        if result.shard_index in result_by_index:
            raise ValueError(f"Duplicate detection shard result: {result.shard_index}")
        result_by_index[result.shard_index] = result
    expected_indices = set(range(plan.shard_count))
    if set(result_by_index) != expected_indices:
        missing = sorted(expected_indices - set(result_by_index))
        unexpected = sorted(set(result_by_index) - expected_indices)
        raise ValueError(
            f"Incomplete detection shard results: missing={missing}, "
            f"unexpected={unexpected}"
        )
    ordered = [result_by_index[index] for index in range(plan.shard_count)]
    model_manifest = ordered[0].model_manifest
    if any(result.model_manifest != model_manifest for result in ordered[1:]):
        raise ValueError("AI model provenance changed between detection shards")
    raw_detection_count = sum(len(result.detections) for result in ordered)
    if raw_detection_count > maximum_raw_detections:
        raise ValueError("Detection fan-in exceeds its detection safety limit")
    raw_detections = tuple(
        detection for result in ordered for detection in result.detections
    )
    return DetectionAggregate(
        model_manifest=model_manifest,
        raw_detections=raw_detections,
        detections=tuple(dedupe_mission_detections(raw_detections)),
        shard_count=plan.shard_count,
        tile_count=plan.tile_count,
    )
