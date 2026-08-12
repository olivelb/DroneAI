"""Versioned, hash-bound object-storage contract for AI tile results."""

from __future__ import annotations

import hashlib
import json
import re
from hmac import compare_digest
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shared.validation import (
    MISSION_ID_PATTERN,
    SAFE_SEGMENT_PATTERN,
    validate_mission_id,
    validate_safe_segment,
)


TILE_RESULT_SCHEMA_VERSION: Literal[1] = 1
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
JsonObject = dict[str, Any]


class TileResultArtifact(BaseModel):
    """Self-describing immutable payload written by one IA tile worker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    vol_id: str = Field(min_length=3, max_length=64, pattern=MISSION_ID_PATTERN)
    analysis_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=SAFE_SEGMENT_PATTERN,
    )
    tile_index: int = Field(ge=0, strict=True)
    attempt: int = Field(ge=0, strict=True)
    detection_count: int = Field(ge=0, strict=True)
    model_manifest: JsonObject
    raw_detections: list[JsonObject] = Field(max_length=100_000)

    @model_validator(mode="after")
    def detection_count_matches_payload(self) -> TileResultArtifact:
        if self.detection_count != len(self.raw_detections):
            raise ValueError("detection_count does not match raw_detections")
        return self


def tile_result_s3_key(
    vol_id: str,
    analysis_run_id: str | None,
    tile_index: int,
    attempt: int,
) -> str:
    """Return the deterministic key used by both publisher and consumer."""
    validate_mission_id(vol_id)
    if analysis_run_id is not None:
        validate_safe_segment(analysis_run_id, field_name="analysis_run_id")
    run_component = analysis_run_id or "pipeline"
    return (
        f"missions/{vol_id}/ai-tile-results/{run_component}/"
        f"attempt_{attempt}/tile_{tile_index}.json"
    )


def build_tile_result_artifact(
    *,
    vol_id: str,
    analysis_run_id: str | None,
    tile_index: int,
    attempt: int,
    model_manifest: JsonObject,
    detections: list[JsonObject],
) -> JsonObject:
    """Validate and normalize a tile result before it reaches storage."""
    records = [{**detection, "tile_index": tile_index} for detection in detections]
    artifact = TileResultArtifact(
        schema_version=TILE_RESULT_SCHEMA_VERSION,
        vol_id=vol_id,
        analysis_run_id=analysis_run_id,
        tile_index=tile_index,
        attempt=attempt,
        detection_count=len(records),
        model_manifest=model_manifest,
        raw_detections=records,
    )
    return artifact.model_dump(mode="json")


def validate_tile_result_bytes(
    raw_payload: bytes,
    *,
    expected_sha256: str,
    expected_size: int,
    vol_id: str,
    analysis_run_id: str | None,
    tile_index: int,
    attempt: int,
    detection_count: int,
    model_manifest: JsonObject,
) -> TileResultArtifact:
    """Validate object integrity and bind its identity to the Kafka reference."""
    if expected_size < 0 or len(raw_payload) != expected_size:
        raise ValueError(
            f"tile result size mismatch: expected {expected_size}, got {len(raw_payload)}"
        )
    normalized_sha256 = expected_sha256.strip().lower()
    if SHA256_PATTERN.fullmatch(normalized_sha256) is None:
        raise ValueError("tile result reference has an invalid SHA-256")
    digest = hashlib.sha256(raw_payload).hexdigest()
    if not compare_digest(digest, normalized_sha256):
        raise ValueError(
            f"tile result SHA-256 mismatch: expected {normalized_sha256}, got {digest}"
        )
    try:
        value = json.loads(raw_payload)
        artifact = TileResultArtifact.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"invalid tile result artifact: {error}") from error
    expected_identity = (vol_id, analysis_run_id, tile_index, attempt)
    actual_identity = (
        artifact.vol_id,
        artifact.analysis_run_id,
        artifact.tile_index,
        artifact.attempt,
    )
    if actual_identity != expected_identity:
        raise ValueError("tile result identity does not match its Kafka reference")
    if artifact.detection_count != detection_count:
        raise ValueError("tile result detection count does not match its Kafka reference")
    if artifact.model_manifest != model_manifest:
        raise ValueError("tile result model manifest does not match its Kafka reference")
    return artifact
