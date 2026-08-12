"""Real PostgreSQL fault and concurrency qualification.

These tests deliberately exercise process-loss and replica races against the
database transaction boundaries used in production. They use synthetic event
and upload records; no scientific dataset is involved.
"""

from __future__ import annotations

import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.database import (
    Dataset,
    DatasetUploadFile,
    DatasetUploadSession,
    InboxEvent,
    Organization,
    OutboxEvent,
    get_session,
)
from shared.event_contracts import make_event
from shared.inbox_outbox import (
    InboxResult,
    dispatch_outbox_batch,
    enqueue_outbox,
    process_inbox_transaction,
)
from shared.tenancy import dataset_prefix

uploads = importlib.import_module("app4-dashboard.api.dataset_uploads")
security = importlib.import_module("app4-dashboard.api.security")


def _organization(prefix: str) -> str:
    organization_id = f"{prefix}-{uuid4().hex[:12]}"
    with get_session() as session:
        session.add(
            Organization(
                id=organization_id,
                display_name=f"{prefix} fault qualification",
                status="active",
                created_by="integration",
                updated_by="integration",
            )
        )
    return organization_id


def _status_event(organization_id: str, suffix: str) -> dict[str, object]:
    vol_id = f"fault-{suffix}"
    return make_event(
        "status",
        {
            "organization_id": organization_id,
            "vol_id": vol_id,
            "service": "integration",
            "step": "FAULT_QUALIFICATION",
            "progress": 50,
            "status": "processing",
        },
        event_id=f"status:{organization_id}:{suffix}",
    )


@pytest.mark.integration
def test_concurrent_kafka_delivery_runs_domain_handler_exactly_once() -> None:
    organization_id = _organization("duplicate-event")
    scope = partial(get_session, organization_id=organization_id)
    event = _status_event(organization_id, uuid4().hex[:8])
    start = threading.Barrier(2)
    handled: list[str] = []
    handled_lock = threading.Lock()

    def consume(replica: int) -> InboxResult:
        start.wait(timeout=5)

        def handler(_session: object, payload: dict[str, object]) -> None:
            with handled_lock:
                handled.append(str(payload["event_id"]))
            # Keep the first transaction open so the second replica exercises
            # the PostgreSQL unique-index wait, not a sequential fast path.
            time.sleep(0.1)

        return process_inbox_transaction(
            scope,
            consumer_group="fault-qualification",
            event=event,
            source={
                "topic": "pipeline-status",
                "partition": replica,
                "offset": 1,
            },
            handler=handler,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, (0, 1)))

    assert sorted(results) == [InboxResult.DUPLICATE, InboxResult.PROCESSED]
    assert handled == [event["event_id"]]
    with scope() as session:
        receipt = session.query(InboxEvent).filter_by(
            consumer_group="fault-qualification",
            event_id=event["event_id"],
        ).one()
        assert receipt.status == "completed"


