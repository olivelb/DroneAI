"""Construction helpers shared by transactional and leased inboxes."""

from __future__ import annotations

from typing import Any

from shared.database import InboxEvent


def build_inbox_record(
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
) -> InboxEvent:
    return InboxEvent(
        consumer_group=consumer_group,
        event_id=event["event_id"],
        event_type=event["event_type"],
        source_topic=source.get("topic"),
        source_partition=source.get("partition"),
        source_offset=source.get("offset"),
        payload=event,
        status="processing",
        attempts=1,
    )
