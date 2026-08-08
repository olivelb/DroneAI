from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from shared import inbox_outbox
from shared.database import InboxEvent, OutboxEvent
from shared.event_contracts import make_event
from shared.inbox_outbox import (
    InboxResult,
    default_worker_id,
    deliver_outbox_event,
    dispatch_outbox_batch,
    enqueue_outbox,
    make_inbox_handler,
    process_inbox_transaction,
    run_outbox_dispatcher,
)
from shared.kafka_reliability import MessageDeferredError, RetryPolicy
from shared.worker_inbox import (
    InboxLeaseLostError,
    WorkerInboxResult,
    claim_inbox_work,
    make_inbox_work_handler,
    process_inbox_work,
    renew_inbox_claim,
)


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


def test_enqueue_outbox_is_idempotent_by_event_id(session_scope):
    with session_scope() as session:
        first = enqueue_outbox(
            session,
            topic="next-stage",
            event=status_event("status:idempotent"),
        )
        second = enqueue_outbox(
            session,
            topic="ignored-on-duplicate",
            event=status_event("status:idempotent"),
        )

        assert second is first
        assert session.query(OutboxEvent).count() == 1


def test_incomplete_inbox_receipt_is_retried(session_scope):
    event = status_event("status:incomplete")
    with session_scope() as session:
        session.add(
            InboxEvent(
                consumer_group="dashboard-api",
                event_id=event["event_id"],
                event_type=event["event_type"],
                payload=event,
                status="processing",
                attempts=1,
                last_error="worker stopped",
            )
        )

    handled = []
    result = process_inbox_transaction(
        session_scope,
        consumer_group="dashboard-api",
        event=event,
        source={"topic": "pipeline-status", "partition": 0, "offset": 9},
        handler=lambda _session, payload: handled.append(payload["event_id"]),
    )

    assert result == InboxResult.PROCESSED
    assert handled == ["status:incomplete"]
    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert record.status == "completed"
        assert record.attempts == 2
        assert record.last_error is None


def test_concurrent_completed_inbox_insert_is_a_duplicate(session_scope):
    event = status_event("status:concurrent")
    process_inbox_transaction(
        session_scope,
        consumer_group="dashboard-api",
        event=event,
        source={"topic": "pipeline-status", "partition": 0, "offset": 4},
        handler=lambda *_args: None,
    )

    collision = IntegrityError("insert", {}, RuntimeError("unique violation"))
    with patch.object(inbox_outbox, "_process_inbox", side_effect=collision):
        result = process_inbox_transaction(
            session_scope,
            consumer_group="dashboard-api",
            event=event,
            source={"topic": "pipeline-status", "partition": 0, "offset": 4},
            handler=lambda *_args: None,
        )

    assert result == InboxResult.DUPLICATE


def test_make_inbox_handler_captures_message_location(session_scope):
    message = SimpleNamespace(
        topic=lambda: "pipeline-status",
        partition=lambda: 3,
        offset=lambda: 17,
    )
    handled = []
    handler = make_inbox_handler(
        session_scope,
        consumer_group="dashboard-api",
        message=message,
        handler=lambda _session, event: handled.append(event["event_id"]),
    )

    handler(status_event("status:wrapped"))

    assert handled == ["status:wrapped"]
    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert (record.source_topic, record.source_partition, record.source_offset) == (
            "pipeline-status",
            3,
            17,
        )


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


def test_long_running_inbox_completes_and_suppresses_duplicate(session_scope):
    event = status_event("status:worker-complete")
    handled = []

    first = process_inbox_work(
        session_scope,
        consumer_group="processing-workers",
        event=event,
        source={"topic": "tile-detections", "partition": 0, "offset": 8},
        handler=lambda payload: handled.append(payload["event_id"]),
        worker_id="worker-a",
        heartbeat_interval_seconds=0,
    )
    second = process_inbox_work(
        session_scope,
        consumer_group="processing-workers",
        event=event,
        source={"topic": "tile-detections", "partition": 1, "offset": 3},
        handler=lambda payload: handled.append(payload["event_id"]),
        worker_id="worker-b",
        heartbeat_interval_seconds=0,
    )

    assert first == WorkerInboxResult.PROCESSED
    assert second == WorkerInboxResult.DUPLICATE
    assert handled == ["status:worker-complete"]
    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert record.status == "completed"
        assert record.locked_at is None
        assert record.locked_by is None


