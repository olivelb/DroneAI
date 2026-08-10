from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import DetectionShardReceipt, Mission, MissionStageRun
from shared.detection_shard_receipts import (
    complete_detection_shard_receipts,
    record_detection_shard_receipt,
)
from shared.detection_sharding import build_detection_shard_plan


@pytest.fixture
def receipt_sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    DetectionShardReceipt.__table__.create(engine)
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


def _plan():
    return build_detection_shard_plan(
        600,
        500,
        256,
        64,
        tiles_per_shard=3,
    )


def _create_run(scope, plan, *, status="running", stage="detection"):
    with scope() as session:
        mission = Mission(
            vol_id="sharded-mission",
            owner_subject="operator-a",
            status="processing",
            params={},
        )
        session.add(mission)
        session.flush()
        run = MissionStageRun(
            run_id="d" * 32,
            mission_id=mission.id,
            stage=stage,
            attempt=0,
            status=status,
            executor="kubernetes-job",
            resource_class="gpu-standard",
            idempotency_key="e" * 64,
            provenance={"detection_shard_plan": plan.descriptor()},
        )
        session.add(run)


def _record(session, plan, shard_index, *, checksum="a" * 64):
    return record_detection_shard_receipt(
        session,
        run_id="d" * 32,
        plan=plan,
        shard_index=shard_index,
        result_key=(
            f"missions/sharded-mission/stage-runs/{'d' * 32}/"
            f"detection-shards/{plan.checksum_sha256}/{shard_index:04d}.json"
        ),
        result_checksum_sha256=checksum,
        result_size_bytes=123,
    )


def test_identical_shard_retry_reuses_immutable_receipt(receipt_sessions):
    plan = _plan()
    _create_run(receipt_sessions, plan)

    with receipt_sessions() as session:
        first = _record(session, plan, 0)
        repeated = _record(session, plan, 0)

        assert first.reused is False
        assert repeated.reused is True
        assert repeated.receipt.id == first.receipt.id
        assert session.query(DetectionShardReceipt).count() == 1


def test_shard_retry_cannot_replace_a_different_result(receipt_sessions):
    plan = _plan()
    _create_run(receipt_sessions, plan)

    with receipt_sessions() as session:
        _record(session, plan, 0)
        with pytest.raises(ValueError, match="different receipt"):
            _record(session, plan, 0, checksum="b" * 64)


def test_finalizer_requires_every_ordered_durable_receipt(receipt_sessions):
    plan = _plan()
    _create_run(receipt_sessions, plan)

    with receipt_sessions() as session:
        _record(session, plan, 1)
        with pytest.raises(ValueError, match=r"missing=\[0\]"):
            complete_detection_shard_receipts(
                session,
                run_id="d" * 32,
                plan=plan,
            )
        _record(session, plan, 0)
        receipts = complete_detection_shard_receipts(
            session,
            run_id="d" * 32,
            plan=plan,
        )

        assert [receipt.shard_index for receipt in receipts] == [0, 1]


def test_receipt_rejects_wrong_plan_status_and_result_key(receipt_sessions):
    plan = _plan()
    _create_run(receipt_sessions, plan, status="queued")

    with receipt_sessions() as session:
        with pytest.raises(ValueError, match="status queued"):
            _record(session, plan, 0)

    with receipt_sessions() as session:
        run = session.query(MissionStageRun).one()
        run.status = "running"
        run.provenance = {
            "detection_shard_plan": {
                **plan.descriptor(),
                "tiles_per_shard": 999,
            }
        }
    with receipt_sessions() as session:
        with pytest.raises(ValueError, match="durable stage plan"):
            _record(session, plan, 0)

    with receipt_sessions() as session:
        run = session.query(MissionStageRun).one()
        run.provenance = {"detection_shard_plan": plan.descriptor()}
        with pytest.raises(ValueError, match="canonical S3 JSON key"):
            record_detection_shard_receipt(
                session,
                run_id="d" * 32,
                plan=plan,
                shard_index=0,
                result_key="../escape.json",
                result_checksum_sha256="a" * 64,
                result_size_bytes=123,
            )
