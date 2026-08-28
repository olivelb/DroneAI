"""Versioned JSON event contracts shared by every pipeline service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, UTC
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from shared.event_schemas import (
    EVENT_MODELS,
    EVENT_TYPES,
    PIPELINE_STATUSES,
)
from shared.tenancy import validate_organization_id

SCHEMA_VERSION = 1


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


def deterministic_tenant_event_id(
    event_type: str,
    organization_id: str,
    *identity: object,
) -> str:
    """Build an idempotency identity that cannot collide across tenants."""

    return deterministic_event_id(
        event_type,
        validate_organization_id(organization_id),
        *identity,
    )


def tenant_correlation_id(organization_id: str, subject_id: str) -> str:
    """Build a trace correlation that is unambiguous across tenants."""

    return f"{validate_organization_id(organization_id)}:{subject_id}"


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
    organization_id = payload.get("organization_id")
    default_correlation_id = (
        f"{organization_id}:{payload['vol_id']}"
        if organization_id and payload.get("vol_id")
        else str(payload.get("vol_id") or uuid4())
    )
    event.update(
        schema_version=SCHEMA_VERSION,
        event_type=event_type,
        event_id=event_id or f"{event_type}:{uuid4()}",
        correlation_id=correlation_id or event_id or default_correlation_id,
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
    event_type = event.get("event_type")
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise EventValidationError(f"unknown or missing event_type: {event_type}")
    if expected_type and event_type != expected_type:
        raise EventValidationError(f"expected event_type={expected_type}, got {event_type}")
    version = event.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise EventValidationError(f"unsupported schema_version={version}; expected {SCHEMA_VERSION}")
    if event_type == "status" and event.get("status") not in PIPELINE_STATUSES:
        allowed = ", ".join(sorted(PIPELINE_STATUSES))
        raise EventValidationError(
            f"status event has unsupported status={event.get('status')!r}; expected one of: {allowed}"
        )
    if type(event.get("attempt")) is not int or event["attempt"] < 0:
        raise EventValidationError("attempt must be a non-negative integer")
    try:
        validated = EVENT_MODELS[event_type].model_validate(event)
    except ValidationError as error:
        raise EventValidationError(
            f"invalid {event_type} event: {error}"
        ) from error
    return cast(
        dict[str, Any],
        validated.model_dump(mode="json", exclude_unset=True),
    )


def decode_event(value: bytes | str, *, expected_type: str) -> dict[str, Any]:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EventValidationError(f"invalid JSON event: {error}") from error
    return validate_event(payload, expected_type=expected_type)