def test_failed_worker_inbox_is_reclaimable(session_scope):
    event = status_event("status:worker-retry")

    with pytest.raises(RuntimeError, match="inference failed"):
        process_inbox_work(
            session_scope,
            consumer_group="ia-workers",
            event=event,
            source={"topic": "image-tiles", "partition": 0, "offset": 12},
            handler=lambda _payload: (_ for _ in ()).throw(
                RuntimeError("inference failed")
            ),
            worker_id="worker-a",
            heartbeat_interval_seconds=0,
        )

    with session_scope() as session:
        failed = session.query(InboxEvent).one()
        assert failed.status == "failed"
        assert failed.attempts == 1
        assert failed.last_error == "RuntimeError: inference failed"

    result = process_inbox_work(
        session_scope,
        consumer_group="ia-workers",
        event=event,
        source={"topic": "image-tiles", "partition": 0, "offset": 12},
        handler=lambda _payload: None,
        worker_id="worker-b",
        heartbeat_interval_seconds=0,
    )

    assert result == WorkerInboxResult.PROCESSED
    with session_scope() as session:
        completed = session.query(InboxEvent).one()
        assert completed.status == "completed"
        assert completed.attempts == 2
        assert completed.last_error is None


def test_worker_inbox_lease_blocks_overlap_and_allows_stale_takeover(
    session_scope,
):
    event = status_event("status:leased")
    started = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    source = {"topic": "vols-bruts", "partition": 2, "offset": 20}

    first = claim_inbox_work(
        session_scope,
        consumer_group="colmap-workers",
        event=event,
        source=source,
        worker_id="worker-a",
        lease_seconds=60,
        now=started,
    )
    overlap = claim_inbox_work(
        session_scope,
        consumer_group="colmap-workers",
        event=event,
        source=source,
        worker_id="worker-b",
        lease_seconds=60,
        now=started + timedelta(seconds=30),
    )
    takeover = claim_inbox_work(
        session_scope,
        consumer_group="colmap-workers",
        event=event,
        source=source,
        worker_id="worker-b",
        lease_seconds=60,
        now=started + timedelta(seconds=61),
    )

    assert first == WorkerInboxResult.PROCESSED
    assert overlap == WorkerInboxResult.IN_PROGRESS
    assert takeover == WorkerInboxResult.PROCESSED
    assert not renew_inbox_claim(
        session_scope,
        consumer_group="colmap-workers",
        event_id=event["event_id"],
        worker_id="worker-a",
    )
    assert renew_inbox_claim(
        session_scope,
        consumer_group="colmap-workers",
        event_id=event["event_id"],
        worker_id="worker-b",
    )
    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert record.attempts == 2
        assert record.locked_by == "worker-b"


def test_active_worker_inbox_defers_kafka_delivery(session_scope):
    event = status_event("status:active")
    source = {"topic": "image-tiles", "partition": 0, "offset": 7}
    assert claim_inbox_work(
        session_scope,
        consumer_group="ia-workers",
        event=event,
        source=source,
        worker_id="worker-a",
        lease_seconds=60,
    ) == WorkerInboxResult.PROCESSED

    with pytest.raises(MessageDeferredError, match="already leased"):
        process_inbox_work(
            session_scope,
            consumer_group="ia-workers",
            event=event,
            source=source,
            handler=lambda _payload: pytest.fail("active work must not overlap"),
            worker_id="worker-b",
            heartbeat_interval_seconds=0,
        )

    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert record.status == "processing"
        assert record.attempts == 1
        assert record.locked_by == "worker-a"


