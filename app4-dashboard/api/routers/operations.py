"""Administrative reliability controls."""

from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.database import OutboxEvent, get_session

from ..security import require_admin

router = APIRouter(
    prefix="/operations",
    tags=["operations"],
    dependencies=[Depends(require_admin)],
)


class OutboxMutationRecord(Protocol):
    id: int
    event_id: str
    event_type: str
    topic: str
    attempts: int
    last_error: str | None
    status: str
    available_at: datetime
    dead_at: datetime | None
    locked_at: datetime | None
    locked_by: str | None


class DeadOutboxEvent(TypedDict):
    id: int
    event_id: str
    event_type: str
    topic: str
    attempts: int
    last_error: str | None
    dead_at: datetime | None


class ReplayResponse(TypedDict):
    status: str
    id: int


@router.get("/outbox/dead")
def list_dead_outbox_events(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[DeadOutboxEvent]:
    with get_session() as session:
        records = cast(
            list[OutboxMutationRecord],
            (
                session.query(OutboxEvent)
                .filter(OutboxEvent.status == "dead")
                .order_by(OutboxEvent.dead_at.desc(), OutboxEvent.id.desc())
                .limit(limit)
                .all()
            ),
        )
        return [
            {
                "id": record.id,
                "event_id": record.event_id,
                "event_type": record.event_type,
                "topic": record.topic,
                "attempts": record.attempts,
                "last_error": record.last_error,
                "dead_at": record.dead_at,
            }
            for record in records
        ]


@router.post("/outbox/{record_id}/replay")
def replay_dead_outbox_event(record_id: int) -> ReplayResponse:
    with get_session() as session:
        record = cast(
            OutboxMutationRecord | None,
            (session.query(OutboxEvent).filter(OutboxEvent.id == record_id).with_for_update().first()),
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Outbox event not found",
            )
        if record.status != "dead":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only dead outbox events can be replayed",
            )
        record.status = "pending"
        record.attempts = 0
        record.available_at = datetime.now(UTC)
        record.dead_at = None
        record.locked_at = None
        record.locked_by = None
    return {"status": "queued", "id": record_id}
