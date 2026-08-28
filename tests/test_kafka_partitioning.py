import pytest

from shared.kafka_partitioning import tenant_mission_key


def test_kafka_keys_are_isolated_by_organization():
    assert tenant_mission_key("tenant-a", "mission-1") == "tenant-a:mission-1"
    assert tenant_mission_key("tenant-a", "mission-1") != tenant_mission_key(
        "tenant-b", "mission-1",
    )


def test_tenant_mission_key_rejects_missing_identity():
    with pytest.raises(ValueError, match="vol_id is required"):
        tenant_mission_key("tenant-a", "")
