"""Pydantic contracts and JSON Schema generation for Kafka events."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from shared.quality_profiles import QualityProfileId
from shared.stage_contracts import (
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    StageId,
    validate_stage_selection,
)
from shared.validation import MISSION_ID_PATTERN, SAFE_SEGMENT_PATTERN


JsonObject = dict[str, Any]
PIPELINE_STATUSES = frozenset({"processing", "success", "error", "cancelled"})
MissionId = Annotated[
    str,
    Field(min_length=3, max_length=64, pattern=MISSION_ID_PATTERN),
]
RunId = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=SAFE_SEGMENT_PATTERN),
]


class EventEnvelope(BaseModel):
    """Trace envelope shared by every version-one event."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1]
    event_type: str
    event_id: str = Field(min_length=1, max_length=512)
    correlation_id: str = Field(min_length=1, max_length=512)
    causation_id: str | None = Field(default=None, max_length=512)
    attempt: int = Field(default=0, ge=0, strict=True)
    emitted_at: datetime


class MissionEvent(EventEnvelope):
    event_type: Literal["mission"]
    vol_id: MissionId
    input_dataset: str | None = Field(default=None, max_length=1024)
    pipeline: Literal["modern", "legacy"] | None = None
    quality_profile: QualityProfileId | None = None
    quality_profile_version: int | None = Field(default=None, ge=1, strict=True)
    quality_profile_overrides: JsonObject | None = None
    tile_size: int | None = Field(default=None, ge=256, le=4096, strict=True)
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    ai_backend: Literal["yolo", "sam3"] | None = None
    ai_model_variant: str | None = Field(default=None, max_length=128)
    ai_model_manifest: JsonObject | None = None
    sam_prompt: str | None = Field(default=None, max_length=256)
    classes: list[str] | None = Field(default=None, max_length=20)
    colmap_params: JsonObject | None = None
    work_drive: str | None = Field(default=None, max_length=256)
    owner_subject: str | None = Field(default=None, max_length=256)
    phases: list[StageId] | None = Field(default=None, min_length=1, max_length=5)
    stage_run_id: str | None = Field(default=None, max_length=36)
    upstream_artifact_ids: dict[StageId, str] | None = None
    stage_parameters: JsonObject | None = None

    @model_validator(mode="after")
    def validate_stage_contract(self) -> MissionEvent:
        phases = list(self.phases or STAGE_ORDER)
        upstream = self.upstream_artifact_ids or {}
        validate_stage_selection(phases, upstream)
        for artifact_id in upstream.values():
            UUID(artifact_id)
        if self.stage_run_id is None:
            if upstream:
                raise ValueError(
                    "upstream artifacts are only accepted for a stage-run command"
                )
            return self
        UUID(self.stage_run_id)
        if len(phases) != 1:
            raise ValueError("a stage-run command must select exactly one phase")
        required = set(STAGE_DEPENDENCIES[phases[0]])
        if set(upstream) != required:
            raise ValueError(
                "a stage-run command must identify every direct dependency"
            )
        return self


class InferenceEventEnvelope(EventEnvelope):
    """Inference settings propagated across the processing and IA stages."""

    analysis_run_id: RunId | None = None
    classes: list[str] | None = Field(default=None, max_length=20)
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    ai_backend: Literal["yolo", "sam3"] | None = None
    ai_model_variant: str | None = Field(default=None, max_length=128)
    sam_prompt: str | None = Field(default=None, max_length=256)


class OrthomosaicEvent(InferenceEventEnvelope):
    event_type: Literal["orthomosaic"]
    vol_id: MissionId
    ortho_s3_key: str | None = Field(default=None, max_length=2048)
    ortho_path: str | None = Field(default=None, max_length=2048)
    tile_size: int | None = Field(default=None, ge=256, le=4096, strict=True)

    @model_validator(mode="after")
    def require_orthomosaic_location(self) -> OrthomosaicEvent:
        if not (self.ortho_s3_key or self.ortho_path):
            raise ValueError("orthomosaic event requires ortho_s3_key or ortho_path")
        return self


