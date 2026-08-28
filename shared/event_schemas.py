"""Pydantic contracts and JSON Schema generation for Kafka events."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from shared.validation import MISSION_ID_PATTERN, SAFE_SEGMENT_PATTERN
from shared.tenancy import ORGANIZATION_ID_PATTERN


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
    attempt: int = Field(ge=0, strict=True)
    emitted_at: datetime
    organization_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=ORGANIZATION_ID_PATTERN,
    )


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
    StatusEvent
    | ControlEvent
    | DeadLetterEvent,
    Field(discriminator="event_type"),
]

EVENT_MODELS: dict[str, type[EventEnvelope]] = {
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
