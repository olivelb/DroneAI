import hashlib
import json

import pytest

from shared.model_provenance import build_model_manifest
from shared.tile_results import (
    build_tile_result_artifact,
    tile_result_s3_key,
    validate_tile_result_bytes,
)


def _manifest():
    return build_model_manifest(
        backend="yolo",
        repository="ultralytics/assets",
        revision="v8.4.0",
        artifact="yolo26l-obb.pt",
        artifact_sha256="a" * 64,
        libraries={"ultralytics": "8.4.0"},
        runtime={"device": "cuda"},
        inference={"confidence": 0.3},
    )


def _artifact_bytes():
    artifact = build_tile_result_artifact(
        vol_id="mission-1",
        analysis_run_id="run-1",
        tile_index=4,
        attempt=2,
        model_manifest=_manifest(),
        detections=[{"class_name": "truck", "confidence": 0.9}],
    )
    return json.dumps(artifact, separators=(",", ":")).encode("utf-8")


def _validate(raw_payload, **overrides):
    arguments = {
        "expected_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "expected_size": len(raw_payload),
        "vol_id": "mission-1",
        "analysis_run_id": "run-1",
        "tile_index": 4,
        "attempt": 2,
        "detection_count": 1,
        "model_manifest": _manifest(),
    }
    arguments.update(overrides)
    return validate_tile_result_bytes(raw_payload, **arguments)


def test_tile_result_artifact_is_versioned_and_bound_to_its_reference():
    raw_payload = _artifact_bytes()

    artifact = _validate(raw_payload)

    assert artifact.schema_version == 1
    assert artifact.detection_count == 1
    assert artifact.raw_detections[0]["tile_index"] == 4
    assert tile_result_s3_key("mission-1", "run-1", 4, 2) == (
        "missions/mission-1/ai-tile-results/run-1/attempt_2/tile_4.json"
    )
    assert tile_result_s3_key("mission-1", None, 4, 2) == (
        "missions/mission-1/ai-tile-results/pipeline/attempt_2/tile_4.json"
    )
    assert tile_result_s3_key(
        "mission-1",
        "run-1",
        4,
        2,
        organization_id="acme-survey",
        workspace_prefix="organizations/acme-survey/missions/mission-1",
    ) == (
        "organizations/acme-survey/missions/mission-1/"
        "ai-tile-results/run-1/attempt_2/tile_4.json"
    )


def test_tenant_tile_result_requires_the_durable_workspace_binding():
    with pytest.raises(ValueError, match="no workspace prefix"):
        tile_result_s3_key(
            "mission-1",
            None,
            4,
            2,
            organization_id="acme-survey",
        )


@pytest.mark.parametrize("unsafe_value", ["../escape", "a/b", "a\\b", ".", ".."])
def test_tile_result_key_rejects_unsafe_path_segments(unsafe_value):
    with pytest.raises(ValueError):
        tile_result_s3_key(unsafe_value, "run-1", 4, 2)
    with pytest.raises(ValueError):
        tile_result_s3_key("mission-1", unsafe_value, 4, 2)


def test_tile_result_artifact_rejects_tampering():
    raw_payload = _artifact_bytes()

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _validate(
            raw_payload + b" ",
            expected_size=len(raw_payload) + 1,
            expected_sha256=hashlib.sha256(raw_payload).hexdigest(),
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"tile_index": 5}, "identity"),
        ({"detection_count": 2}, "detection count"),
        ({"model_manifest": {"backend": "sam3"}}, "model manifest"),
    ],
)
def test_tile_result_artifact_rejects_reference_mismatches(override, message):
    with pytest.raises(ValueError, match=message):
        _validate(_artifact_bytes(), **override)