@pytest.mark.integration
def test_outbox_process_loss_is_reclaimed_only_after_lease_expiry() -> None:
    organization_id = _organization("outbox-crash")
    scope = partial(get_session, organization_id=organization_id)
    event = _status_event(organization_id, uuid4().hex[:8])
    now = datetime.now(UTC)
    with scope() as session:
        enqueue_outbox(
            session,
            topic="pipeline-status",
            key=f"{organization_id}:fault",
            event=event,
            now=now,
        )

    def terminate_after_claim(*_args: object) -> None:
        # BaseException intentionally bypasses ordinary publication recovery,
        # matching a process termination between claim commit and delivery.
        raise SystemExit("simulated process termination")

    with pytest.raises(SystemExit, match="simulated process termination"):
        dispatch_outbox_batch(
            scope,
            publisher=terminate_after_claim,
            worker_id="terminated-worker",
            now=now,
            lease_seconds=30,
        )

    with scope() as session:
        claimed = session.query(OutboxEvent).filter_by(
            event_id=event["event_id"]
        ).one()
        assert claimed.status == "publishing"
        assert claimed.locked_by == "terminated-worker"

    early = dispatch_outbox_batch(
        scope,
        publisher=lambda *_args: pytest.fail("active lease was stolen"),
        worker_id="replacement-worker",
        now=now + timedelta(seconds=29),
        lease_seconds=30,
    )
    published: list[str] = []
    recovered = dispatch_outbox_batch(
        scope,
        publisher=lambda _topic, payload, _key: published.append(
            str(payload["event_id"])
        ),
        worker_id="replacement-worker",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert early["selected"] == 0
    assert recovered["published"] == 1
    assert published == [event["event_id"]]


def _seed_finalizing_upload(organization_id: str) -> tuple[str, str, bytes]:
    session_id = str(uuid4())
    dataset_name = f"fault-upload-{uuid4().hex[:10]}"
    prefix = dataset_prefix(organization_id, dataset_name)
    payload = b"synthetic-upload"
    with get_session(organization_id=organization_id) as session:
        record = DatasetUploadSession(
            session_id=session_id,
            dataset_name=dataset_name,
            organization_id=organization_id,
            status="finalizing",
            total_bytes=len(payload),
            file_count=1,
            part_size=5 * 1024 * 1024,
            created_by="integration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            completed_at=datetime.now(UTC),
        )
        record.files.append(
            DatasetUploadFile(
                filename="synthetic.jpg",
                s3_key=f"{prefix}/synthetic.jpg",
                size_bytes=len(payload),
                content_type="image/jpeg",
                multipart_upload_id="completed-upload",
                status="completed",
                completed_parts=[{"part_number": 1, "etag": '"part-1"'}],
                etag='"object-etag"',
            )
        )
        session.add(record)
    return session_id, dataset_name, payload


@pytest.mark.integration
def test_concurrent_upload_finalization_publishes_one_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = _organization("upload-race")
    session_id, dataset_name, _payload = _seed_finalizing_upload(organization_id)
    principal = security.Principal("integration", "admin", organization_id)
    start = threading.Barrier(2)
    put_started = threading.Event()
    release_put = threading.Event()
    manifests: dict[str, bytes] = {}
    manifest_lock = threading.Lock()

    def put_object(key: str, data: bytes) -> str:
        with manifest_lock:
            manifests[key] = bytes(data)
        put_started.set()
        assert release_put.wait(5)
        return '"manifest-etag"'

    monkeypatch.setattr(uploads.storage, "put_object", put_object)
    monkeypatch.setattr(
        uploads.storage,
        "get_object_size",
        lambda key: len(manifests[key]),
    )

    def finalize() -> dict[str, object]:
        start.wait(timeout=5)
        with get_session(organization_id=organization_id) as session:
            return uploads.finalize_upload_session(
                session,
                session_id,
                principal,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(finalize) for _ in range(2)]
        assert put_started.wait(5)
        time.sleep(0.1)
        assert all(not future.done() for future in futures)
        release_put.set()
        results = [future.result(timeout=5) for future in futures]

    assert [result["status"] for result in results] == ["done", "done"]
    assert len(manifests) == 1
    with get_session(organization_id=organization_id) as session:
        upload = session.query(DatasetUploadSession).filter_by(
            session_id=session_id
        ).one()
        assert upload.status == "completed"
        assert session.query(Dataset).filter_by(
            organization_id=organization_id,
            name=dataset_name,
        ).count() == 1


@pytest.mark.integration
def test_s3_timeout_leaves_recoverable_upload_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = _organization("upload-timeout")
    session_id, dataset_name, _payload = _seed_finalizing_upload(organization_id)
    principal = security.Principal("integration", "admin", organization_id)
    manifests: dict[str, bytes] = {}
    attempts = 0

    def put_object(key: str, data: bytes) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic S3 timeout")
        manifests[key] = bytes(data)
        return '"manifest-etag"'

    monkeypatch.setattr(uploads.storage, "put_object", put_object)
    monkeypatch.setattr(
        uploads.storage,
        "get_object_size",
        lambda key: len(manifests[key]),
    )

    with pytest.raises(HTTPException) as failure:
        with get_session(organization_id=organization_id) as session:
            uploads.finalize_upload_session(session, session_id, principal)
    assert failure.value.status_code == 502
    with get_session(organization_id=organization_id) as session:
        upload = session.query(DatasetUploadSession).filter_by(
            session_id=session_id
        ).one()
        assert upload.status == "finalizing"
        assert "synthetic S3 timeout" in str(upload.last_error)
        assert session.query(Dataset).filter_by(
            organization_id=organization_id,
            name=dataset_name,
        ).count() == 0

    with get_session(organization_id=organization_id) as session:
        recovered = uploads.finalize_upload_session(session, session_id, principal)

    assert recovered["status"] == "done"
    assert attempts == 2
    with get_session(organization_id=organization_id) as session:
        assert session.query(Dataset).filter_by(
            organization_id=organization_id,
            name=dataset_name,
        ).count() == 1
