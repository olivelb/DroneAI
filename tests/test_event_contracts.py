import json

import pytest

from shared.event_contracts import (
    EventValidationError,
    decode_event,
    deterministic_event_id,
    make_event,
)


def test_deterministic_event_id_is_stable_and_namespaced():
    first = deterministic_event_id("image_tile", "mission-1", 7)
    second = deterministic_event_id("image_tile", "mission-1", 7)
    other = deterministic_event_id("tile_detection", "mission-1", 7)

    assert first == second
    assert first != other
    assert first.startswith("image_tile:")


def test_make_event_adds_versioned_trace_metadata():
    event = make_event(
        "image_tile",
        {
            "vol_id": "mission-1",
            "tile_index": 7,
            "tile_s3_key": "missions/mission-1/tile_7.jpg",
        },
        event_id="image_tile:fixed",
        correlation_id="mission:root",
        causation_id="orthomosaic:parent",
    )

    assert event["schema_version"] == 1
    assert event["event_type"] == "image_tile"
    assert event["event_id"] == "image_tile:fixed"
    assert event["correlation_id"] == "mission:root"
    assert event["causation_id"] == "orthomosaic:parent"
    assert event["attempt"] == 0
    assert event["emitted_at"]


def test_decode_event_upgrades_a_legacy_payload():
    event = decode_event(
        json.dumps(
            {
                "vol_id": "mission-1",
                "tile_index": 2,
                "detections": [],
            }
        ),
        expected_type="tile_detection",
    )

    assert event["schema_version"] == 1
    assert event["event_type"] == "tile_detection"
    assert event["event_id"].startswith("tile_detection:")


def test_decode_event_rejects_wrong_version_and_missing_fields():
    with pytest.raises(EventValidationError, match="unsupported schema_version"):
        decode_event(
            json.dumps(
                {
                    "schema_version": 99,
                    "event_type": "mission",
                    "vol_id": "mission-1",
                }
            ),
            expected_type="mission",
        )

    with pytest.raises(EventValidationError, match="tile_index"):
        decode_event(
            json.dumps({"vol_id": "mission-1", "detections": []}),
            expected_type="tile_detection",
        )


def test_status_events_accept_cancelled_and_reject_unknown_states():
    event = make_event(
        "status",
        {
            "vol_id": "mission-1",
            "service": "COLMAP",
            "step": "CANCELLED",
            "progress": 0,
            "status": "cancelled",
        },
    )

    assert event["status"] == "cancelled"
    with pytest.raises(EventValidationError, match="unsupported status"):
        make_event(
            "status",
            {
                "vol_id": "mission-1",
                "status": "stopped",
            },
        )
