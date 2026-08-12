"""Indexed shard and finalizer executors for bounded raster detection."""

from __future__ import annotations

import os
import shutil

from detection_stage import (
    DetectionStageConfig,
    DetectionStageRunner,
    _restore_raster_workspace,
    _workspace_path,
    run_detection_stage,
)
from shared.database import get_session
from shared.detection_shard_publication import (
    publish_detection_shard_result,
    restore_detection_shard_results,
)
from shared.detection_shard_receipts import complete_detection_shard_receipts
from shared.detection_shard_results import (
    aggregate_detection_shards,
    parse_detection_shard_result,
)
from shared.detection_sharding import (
    DetectionShardPlan,
    parse_detection_shard_plan_descriptor,
)
from shared.stage_execution import (
    StageExecutionContext,
    StageExecutionControl,
    StageExecutionResult,
)
from shared.stage_workspace import artifact_selective_restore_enabled


def _durable_plan(context: StageExecutionContext) -> DetectionShardPlan:
    return parse_detection_shard_plan_descriptor(
        context.run_provenance.get("detection_shard_plan")
    )


def _bounded_environment_index(name: str, upper_bound: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw or not raw.isdigit():
        raise ValueError(f"{name} must be a non-negative integer")
    value = int(raw)
    if not 0 <= value < upper_bound:
        raise ValueError(f"{name} is outside its shard plan")
    return value


def run_detection_shard_subtask(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> None:
    if not artifact_selective_restore_enabled():
        raise ValueError("Indexed detection requires selective Manifest v2 restore")
    plan = _durable_plan(context)
    declared_count = int(os.getenv("DRONEAI_DETECTION_SHARD_COUNT", "0"))
    if declared_count != plan.shard_count:
        raise ValueError("Indexed Job shard count does not match the durable plan")
    shard_index = _bounded_environment_index(
        "DRONEAI_DETECTION_SHARD_INDEX",
        plan.shard_count,
    )
    workspace = _workspace_path(f"{context.run_id}-shard-{shard_index:04d}")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    try:
        raster_path, _restored = _restore_raster_workspace(
            context,
            control,
            workspace,
            selective_restore=True,
        )
        config = DetectionStageConfig.from_context(context)
        raw, model_manifest, _raster_metadata = DetectionStageRunner(
            context,
            control,
            workspace,
            config,
        ).run_shard(raster_path, plan, shard_index)
        shard = plan.shard(shard_index)
        result = parse_detection_shard_result(
            {
                "schema_version": 1,
                "plan_checksum_sha256": plan.checksum_sha256,
                "shard_index": shard_index,
                "tile_count": shard.tile_count,
                "model_manifest": model_manifest,
                "detections": raw,
            },
            plan,
        )
        control.raise_if_cancelled()
        with get_session(organization_id=context.organization_id) as session:
            publish_detection_shard_result(
                session,
                run_id=context.run_id,
                plan=plan,
                result=result,
                organization_id=context.organization_id,
                cancellation_check=control.raise_if_cancelled,
            )
    finally:
        if workspace.exists():
            shutil.rmtree(workspace)


def run_detection_finalizer(
    context: StageExecutionContext,
    control: StageExecutionControl,
) -> StageExecutionResult:
    if not artifact_selective_restore_enabled():
        raise ValueError("Indexed detection finalizer requires Manifest v2 restore")
    plan = _durable_plan(context)
    with get_session(organization_id=context.organization_id) as session:
        receipts = complete_detection_shard_receipts(
            session,
            run_id=context.run_id,
            plan=plan,
        )
    results = restore_detection_shard_results(
        receipts,
        plan,
        cancellation_check=control.raise_if_cancelled,
    )
    config = DetectionStageConfig.from_context(context)
    aggregate = aggregate_detection_shards(
        plan,
        results,
        maximum_raw_detections=config.maximum_raw_detections,
    )
    return run_detection_stage(
        context,
        control,
        aggregate=aggregate,
        plan=plan,
    )