class ImageTileEvent(InferenceEventEnvelope):
    event_type: Literal["image_tile"]
    vol_id: MissionId
    tile_index: int = Field(ge=0, strict=True)
    tile_s3_key: str | None = Field(default=None, max_length=2048)
    tile_path: str | None = Field(default=None, max_length=2048)
    offset_x: int | None = Field(default=None, ge=0, strict=True)
    offset_y: int | None = Field(default=None, ge=0, strict=True)
    total_tiles: int | None = Field(default=None, ge=1, strict=True)
    ortho_transform: list[Any] | None = Field(default=None, min_length=6, max_length=9)
    ortho_crs: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def require_tile_location(self) -> ImageTileEvent:
        if not (self.tile_s3_key or self.tile_path):
            raise ValueError("image_tile event requires tile_s3_key or tile_path")
        return self


class TileDetectionEvent(EventEnvelope):
    event_type: Literal["tile_detection"]
    vol_id: MissionId
    tile_index: int = Field(ge=0, strict=True)
    detections: list[JsonObject] | None = None
    analysis_run_id: RunId | None = None
    model_manifest: JsonObject | None = None
    result_s3_key: str | None = Field(default=None, max_length=2048)
    result_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    result_size_bytes: int | None = Field(default=None, ge=1, strict=True)
    detection_count: int | None = Field(default=None, ge=0, strict=True)
    result_schema_version: Literal[1] | None = None

    @model_validator(mode="after")
    def require_inline_or_referenced_result(self) -> TileDetectionEvent:
        inline = self.detections is not None
        reference_values = (
            self.result_s3_key,
            self.result_sha256,
            self.result_size_bytes,
            self.detection_count,
            self.result_schema_version,
        )
        referenced = any(value is not None for value in reference_values)
        if inline == referenced:
            raise ValueError(
                "tile_detection requires exactly one of detections or a result reference"
            )
        if referenced and any(value is None for value in reference_values):
            raise ValueError("tile_detection result reference is incomplete")
        if referenced and self.model_manifest is None:
            raise ValueError("tile_detection result reference requires model_manifest")
        return self


class StatusEvent(EventEnvelope):
    event_type: Literal["status"]
    vol_id: MissionId
    status: Literal["processing", "success", "error", "cancelled"]
    service: str | None = Field(default=None, max_length=64)
    step: str | None = Field(default=None, max_length=128)
    progress: int | None = Field(default=None, ge=0, le=100, strict=True)
    log: str | None = Field(default=None, max_length=16_384)
    details: JsonObject | None = None
    stage_run_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode="after")
    def validate_stage_run_id(self) -> StatusEvent:
        if self.stage_run_id is not None:
            UUID(self.stage_run_id)
        return self


class ControlEvent(EventEnvelope):
    event_type: Literal["control"]
    vol_id: MissionId
    command: Literal["cancel"]
    analysis_run_id: RunId | None = None


class DeadLetterEvent(EventEnvelope):
    event_type: Literal["dead_letter"]
    source_topic: str = Field(min_length=1, max_length=256)
    source_partition: int = Field(ge=0, strict=True)
    source_offset: int = Field(ge=0, strict=True)
    consumer_group: str = Field(min_length=1, max_length=256)
    expected_event_type: str = Field(min_length=1, max_length=64)
    attempts: int = Field(ge=1, strict=True)
    error: str = Field(min_length=1, max_length=16_384)
    original_value: str = Field(max_length=2_000_000)


KafkaEvent = Annotated[
    MissionEvent
    | OrthomosaicEvent
    | ImageTileEvent
    | TileDetectionEvent
    | StatusEvent
    | ControlEvent
    | DeadLetterEvent,
    Field(discriminator="event_type"),
]

EVENT_MODELS: dict[str, type[EventEnvelope]] = {
    "mission": MissionEvent,
    "orthomosaic": OrthomosaicEvent,
    "image_tile": ImageTileEvent,
    "tile_detection": TileDetectionEvent,
    "status": StatusEvent,
    "control": ControlEvent,
    "dead_letter": DeadLetterEvent,
}
EVENT_TYPES = frozenset(EVENT_MODELS)
_EVENT_ADAPTER: TypeAdapter[KafkaEvent] = TypeAdapter(KafkaEvent)


def kafka_event_json_schema() -> JsonObject:
    """Return the discriminated JSON Schema for every supported event."""

    schema = _EVENT_ADAPTER.json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:droneai:kafka-events:v1",
        "title": "DroneAI Kafka Event v1",
        **schema,
    }
