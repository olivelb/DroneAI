"""Versioned JSON event contracts shared by every pipeline service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
EVENT_TYPES = {
    "mission",
    "orthomosaic",
    "image_tile",
    "tile_detection",
    "status",
    "control",
    "dead_letter",
}
REQUIRED_FIELDS = {
    "mission": ("vol_id",),
    "orthomosaic": ("vol_id",),
    "image_tile": ("vol_id", "tile_index"),
    "tile_detection": ("vol_id", "tile_index", "detections"),
    "status": ("vol_id", "status"),
    "control": ("vol_id", "command"),
    "dead_letter": ("source_topic", "consumer_group", "error"),
}
PIPELINE_STATUSES = frozenset({"processing", "success", "error", "cancelled"})


class EventValidationError(ValueError):
    """Raised when an event does not satisfy its declared contract."""


def deterministic_event_id(event_type: str, *identity: object) -> str:
    if event_type not in EVENT_TYPES:
        raise EventValidationError(f"unknown event_type: {event_type}")
    canonical = json.dumps(
        [event_type, *identity],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{event_type}:{digest[:32]}"


def make_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    event = dict(payload)
    event.update(
        schema_version=SCHEMA_VERSION,
        event_type=event_type,
        event_id=event_id or f"{event_type}:{uuid4()}",
        correlation_id=correlation_id or event_id or str(payload.get("vol_id") or uuid4()),
        causation_id=causation_id,
        attempt=attempt,
        emitted_at=datetime.now(UTC).isoformat(),
    )
    return validate_event(event, expected_type=event_type)


def validate_event(
    event: dict[str, Any],
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise EventValidationError("event must be a JSON object")
    event_type = event.get("event_type") or expected_type
    if event_type not in EVENT_TYPES:
        raise EventValidationError(f"unknown or missing event_type: {event_type}")
    if expected_type and event_type != expected_type:
        raise EventValidationError(f"expected event_type={expected_type}, got {event_type}")
    version = event.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise EventValidationError(f"unsupported schema_version={version}; expected {SCHEMA_VERSION}")
    missing = [field for field in REQUIRED_FIELDS[event_type] if field not in event or event[field] is None]
    if event_type == "orthomosaic" and not (event.get("ortho_s3_key") or event.get("ortho_path")):
        missing.append("ortho_s3_key|ortho_path")
    if event_type == "image_tile" and not (event.get("tile_s3_key") or event.get("tile_path")):
        missing.append("tile_s3_key|tile_path")
    if missing:
        raise EventValidationError(f"{event_type} event missing required fields: {', '.join(missing)}")
    if event_type == "status" and event.get("status") not in PIPELINE_STATUSES:
        allowed = ", ".join(sorted(PIPELINE_STATUSES))
        raise EventValidationError(
            f"status event has unsupported status={event.get('status')!r}; expected one of: {allowed}"
        )
    normalized = dict(event)
    normalized["schema_version"] = version
    normalized["event_type"] = event_type
    if not normalized.get("event_id"):
        identity = [
            normalized.get("vol_id"),
            normalized.get("tile_index"),
            normalized.get("command"),
        ]
        normalized["event_id"] = deterministic_event_id(event_type, *identity)
    normalized.setdefault("correlation_id", normalized.get("vol_id") or normalized["event_id"])
    normalized.setdefault("causation_id", None)
    normalized.setdefault("attempt", 0)
    normalized.setdefault("emitted_at", datetime.now(UTC).isoformat())
    if not isinstance(normalized["attempt"], int) or normalized["attempt"] < 0:
        raise EventValidationError("attempt must be a non-negative integer")
    return normalized


def decode_event(value: bytes | str, *, expected_type: str) -> dict[str, Any]:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EventValidationError(f"invalid JSON event: {error}") from error
    return validate_event(payload, expected_type=expected_type)
