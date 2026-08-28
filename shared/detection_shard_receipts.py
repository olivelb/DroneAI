"""Transactional durable receipts for indexed detection shard outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast

from sqlalchemy.orm import Session

from shared.artifact_manifest import content_addressed_blob_key
from shared.database import DetectionShardReceipt, Mission, MissionStageRun
from shared.detection_sharding import DetectionShardPlan
from shared.tenancy import validate_organization_id


@dataclass(frozen=True)
class RecordedShardReceipt:
    receipt: DetectionShardReceipt
    reused: bool


def _lower_sha256(value: str, field: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lower-case SHA-256")
    return value


def _result_key(
    value: str,
    checksum_sha256: str,
    organization_id: str,
) -> str:
    if not value or "\\" in value:
        raise ValueError("Detection shard result key must be a canonical S3 key")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("Detection shard result key must be a canonical S3 key")
    expected_key = content_addressed_blob_key(
        checksum_sha256,
        organization_id=organization_id,
    )
    if value != expected_key:
        raise ValueError(
            "Detection shard result key must match its content checksum"
        )
    return value


def _durable_plan(run: MissionStageRun) -> dict[str, Any]:
    provenance = cast(dict[str, Any], run.provenance or {})
    value = provenance.get("detection_shard_plan")
    if not isinstance(value, dict):
        raise ValueError("Detection stage run has no durable shard plan")
    return cast(dict[str, Any], value)


def detection_run_organization_id(session: Session, run_id: str) -> str:
    """Resolve the durable mission tenant for a detection stage run."""

    value = (
        session.query(Mission.organization_id)
        .join(MissionStageRun, MissionStageRun.mission_id == Mission.id)
        .filter(MissionStageRun.run_id == run_id)
        .scalar()
    )
    if value is None:
        raise ValueError("Detection stage run has no durable mission organization")
    return validate_organization_id(str(value))


def _same_receipt(
    receipt: DetectionShardReceipt,
    *,
    shard_count: int,
    tile_count: int,
    result_key: str,
    result_checksum_sha256: str,
    result_size_bytes: int,
) -> bool:
    return bool(
        receipt.shard_count == shard_count
        and receipt.tile_count == tile_count
        and receipt.result_key == result_key
        and receipt.result_checksum_sha256 == result_checksum_sha256
        and receipt.result_size_bytes == result_size_bytes
    )


def record_detection_shard_receipt(
    session: Session,
    *,
    run_id: str,
    plan: DetectionShardPlan,
    shard_index: int,
    result_key: str,
    result_checksum_sha256: str,
    result_size_bytes: int,
    organization_id: str | None = None,
) -> RecordedShardReceipt:
    """Insert one receipt or reuse an identical retry under the run row lock."""

    if plan.shard_count < 2:
        raise ValueError("Detection shard receipts require a multi-shard plan")
    shard = plan.shard(shard_index)
    checksum = _lower_sha256(result_checksum_sha256, "Shard result checksum")
    if result_size_bytes <= 0:
        raise ValueError("Shard result size must be positive")
    run = (
        session.query(MissionStageRun)
        .filter(MissionStageRun.run_id == run_id)
        .with_for_update()
        .one()
    )
    if run.stage != "detection" or run.executor != "kubernetes-job":
        raise ValueError("Shard receipts require a Kubernetes detection stage run")
    if run.status != "running":
        raise ValueError(f"Shard receipt cannot publish from status {run.status}")
    if _durable_plan(run) != plan.descriptor():
        raise ValueError("Shard receipt plan does not match the durable stage plan")
    durable_organization = detection_run_organization_id(session, run_id)
    if (
        organization_id is not None
        and validate_organization_id(organization_id) != durable_organization
    ):
        raise ValueError("Detection shard organization does not match mission")
    canonical_key = _result_key(result_key, checksum, durable_organization)
    existing = (
        session.query(DetectionShardReceipt)
        .filter(
            DetectionShardReceipt.stage_run_id == run.id,
            DetectionShardReceipt.plan_checksum_sha256 == plan.checksum_sha256,
            DetectionShardReceipt.shard_index == shard_index,
        )
        .one_or_none()
    )
    if existing is not None:
        if not _same_receipt(
            existing,
            shard_count=plan.shard_count,
            tile_count=shard.tile_count,
            result_key=canonical_key,
            result_checksum_sha256=checksum,
            result_size_bytes=result_size_bytes,
        ):
            raise ValueError(
                f"Detection shard {shard_index} already has a different receipt"
            )
        return RecordedShardReceipt(existing, reused=True)
    receipt = DetectionShardReceipt(
        stage_run_id=run.id,
        plan_checksum_sha256=plan.checksum_sha256,
        shard_index=shard_index,
        shard_count=plan.shard_count,
        tile_count=shard.tile_count,
        result_key=canonical_key,
        result_checksum_sha256=checksum,
        result_size_bytes=result_size_bytes,
    )
    session.add(receipt)
    session.flush()
    return RecordedShardReceipt(receipt, reused=False)


def complete_detection_shard_receipts(
    session: Session,
    *,
    run_id: str,
    plan: DetectionShardPlan,
) -> tuple[DetectionShardReceipt, ...]:
    """Return one ordered receipt per shard or fail closed on any gap."""

    run = session.query(MissionStageRun).filter(MissionStageRun.run_id == run_id).one()
    if run.stage != "detection" or run.executor != "kubernetes-job":
        raise ValueError("Shard finalization requires a Kubernetes detection run")
    if run.status != "running":
        raise ValueError(f"Shard finalization cannot run from status {run.status}")
    if _durable_plan(run) != plan.descriptor():
        raise ValueError("Finalizer plan does not match the durable stage plan")
    durable_organization = detection_run_organization_id(session, run_id)
    receipts = cast(
        list[DetectionShardReceipt],
        session.query(DetectionShardReceipt)
        .filter(
            DetectionShardReceipt.stage_run_id == run.id,
            DetectionShardReceipt.plan_checksum_sha256 == plan.checksum_sha256,
        )
        .order_by(DetectionShardReceipt.shard_index)
        .all(),
    )
    indices = [cast(int, receipt.shard_index) for receipt in receipts]
    expected = list(range(plan.shard_count))
    if indices != expected:
        missing = sorted(set(expected) - set(indices))
        unexpected = sorted(set(indices) - set(expected))
        raise ValueError(
            f"Incomplete durable detection shard receipts: missing={missing}, "
            f"unexpected={unexpected}"
        )
    for receipt, shard in zip(receipts, plan.shards, strict=True):
        if (
            receipt.shard_count != plan.shard_count
            or receipt.tile_count != shard.tile_count
        ):
            raise ValueError("Durable detection shard receipt contradicts its plan")
        checksum = _lower_sha256(
            cast(str, receipt.result_checksum_sha256),
            "Durable shard result checksum",
        )
        _result_key(
            cast(str, receipt.result_key),
            checksum,
            durable_organization,
        )
        if cast(int, receipt.result_size_bytes) <= 0:
            raise ValueError("Durable shard result size must be positive")
    return tuple(receipts)
