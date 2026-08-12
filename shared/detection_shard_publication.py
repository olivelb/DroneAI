"""CAS publication and verified restoration for detection shard results."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from shared import storage
from shared.checksums import sha256_file
from shared.database import DetectionShardReceipt
from shared.detection_shard_receipts import (
    RecordedShardReceipt,
    detection_run_organization_id,
    record_detection_shard_receipt,
)
from shared.detection_shard_results import (
    DetectionShardResult,
    canonical_detection_shard_result,
    parse_detection_shard_result,
)
from shared.detection_sharding import DetectionShardPlan
from shared.tenancy import LEGACY_ORGANIZATION_ID, validate_organization_id


@dataclass(frozen=True)
class PublishedDetectionShard:
    receipt: DetectionShardReceipt
    receipt_reused: bool
    object_reused: bool
    transferred_bytes: int


def publish_detection_shard_result(
    session: Session,
    *,
    run_id: str,
    plan: DetectionShardPlan,
    result: DetectionShardResult,
    organization_id: str | None = None,
    cancellation_check: Callable[[], None] | None = None,
) -> PublishedDetectionShard:
    """Publish canonical result bytes to CAS, then commit their durable receipt."""

    validated = parse_detection_shard_result(result.payload(), plan)
    content = canonical_detection_shard_result(validated)
    durable_organization = detection_run_organization_id(session, run_id)
    if (
        organization_id is not None
        and validate_organization_id(organization_id) != durable_organization
    ):
        raise ValueError("Detection shard organization does not match mission")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="droneai-detection-shard-",
        suffix=".json",
        delete=False,
    ) as descriptor:
        path = Path(descriptor.name)
        descriptor.write(content)
    try:
        if durable_organization == LEGACY_ORGANIZATION_ID:
            uploaded = storage.publish_content_addressed_file(
                path,
                cancellation_check=cancellation_check,
            )
        else:
            uploaded = storage.publish_content_addressed_file(
                path,
                organization_id=durable_organization,
                cancellation_check=cancellation_check,
            )
    finally:
        path.unlink(missing_ok=True)
    recorded: RecordedShardReceipt = record_detection_shard_receipt(
        session,
        run_id=run_id,
        plan=plan,
        shard_index=validated.shard_index,
        result_key=uploaded.key,
        result_checksum_sha256=uploaded.checksum_sha256,
        result_size_bytes=uploaded.size_bytes,
        organization_id=durable_organization,
    )
    return PublishedDetectionShard(
        receipt=recorded.receipt,
        receipt_reused=recorded.reused,
        object_reused=uploaded.reused,
        transferred_bytes=uploaded.transferred_bytes,
    )


def restore_detection_shard_results(
    receipts: tuple[DetectionShardReceipt, ...],
    plan: DetectionShardPlan,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> list[DetectionShardResult]:
    """Download, checksum and parse a complete ordered set of shard results."""

    if len(receipts) != plan.shard_count:
        raise ValueError("Detection shard restoration requires every plan receipt")
    results: list[DetectionShardResult] = []
    for expected_index, receipt in enumerate(receipts):
        if cancellation_check is not None:
            cancellation_check()
        if receipt.shard_index != expected_index:
            raise ValueError("Detection shard receipts are not in plan order")
        with tempfile.NamedTemporaryFile(
            prefix="droneai-detection-shard-restore-",
            suffix=".json",
            delete=False,
        ) as descriptor:
            path = Path(descriptor.name)
        try:
            storage.download_file(cast(str, receipt.result_key), path)
            actual_size = path.stat().st_size
            actual_checksum = sha256_file(path)
            if (
                actual_size != receipt.result_size_bytes
                or actual_checksum != receipt.result_checksum_sha256
            ):
                raise OSError(
                    f"Detection shard {expected_index} result verification failed"
                )
            try:
                payload = cast(Any, json.loads(path.read_bytes()))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(
                    f"Detection shard {expected_index} result is not valid JSON"
                ) from error
            result = parse_detection_shard_result(payload, plan)
            if result.shard_index != expected_index:
                raise ValueError("Detection shard result index contradicts its receipt")
            results.append(result)
        finally:
            path.unlink(missing_ok=True)
    return results
