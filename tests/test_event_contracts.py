import json
from pathlib import Path

import pytest

from shared.event_contracts import (
    EventValidationError,
    decode_event,
    deterministic_event_id,
    make_event,
)
from shared.event_schemas import EVENT_TYPES, kafka_event_json_schema


ROOT = Path(__file__).resolve().parents[1]


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


def test_mission_event_accepts_a_versioned_single_stage_command():
    artifact_id = "d671317d-9424-42ab-86c3-56adb0ea7685"
    run_id = "02fd284a-aa1c-489b-bb8f-f9eeed70e761"

    event = make_event(
        "mission",
        {
            "vol_id": "mission-stage",
            "phases": ["detection"],
            "stage_run_id": run_id,
            "upstream_artifact_ids": {"rasterization": artifact_id},
            "stage_parameters": {"confidence": 0.45},
        },
    )

    assert event["stage_run_id"] == run_id
    assert event["upstream_artifact_ids"] == {"rasterization": artifact_id}


def test_mission_event_rejects_incomplete_stage_dependencies():
    with pytest.raises(EventValidationError, match="requires selected phase"):
        make_event(
            "mission",
            {
                "vol_id": "mission-stage",
                "phases": ["detection"],
                "stage_run_id": "02fd284a-aa1c-489b-bb8f-f9eeed70e761",
            },
        )


def test_tile_detection_accepts_a_complete_object_storage_reference():
    event = make_event(
        "tile_detection",
        {
            "vol_id": "mission-1",
            "tile_index": 2,
            "model_manifest": {"backend": "yolo"},
            "result_s3_key": "missions/mission-1/ai-tile-results/pipeline/attempt_0/tile_2.json",
            "result_sha256": "a" * 64,
            "result_size_bytes": 123,
            "detection_count": 4,
            "result_schema_version": 1,
        },
    )

    assert event["result_size_bytes"] == 123
    assert "detections" not in event


@pytest.mark.parametrize(
    "payload",
    [
        {
            "vol_id": "mission-1",
            "tile_index": 2,
            "result_s3_key": "result.json",
        },
        {
            "vol_id": "mission-1",
            "tile_index": 2,
            "detections": [],
            "model_manifest": {"backend": "yolo"},
            "result_s3_key": "result.json",
            "result_sha256": "a" * 64,
            "result_size_bytes": 2,
            "detection_count": 0,
            "result_schema_version": 1,
        },
    ],
)
def test_tile_detection_rejects_incomplete_or_ambiguous_results(payload):
    with pytest.raises(EventValidationError, match="result reference|exactly one"):
        make_event("tile_detection", payload)


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


@pytest.mark.parametrize("unsafe_value", ["../escape", "a/b", "a\\b", ".", ".."])
def test_event_path_identifiers_reject_unsafe_segments(unsafe_value):
    with pytest.raises(EventValidationError, match="vol_id"):
        make_event(
            "image_tile",
            {
                "vol_id": unsafe_value,
                "tile_index": 1,
                "tile_s3_key": "missions/safe/tile.jpg",
            },
        )

    with pytest.raises(EventValidationError, match="analysis_run_id"):
        make_event(
            "image_tile",
            {
                "vol_id": "mission-safe",
                "analysis_run_id": unsafe_value,
                "tile_index": 1,
                "tile_s3_key": "missions/mission-safe/tile.jpg",
            },
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
    identified = make_event(
        "status",
        {
            "vol_id": "mission-1",
            "status": "processing",
            "stage_run_id": "02fd284a-aa1c-489b-bb8f-f9eeed70e761",
        },
    )
    assert identified["stage_run_id"] == "02fd284a-aa1c-489b-bb8f-f9eeed70e761"
    with pytest.raises(EventValidationError, match="unsupported status"):
        make_event(
            "status",
            {
                "vol_id": "mission-1",
                "status": "stopped",
            },
        )


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        (
            "image_tile",
            {"vol_id": "mission-1", "tile_index": "2", "tile_s3_key": "tile.jpg"},
            "tile_index",
        ),
        (
            "image_tile",
            {"vol_id": "mission-1", "tile_index": 2},
            "tile_s3_key or tile_path",
        ),
        (
            "tile_detection",
            {"vol_id": "mission-1", "tile_index": 2, "detections": {}},
            "detections",
        ),
        (
            "control",
            {"vol_id": "mission-1", "command": "stop"},
            "command",
        ),
    ],
)
def test_event_specific_schemas_reject_invalid_payloads(
    event_type,
    payload,
    message,
):
    with pytest.raises(EventValidationError, match=message):
        make_event(event_type, payload)


def test_event_contracts_preserve_forward_compatible_extensions():
    event = make_event(
        "mission",
        {
            "vol_id": "mission-1",
            "future_parameter": {"enabled": True},
        },
    )

    assert event["future_parameter"] == {"enabled": True}


def test_discriminated_json_schema_covers_every_event_type():
    schema = kafka_event_json_schema()
    definitions = schema["$defs"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:droneai:kafka-events:v1"
    assert len(schema["oneOf"]) == len(EVENT_TYPES)
    assert {
        definition["properties"]["event_type"]["const"]
        for definition in definitions.values()
        if "event_type" in definition.get("properties", {})
    } == EVENT_TYPES


@pytest.mark.parametrize(
    "requirements_file",
    [
        "requirements/colmap.txt",
        "requirements/processing.txt",
        "requirements/ia-extra.txt",
    ],
)
def test_worker_runtime_locks_include_pydantic(requirements_file):
    lock = (ROOT / requirements_file).read_text(encoding="utf-8")

    assert "pydantic==2.13.4" in lock
