"""Administrative reliability controls."""

from datetime import UTC, datetime
from typing import Annotated, Protocol, TypedDict, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from shared.database import OUTBOX_EVENT_STATUSES, OutboxEvent, get_session

from ..security import Principal, bind_tenant_context, require_admin

router = APIRouter(
    prefix="/operations",
    tags=["operations"],
    dependencies=[Depends(require_admin), Depends(bind_tenant_context)],
)


class OutboxMutationRecord(Protocol):
    id: int
    organization_id: str
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
    message_key: str | None
    published_at: datetime | None


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


class OutboxDeliveryStatus(TypedDict):
    id: int
    event_id: str
    event_type: str
    topic: str
    message_key: str | None
    status: str
    attempts: int
    published_at: datetime | None
    last_error: str | None


@router.get("/outbox")
def list_outbox_delivery_status(
    principal: Annotated[Principal, Depends(require_admin)],
    delivery_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[OutboxDeliveryStatus]:
    with get_session() as session:
        query = session.query(OutboxEvent).filter(
            OutboxEvent.organization_id == principal.organization_id,
        )
        if delivery_status is not None:
            if delivery_status not in OUTBOX_EVENT_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Unsupported outbox status",
                )
            query = query.filter(OutboxEvent.status == delivery_status)
        records = cast(
            list[OutboxMutationRecord],
            query.order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .limit(limit)
            .all(),
        )
        return [
            {
                "id": record.id,
                "event_id": record.event_id,
                "event_type": record.event_type,
                "topic": record.topic,
                "message_key": record.message_key,
                "status": record.status,
                "attempts": record.attempts,
                "published_at": record.published_at,
                "last_error": record.last_error,
            }
            for record in records
        ]


@router.get("/outbox/dead")
def list_dead_outbox_events(
    principal: Annotated[Principal, Depends(require_admin)],
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[DeadOutboxEvent]:
    with get_session() as session:
        records = cast(
            list[OutboxMutationRecord],
            (
                session.query(OutboxEvent)
                .filter(
                    OutboxEvent.organization_id == principal.organization_id,
                    OutboxEvent.status == "dead",
                )
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
def replay_dead_outbox_event(
    record_id: int,
    principal: Annotated[Principal, Depends(require_admin)],
) -> ReplayResponse:
    with get_session() as session:
        record = cast(
            OutboxMutationRecord | None,
            (
                session.query(OutboxEvent)
                .filter(
                    OutboxEvent.id == record_id,
                    OutboxEvent.organization_id == principal.organization_id,
                )
                .with_for_update()
                .first()
            ),
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
