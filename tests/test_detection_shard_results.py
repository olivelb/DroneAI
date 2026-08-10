from copy import deepcopy

import pytest

from shared.detection_shard_results import (
    aggregate_detection_shards,
    canonical_detection_shard_result,
    parse_detection_shard_result,
)
from shared.detection_sharding import build_detection_shard_plan
from shared.model_provenance import build_model_manifest


def _model_manifest() -> dict[str, object]:
    return build_model_manifest(
        backend="sam3",
        repository="facebook/sam3",
        revision="3" * 40,
        artifact="model.safetensors",
        artifact_sha256="a" * 64,
        libraries={"transformers": "test"},
        runtime={"device": "cpu"},
        inference={"confidence": 0.3},
    )


def _detection(tile_index: int, offset: float = 0.0) -> dict[str, object]:
    return {
        "global_pixel_x": 100.0 + offset,
        "global_pixel_y": 100.0,
        "confidence": 0.9,
        "class_id": 0,
        "class_name": "car",
        "segment": [
            [95.0 + offset, 95.0],
            [105.0 + offset, 95.0],
            [105.0 + offset, 105.0],
            [95.0 + offset, 105.0],
        ],
        "tile_index": tile_index,
    }


def _payload(plan, shard_index, detections):
    shard = plan.shard(shard_index)
    return {
        "schema_version": 1,
        "plan_checksum_sha256": plan.checksum_sha256,
        "shard_index": shard_index,
        "tile_count": shard.tile_count,
        "model_manifest": _model_manifest(),
        "detections": detections,
    }


def test_fan_in_orders_shards_and_deduplicates_across_their_boundary() -> None:
    plan = build_detection_shard_plan(
        600,
        500,
        256,
        64,
        tiles_per_shard=5,
    )
    left = parse_detection_shard_result(
        _payload(plan, 0, [_detection(plan.shard(0).end_tile_index - 1)]),
        plan,
    )
    right = parse_detection_shard_result(
        _payload(plan, 1, [_detection(plan.shard(1).first_tile_index, 1.0)]),
        plan,
    )

    aggregate = aggregate_detection_shards(plan, [right, left])

    assert aggregate.shard_count == 2
    assert aggregate.tile_count == plan.tile_count
    assert len(aggregate.raw_detections) == 2
    assert len(aggregate.detections) == 1
    assert aggregate.model_manifest == _model_manifest()
    assert canonical_detection_shard_result(left).startswith(b'{"detections"')


def test_shard_result_rejects_wrong_plan_and_out_of_range_tile() -> None:
    plan = build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=5)
    wrong_plan = _payload(plan, 0, [])
    wrong_plan["plan_checksum_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="plan checksum"):
        parse_detection_shard_result(wrong_plan, plan)

    wrong_tile = _payload(plan, 0, [_detection(plan.shard(0).end_tile_index)])
    with pytest.raises(ValueError, match="outside its declared shard"):
        parse_detection_shard_result(wrong_tile, plan)


def test_fan_in_rejects_missing_duplicate_and_changed_model_results() -> None:
    plan = build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=5)
    left = parse_detection_shard_result(_payload(plan, 0, []), plan)
    right_payload = _payload(plan, 1, [])
    right = parse_detection_shard_result(right_payload, plan)

    with pytest.raises(ValueError, match="Incomplete"):
        aggregate_detection_shards(plan, [left])
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_detection_shards(plan, [left, left, right])

    changed_payload = deepcopy(right_payload)
    changed_payload["model_manifest"]["artifact_sha256"] = "b" * 64
    changed = parse_detection_shard_result(changed_payload, plan)
    with pytest.raises(ValueError, match="provenance changed"):
        aggregate_detection_shards(plan, [left, changed])


def test_shard_and_fan_in_detection_limits_fail_closed() -> None:
    plan = build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=5)
    payload = _payload(plan, 0, [_detection(0), _detection(1, 100.0)])
    with pytest.raises(ValueError, match="detection safety limit"):
        parse_detection_shard_result(payload, plan, maximum_raw_detections=1)

    left = parse_detection_shard_result(payload, plan)
    right = parse_detection_shard_result(_payload(plan, 1, []), plan)
    with pytest.raises(ValueError, match="fan-in exceeds"):
        aggregate_detection_shards(
            plan,
            [left, right],
            maximum_raw_detections=1,
        )
