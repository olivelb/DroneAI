"""Contracts for the control-plane events still emitted in production."""

import json
from pathlib import Path

import pytest

from shared.event_contracts import (
    EventValidationError,
    decode_event,
    deterministic_event_id,
    deterministic_tenant_event_id,
    make_event,
)
from shared.event_schemas import EVENT_TYPES, kafka_event_json_schema

ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_event_id_is_stable_and_namespaced():
    first = deterministic_event_id("control", "mission-1", 7)
    assert first == deterministic_event_id("control", "mission-1", 7)
    assert first != deterministic_event_id("status", "mission-1", 7)
    assert first.startswith("control:")


def test_deterministic_event_id_is_isolated_by_organization():
    assert deterministic_tenant_event_id(
        "control", "tenant-a", "mission-1", "cancel",
    ) != deterministic_tenant_event_id(
        "control", "tenant-b", "mission-1", "cancel",
    )


def test_make_event_adds_versioned_trace_metadata():
    event = make_event(
        "control",
        {"vol_id": "mission-1", "command": "cancel"},
        event_id="control:fixed",
        correlation_id="mission:root",
        causation_id="control:parent",
    )
    assert event["schema_version"] == 1
    assert event["event_type"] == "control"
    assert event["event_id"] == "control:fixed"
    assert event["correlation_id"] == "mission:root"
    assert event["causation_id"] == "control:parent"
    assert event["attempt"] == 0
    assert event["emitted_at"]


def test_decode_event_rejects_unversioned_payload():
    with pytest.raises(EventValidationError, match="missing event_type"):
        decode_event(
            json.dumps({"vol_id": "mission-1", "command": "cancel"}),
            expected_type="control",
        )


@pytest.mark.parametrize(
    "field",
    ["schema_version", "event_type", "event_id", "correlation_id", "attempt", "emitted_at"],
)
def test_decode_event_requires_complete_envelope(field):
    event = make_event("control", {"vol_id": "mission-1", "command": "cancel"})
    del event[field]
    with pytest.raises(EventValidationError, match=field):
        decode_event(json.dumps(event), expected_type="control")


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 99])
def test_decode_event_rejects_unsupported_schema_versions(version):
    event = make_event("control", {"vol_id": "mission-1", "command": "cancel"})
    event["schema_version"] = version
    with pytest.raises(EventValidationError, match="schema_version"):
        decode_event(json.dumps(event), expected_type="control")


@pytest.mark.parametrize("event_type", ["mission", "orthomosaic", "image_tile", "tile_detection"])
def test_retired_compute_contracts_are_rejected(event_type):
    with pytest.raises(EventValidationError, match="unknown"):
        make_event(event_type, {"vol_id": "mission-1"})
    with pytest.raises(EventValidationError, match="unknown"):
        deterministic_event_id(event_type, "mission-1")


def test_decode_event_rejects_missing_domain_fields():
    event = make_event("control", {"vol_id": "mission-1", "command": "cancel"})
    del event["command"]
    with pytest.raises(EventValidationError, match="command"):
        decode_event(json.dumps(event), expected_type="control")


@pytest.mark.parametrize("unsafe_value", ["../escape", "a/b", "a\\b", ".", ".."])
def test_event_path_identifiers_reject_unsafe_segments(unsafe_value):
    with pytest.raises(EventValidationError, match="vol_id"):
        make_event("control", {"vol_id": unsafe_value, "command": "cancel"})
    with pytest.raises(EventValidationError, match="analysis_run_id"):
        make_event(
            "control",
            {"vol_id": "mission-safe", "analysis_run_id": unsafe_value, "command": "cancel"},
        )


@pytest.mark.parametrize("organization_id", ["../escape", "Upper", "a/b"])
def test_events_reject_unsafe_organization_identifiers(organization_id):
    with pytest.raises(EventValidationError, match="organization_id"):
        make_event(
            "status",
            {
                "vol_id": "mission-safe",
                "organization_id": organization_id,
                "status": "processing",
            },
        )


def test_status_events_accept_cancelled_and_exact_stage_identity():
    event = make_event(
        "status",
        {
            "vol_id": "mission-1",
            "service": "COLMAP",
            "step": "CANCELLED",
            "progress": 0,
            "status": "cancelled",
            "stage_run_id": "02fd284a-aa1c-489b-bb8f-f9eeed70e761",
        },
    )
    assert event["status"] == "cancelled"
    assert event["stage_run_id"] == "02fd284a-aa1c-489b-bb8f-f9eeed70e761"
    with pytest.raises(EventValidationError, match="unsupported status"):
        make_event("status", {"vol_id": "mission-1", "status": "stopped"})


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        ("control", {"vol_id": "mission-1", "command": "stop"}, "command"),
        ("status", {"vol_id": "mission-1", "status": "processing", "progress": "2"}, "progress"),
        ("status", {"vol_id": "mission-1", "status": "processing", "progress": 101}, "progress"),
        ("status", {"vol_id": "mission-1", "status": "processing", "stage_run_id": "bad"}, "UUID"),
    ],
)
def test_event_specific_schemas_reject_invalid_payloads(event_type, payload, message):
    with pytest.raises(EventValidationError, match=message):
        make_event(event_type, payload)


def test_event_contracts_preserve_additive_extensions():
    event = make_event(
        "control",
        {"vol_id": "mission-1", "command": "cancel", "future_parameter": {"enabled": True}},
    )
    assert event["future_parameter"] == {"enabled": True}


def test_discriminated_json_schema_covers_only_current_events():
    schema = kafka_event_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:droneai:kafka-events:v1"
    assert len(schema["oneOf"]) == len(EVENT_TYPES)
    assert {
        definition["properties"]["event_type"]["const"]
        for definition in schema["$defs"].values()
        if "event_type" in definition.get("properties", {})
    } == EVENT_TYPES == {"control", "status", "dead_letter"}


@pytest.mark.parametrize("requirements_file", ["requirements/colmap.txt", "requirements/ia-extra.txt"])
def test_stage_runtime_locks_include_pydantic(requirements_file):
    assert "pydantic==2.13.4" in (ROOT / requirements_file).read_text(encoding="utf-8")
