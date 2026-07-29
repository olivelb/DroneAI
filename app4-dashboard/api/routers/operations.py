"""Administrative reliability controls."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.database import OutboxEvent, get_session

from ..security import require_admin

router = APIRouter(
    prefix="/operations",
    tags=["operations"],
    dependencies=[Depends(require_admin)],
)


@router.get("/outbox/dead")
def list_dead_outbox_events(
    limit: int = Query(default=100, ge=1, le=1000),
):
    with get_session() as session:
        records = (
            session.query(OutboxEvent)
            .filter(OutboxEvent.status == "dead")
            .order_by(OutboxEvent.dead_at.desc(), OutboxEvent.id.desc())
            .limit(limit)
            .all()
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
def replay_dead_outbox_event(record_id: int):
    with get_session() as session:
        record = (
            session.query(OutboxEvent)
            .filter(OutboxEvent.id == record_id)
            .with_for_update()
            .first()
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
        record.available_at = datetime.now(timezone.utc)
        record.dead_at = None
        record.locked_at = None
        record.locked_by = None
    return {"status": "queued", "id": record_id}
