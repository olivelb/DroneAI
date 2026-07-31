from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import InboxEvent, OutboxEvent
from shared.event_contracts import make_event
from shared.inbox_outbox import (
    InboxResult,
    dispatch_outbox_batch,
    enqueue_outbox,
    process_inbox_transaction,
)
from shared.kafka_reliability import RetryPolicy


@pytest.fixture
def session_scope():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InboxEvent.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope


def status_event(event_id="status:one"):
    return make_event(
        "status",
        {
            "vol_id": "mission-1",
            "service": "IA",
            "step": "DETECTING",
            "progress": 50,
            "status": "processing",
        },
        event_id=event_id,
        correlation_id="mission-1",
    )


def test_inbox_and_outbox_share_one_transaction_and_deduplicate(
    session_scope,
):
    calls = []

    def handle(session, event):
        calls.append(event["event_id"])
        enqueue_outbox(
            session,
            topic="next-stage",
            key=event["vol_id"],
            event=make_event(
                "mission",
                {
                    "vol_id": event["vol_id"],
                    "input_dataset": "datasets/mission-1",
                },
                event_id="mission:next",
            ),
        )

    first = process_inbox_transaction(
        session_scope,
        consumer_group="dashboard-api",
        event=status_event(),
        source={"topic": "pipeline-status", "partition": 1, "offset": 10},
        handler=handle,
    )
    second = process_inbox_transaction(
        session_scope,
        consumer_group="dashboard-api",
        event=status_event(),
        source={"topic": "pipeline-status", "partition": 1, "offset": 10},
        handler=handle,
    )

    assert first == InboxResult.PROCESSED
    assert second == InboxResult.DUPLICATE
    assert calls == ["status:one"]
    with session_scope() as session:
        assert session.query(InboxEvent).count() == 1
        assert session.query(OutboxEvent).count() == 1
        assert session.query(InboxEvent).one().status == "completed"


def test_handler_failure_rolls_back_inbox_and_outbox(session_scope):
    def fail(session, event):
        enqueue_outbox(
            session,
            topic="next-stage",
            event=make_event(
                "mission",
                {"vol_id": event["vol_id"]},
                event_id="mission:rolled-back",
            ),
        )
        raise RuntimeError("domain mutation failed")

    with pytest.raises(RuntimeError, match="domain mutation failed"):
        process_inbox_transaction(
            session_scope,
            consumer_group="dashboard-api",
            event=status_event(),
            source={"topic": "pipeline-status", "partition": 0, "offset": 2},
            handler=fail,
        )

    with session_scope() as session:
        assert session.query(InboxEvent).count() == 0
        assert session.query(OutboxEvent).count() == 0


def test_dispatcher_marks_successful_publication(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        enqueue_outbox(
            session,
            topic="vols-bruts",
            key="mission-1",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:dispatch",
            ),
            now=now,
        )

    published = []
    result = dispatch_outbox_batch(
        session_scope,
        publisher=lambda topic, payload, key: published.append(
            (topic, payload["event_id"], key)
        ),
        worker_id="test-worker",
        now=now,
    )

    assert result == {
        "selected": 1,
        "published": 1,
        "failed": 0,
        "dead": 0,
    }
    assert published == [("vols-bruts", "mission:dispatch", "mission-1")]
    with session_scope() as session:
        record = session.query(OutboxEvent).one()
        assert record.status == "published"
        assert record.attempts == 1
        assert record.published_at.replace(tzinfo=timezone.utc) == now
        assert record.locked_by is None


def test_dispatcher_publishes_outside_the_claim_transaction(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        enqueue_outbox(
            session,
            topic="vols-bruts",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:outside-transaction",
            ),
            now=now,
        )

    active_sessions = 0

    @contextmanager
    def tracked_scope():
        nonlocal active_sessions
        with session_scope() as session:
            active_sessions += 1
            try:
                yield session
            finally:
                active_sessions -= 1

    def publish(*_args):
        assert active_sessions == 0

    result = dispatch_outbox_batch(
        tracked_scope,
        publisher=publish,
        worker_id="test-worker",
        now=now,
    )

    assert result["published"] == 1


def test_dispatcher_reclaims_an_expired_publication_lease(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        record = enqueue_outbox(
            session,
            topic="vols-bruts",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:expired-lease",
            ),
            now=now,
        )
        record.status = "publishing"
        record.locked_by = "crashed-worker"
        record.locked_at = now - timedelta(minutes=10)

    published = []
    result = dispatch_outbox_batch(
        session_scope,
        publisher=lambda topic, payload, key: published.append(
            (topic, payload["event_id"], key)
        ),
        worker_id="replacement-worker",
        now=now,
        lease_seconds=60,
    )

    assert result["published"] == 1
    assert published[0][1] == "mission:expired-lease"


def test_dispatcher_schedules_failed_publication_for_retry(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        enqueue_outbox(
            session,
            topic="vols-bruts",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:retry",
            ),
            now=now,
        )

    def fail(_topic, _payload, _key):
        raise OSError("broker unavailable")

    first = dispatch_outbox_batch(
        session_scope,
        publisher=fail,
        worker_id="test-worker",
        retry_policy=RetryPolicy(3, 2, 10),
        now=now,
    )
    too_early = dispatch_outbox_batch(
        session_scope,
        publisher=lambda *_args: None,
        worker_id="test-worker",
        retry_policy=RetryPolicy(3, 2, 10),
        now=now + timedelta(seconds=1),
    )
    retried = dispatch_outbox_batch(
        session_scope,
        publisher=lambda *_args: None,
        worker_id="test-worker",
        retry_policy=RetryPolicy(3, 2, 10),
        now=now + timedelta(seconds=2),
    )

    assert first == {
        "selected": 1,
        "published": 0,
        "failed": 1,
        "dead": 0,
    }
    assert too_early["selected"] == 0
    assert retried == {
        "selected": 1,
        "published": 1,
        "failed": 0,
        "dead": 0,
    }
    with session_scope() as session:
        record = session.query(OutboxEvent).one()
        assert record.status == "published"
        assert record.attempts == 2


def test_dispatcher_dead_letters_outbox_after_retry_budget(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        enqueue_outbox(
            session,
            topic="vols-bruts",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:dead",
            ),
            now=now,
        )

    def fail(*_args):
        raise OSError("broker unavailable")

    policy = RetryPolicy(2, 0, 0)
    dispatch_outbox_batch(
        session_scope,
        publisher=fail,
        worker_id="test-worker",
        retry_policy=policy,
        now=now,
    )
    result = dispatch_outbox_batch(
        session_scope,
        publisher=fail,
        worker_id="test-worker",
        retry_policy=policy,
        now=now,
    )

    assert result == {
        "selected": 1,
        "published": 0,
        "failed": 0,
        "dead": 1,
    }
    with session_scope() as session:
        record = session.query(OutboxEvent).one()
        assert record.status == "dead"
        assert record.dead_at is not None