def test_worker_inbox_detects_lost_ownership(session_scope):
    event = status_event("status:lease-lost")

    def steal_claim(_payload):
        with session_scope() as session:
            record = session.query(InboxEvent).one()
            record.locked_by = "worker-b"

    with pytest.raises(InboxLeaseLostError, match="lease lost"):
        process_inbox_work(
            session_scope,
            consumer_group="processing-workers",
            event=event,
            source={"topic": "tile-detections", "partition": 0, "offset": 1},
            handler=steal_claim,
            worker_id="worker-a",
            heartbeat_interval_seconds=0,
        )


def test_worker_inbox_handler_captures_message_location(session_scope):
    message = SimpleNamespace(
        topic=lambda: "image-tiles",
        partition=lambda: 4,
        offset=lambda: 23,
    )
    handler = make_inbox_work_handler(
        session_scope,
        consumer_group="ia-workers",
        message=message,
        handler=lambda _event: None,
        worker_id="worker-a",
    )

    handler(status_event("status:worker-wrapped"))

    with session_scope() as session:
        record = session.query(InboxEvent).one()
        assert (record.source_topic, record.source_partition, record.source_offset) == (
            "image-tiles",
            4,
            23,
        )


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


def test_direct_delivery_covers_success_retry_and_dead_letter():
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    successful = SimpleNamespace(
        topic="topic",
        payload={"event_id": "event:success"},
        message_key="mission-1",
        attempts=0,
        status="publishing",
        locked_at=now,
        locked_by="worker",
        published_at=None,
        last_error="old",
        dead_at=None,
        available_at=now,
    )

    assert deliver_outbox_event(
        successful,
        publisher=lambda *_args: None,
        retry_policy=RetryPolicy(2, 3, 10),
        now=now,
    ) is True
    assert successful.status == "published"
    assert successful.published_at == now

    failing = SimpleNamespace(**successful.__dict__)
    failing.attempts = 0
    failing.status = "publishing"
    failing.published_at = None

    def fail(*_args):
        raise OSError("offline")

    assert deliver_outbox_event(
        failing,
        publisher=fail,
        retry_policy=RetryPolicy(2, 3, 10),
        now=now,
    ) is False
    assert failing.status == "failed"
    assert failing.available_at == now + timedelta(seconds=3)
    assert deliver_outbox_event(
        failing,
        publisher=fail,
        retry_policy=RetryPolicy(2, 3, 10),
        now=now,
    ) is False
    assert failing.status == "dead"
    assert failing.dead_at == now


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


def test_dispatcher_does_not_finalize_a_stolen_claim(session_scope):
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with session_scope() as session:
        enqueue_outbox(
            session,
            topic="vols-bruts",
            event=make_event(
                "mission",
                {"vol_id": "mission-1"},
                event_id="mission:stolen-claim",
            ),
            now=now,
        )

    def steal_claim(*_args):
        with session_scope() as session:
            record = session.query(OutboxEvent).one()
            record.locked_by = "replacement-worker"

    result = dispatch_outbox_batch(
        session_scope,
        publisher=steal_claim,
        worker_id="original-worker",
        now=now,
    )

    assert result == {"selected": 1, "published": 0, "failed": 0, "dead": 0}


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


def test_dispatcher_loop_reports_batches_and_recovers_from_errors(session_scope):
    class OneIterationStop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _timeout):
            self.stopped = True

    logger = MagicMock()
    stop_event = OneIterationStop()
    with patch.object(
        inbox_outbox,
        "dispatch_outbox_batch",
        return_value={"selected": 1, "published": 1, "failed": 0, "dead": 0},
    ):
        run_outbox_dispatcher(
            session_scope,
            publisher=lambda *_args: None,
            stop_event=stop_event,
            worker_id="worker",
            logger=logger,
        )
    logger.info.assert_called_once()

    logger.reset_mock()
    stop_event = OneIterationStop()
    with patch.object(
        inbox_outbox,
        "dispatch_outbox_batch",
        side_effect=RuntimeError("database unavailable"),
    ):
        run_outbox_dispatcher(
            session_scope,
            publisher=lambda *_args: None,
            stop_event=stop_event,
            worker_id=default_worker_id(),
            logger=logger,
        )
    logger.exception.assert_called_once()
