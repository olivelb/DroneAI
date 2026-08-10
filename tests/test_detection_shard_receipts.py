from contextlib import contextmanager
import hashlib
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import DetectionShardReceipt, Mission, MissionStageRun
from shared import detection_shard_publication
from shared.detection_shard_publication import (
    publish_detection_shard_result,
    restore_detection_shard_results,
)
from shared.detection_shard_receipts import (
    complete_detection_shard_receipts,
    record_detection_shard_receipt,
)
from shared.detection_sharding import build_detection_shard_plan
from shared.detection_shard_results import parse_detection_shard_result
from shared.model_provenance import build_model_manifest


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


@pytest.fixture
def shard_cas(tmp_path, monkeypatch):
    root = tmp_path / "shard-cas"

    def publish(local_path, *, cancellation_check=None):
        if cancellation_check is not None:
            cancellation_check()
        content = Path(local_path).read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        key = f"blobs/sha256/{checksum[:2]}/{checksum}"
        target = root / key
        reused = target.exists()
        if not reused:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return detection_shard_publication.storage.ContentAddressedUpload(
            key=key,
            size_bytes=len(content),
            checksum_sha256=checksum,
            reused=reused,
            transferred_bytes=0 if reused else len(content),
        )

    def download(key, local_path):
        shutil.copy2(root / key, local_path)
        return Path(local_path)

    monkeypatch.setattr(
        detection_shard_publication.storage,
        "publish_content_addressed_file",
        publish,
    )
    monkeypatch.setattr(
        detection_shard_publication.storage,
        "download_file",
        download,
    )
    return root


def _shard_result(plan, shard_index):
    shard = plan.shard(shard_index)
    return parse_detection_shard_result(
        {
            "schema_version": 1,
            "plan_checksum_sha256": plan.checksum_sha256,
            "shard_index": shard_index,
            "tile_count": shard.tile_count,
            "model_manifest": build_model_manifest(
                backend="sam3",
                repository="facebook/sam3",
                revision="3" * 40,
                artifact="model.safetensors",
                artifact_sha256="a" * 64,
                libraries={"transformers": "test"},
                runtime={"device": "cpu"},
                inference={"confidence": 0.3},
            ),
            "detections": [],
        },
        plan,
    )


def _record(session, plan, shard_index, *, checksum="a" * 64):
    return record_detection_shard_receipt(
        session,
        run_id="d" * 32,
        plan=plan,
        shard_index=shard_index,
        result_key=f"blobs/sha256/{checksum[:2]}/{checksum}",
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
        with pytest.raises(ValueError, match="canonical S3 key"):
            record_detection_shard_receipt(
                session,
                run_id="d" * 32,
                plan=plan,
                shard_index=0,
                result_key="../escape.json",
                result_checksum_sha256="a" * 64,
                result_size_bytes=123,
            )


def test_cas_publication_and_restore_are_idempotent_and_verified(
    receipt_sessions,
    shard_cas,
):
    plan = _plan()
    _create_run(receipt_sessions, plan)

    with receipt_sessions() as session:
        first = publish_detection_shard_result(
            session,
            run_id="d" * 32,
            plan=plan,
            result=_shard_result(plan, 0),
        )
        second = publish_detection_shard_result(
            session,
            run_id="d" * 32,
            plan=plan,
            result=_shard_result(plan, 1),
        )
        repeated = publish_detection_shard_result(
            session,
            run_id="d" * 32,
            plan=plan,
            result=_shard_result(plan, 0),
        )
        receipts = complete_detection_shard_receipts(
            session,
            run_id="d" * 32,
            plan=plan,
        )
        restored = restore_detection_shard_results(receipts, plan)

        assert first.object_reused is False
        assert second.object_reused is False
        assert repeated.object_reused is True
        assert repeated.receipt_reused is True
        assert repeated.transferred_bytes == 0
        assert [result.shard_index for result in restored] == [0, 1]

    corrupt_path = shard_cas / first.receipt.result_key
    corrupt_path.write_bytes(b"corrupt")
    with pytest.raises(OSError, match="verification failed"):
        restore_detection_shard_results(receipts, plan)
