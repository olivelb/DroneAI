import pytest

from shared.kafka_partitioning import tenant_mission_key, tile_work_key


def test_tile_work_key_is_stable_for_campaign_and_pipeline_work():
    assert tile_work_key("mission-1", "run-1", 7) == "mission-1:run-1:tile:7"
    assert tile_work_key("mission-1", None, 7) == "mission-1:pipeline:tile:7"


def test_tile_work_key_distributes_logically_distinct_tiles():
    assert tile_work_key("mission-1", "run-1", 7) != tile_work_key(
        "mission-1",
        "run-1",
        8,
    )


def test_kafka_keys_are_isolated_by_organization():
    assert tenant_mission_key("tenant-a", "mission-1") == "tenant-a:mission-1"
    assert tile_work_key(
        "mission-1",
        "run-1",
        7,
        organization_id="tenant-a",
    ) != tile_work_key(
        "mission-1",
        "run-1",
        7,
        organization_id="tenant-b",
    )


@pytest.mark.parametrize(
    ("vol_id", "tile_index", "message"),
    [
        ("", 0, "vol_id is required"),
        ("mission-1", -1, "tile_index must be non-negative"),
    ],
)
def test_tile_work_key_rejects_invalid_identity(vol_id, tile_index, message):
    with pytest.raises(ValueError, match=message):
        tile_work_key(vol_id, None, tile_index)
