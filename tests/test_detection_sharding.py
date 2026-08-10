import pytest

from shared.detection_sharding import (
    build_detection_shard_plan,
    parse_detection_shard_plan_descriptor,
)


def test_large_detection_plan_is_partitioned_deterministically() -> None:
    plan = build_detection_shard_plan(
        width=346_624,
        height=9_472,
        tile_size=1_024,
        overlap=256,
        tiles_per_shard=1_024,
    )
    repeated = build_detection_shard_plan(
        width=346_624,
        height=9_472,
        tile_size=1_024,
        overlap=256,
        tiles_per_shard=1_024,
    )

    assert plan.tile_count == 5_412
    assert plan.shard_count == 6
    assert [shard.tile_count for shard in plan.shards] == [
        1_024,
        1_024,
        1_024,
        1_024,
        1_024,
        292,
    ]
    assert plan.checksum_sha256 == repeated.checksum_sha256
    assert plan.descriptor()["checksum_sha256"] == plan.checksum_sha256


def test_shard_windows_cover_each_row_major_tile_exactly_once() -> None:
    plan = build_detection_shard_plan(
        width=600,
        height=500,
        tile_size=256,
        overlap=64,
        tiles_per_shard=3,
    )

    tiles = [tile for shard in plan.shards for tile in plan.tiles(shard.shard_index)]

    assert [tile.tile_index for tile in tiles] == list(range(plan.tile_count))
    assert len({(tile.offset_x, tile.offset_y) for tile in tiles}) == plan.tile_count
    assert tiles[0].offset_x == 0
    assert tiles[0].offset_y == 0
    assert tiles[-1].offset_x == 344
    assert tiles[-1].offset_y == 244
    assert tiles[-1].width == 256
    assert tiles[-1].height == 256
    assert plan.planned_inference_pixels == sum(
        tile.width * tile.height for tile in tiles
    )


def test_detection_plan_fails_closed_on_tile_and_shard_limits() -> None:
    with pytest.raises(ValueError, match="tile safety limit"):
        build_detection_shard_plan(
            346_624,
            9_472,
            1_024,
            256,
            tiles_per_shard=1_024,
            maximum_tiles=5_000,
        )

    with pytest.raises(ValueError, match="shard safety limit"):
        build_detection_shard_plan(
            346_624,
            9_472,
            1_024,
            256,
            tiles_per_shard=1_000,
            maximum_shards=5,
        )


def test_detection_plan_rejects_invalid_bounds_and_shard_index() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        build_detection_shard_plan(0, 100, 256, 64, tiles_per_shard=10)
    with pytest.raises(ValueError, match="tiles per shard"):
        build_detection_shard_plan(100, 100, 256, 64, tiles_per_shard=0)

    plan = build_detection_shard_plan(100, 100, 256, 64, tiles_per_shard=10)
    with pytest.raises(IndexError, match="out of range"):
        tuple(plan.tiles(1))


def test_durable_plan_descriptor_round_trip_fails_closed_on_drift() -> None:
    plan = build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=3)

    restored = parse_detection_shard_plan_descriptor(plan.descriptor())

    assert restored == plan
    drifted = {**plan.descriptor(), "tile_count": plan.tile_count + 1}
    with pytest.raises(ValueError, match="inconsistent"):
        parse_detection_shard_plan_descriptor(drifted)
    with pytest.raises(ValueError, match="invalid fields"):
        parse_detection_shard_plan_descriptor(
            {**plan.descriptor(), "unexpected": True}
        )
