from contextlib import contextmanager
from pathlib import Path
import sys

import pytest


APP2_ROOT = Path(__file__).resolve().parents[1] / "app2-ia"
if str(APP2_ROOT) not in sys.path:
    sys.path.insert(0, str(APP2_ROOT))

import detection_shard_stage  # noqa: E402
import stage_executor as detection_stage_executor  # noqa: E402
from shared.detection_shard_results import parse_detection_shard_result  # noqa: E402
from shared.detection_sharding import build_detection_shard_plan  # noqa: E402
from shared.model_provenance import build_model_manifest  # noqa: E402
from shared.stage_execution import (  # noqa: E402
    StageArtifactInput,
    StageExecutionContext,
    StageExecutionResult,
)
from shared.stage_workspace import RestoredWorkspace  # noqa: E402


class FakeControl:
    def __init__(self) -> None:
        self.checks = 0

    def raise_if_cancelled(self) -> None:
        self.checks += 1


def _model_manifest():
    return build_model_manifest(
        backend="sam3",
        repository="facebook/sam3",
        revision="3" * 40,
        artifact="model.safetensors",
        artifact_sha256="a" * 64,
        libraries={"transformers": "test"},
        runtime={"device": "cpu"},
        inference={"confidence": 0.3},
    )


def _plan():
    return build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=3)


def _context(plan):
    return StageExecutionContext(
        run_id="d" * 32,
        mission_id=1,
        organization_id="acme-survey",
        vol_id="quarry-001",
        workspace_prefix="organizations/acme-survey/missions/quarry-001",
        owner_subject="operator-a",
        stage="detection",
        attempt=0,
        mission_attempt=0,
        parameters={
            "ai": {
                "backend": "sam3",
                "tile_size": 256,
                "tile_overlap": 64,
            }
        },
        mission_parameters={},
        inputs=(
            StageArtifactInput(
                artifact_id="raster-1",
                kind="raster_product_workspace",
                uri="s3://drone-ai/raster/manifest.json",
                checksum_sha256="b" * 64,
                size_bytes=100,
                metadata={
                    "manifest_key": "raster/manifest.json",
                    "ortho_file": "orthomosaic.tif",
                },
            ),
        ),
        run_provenance={"detection_shard_plan": plan.descriptor()},
    )


def _result(plan, shard_index):
    shard = plan.shard(shard_index)
    return parse_detection_shard_result(
        {
            "schema_version": 1,
            "plan_checksum_sha256": plan.checksum_sha256,
            "shard_index": shard_index,
            "tile_count": shard.tile_count,
            "model_manifest": _model_manifest(),
            "detections": [],
        },
        plan,
    )


def test_indexed_subtask_runs_only_its_index_and_publishes_a_receipt(
    tmp_path,
    monkeypatch,
):
    plan = _plan()
    context = _context(plan)
    control = FakeControl()
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("DRONEAI_DETECTION_SHARD_INDEX", "1")
    monkeypatch.setenv("DRONEAI_DETECTION_SHARD_COUNT", "2")
    monkeypatch.setattr(
        detection_shard_stage,
        "artifact_selective_restore_enabled",
        lambda: True,
    )

    def restore(_context, _control, workspace, *, selective_restore):
        assert selective_restore is True
        raster = Path(workspace, "orthomosaic.tif")
        raster.write_bytes(b"raster")
        return raster, RestoredWorkspace(6, 1, 6, 0, 0.1, 1, 2)

    monkeypatch.setattr(detection_shard_stage, "_restore_raster_workspace", restore)
    observed = {}

    def run_shard(_runner, _raster, received_plan, shard_index):
        observed["plan"] = received_plan
        observed["index"] = shard_index
        return [], _model_manifest(), {}

    monkeypatch.setattr(
        detection_shard_stage.DetectionStageRunner,
        "run_shard",
        run_shard,
    )

    @contextmanager
    def session_scope(**context):
        assert context == {"organization_id": "acme-survey"}
        yield object()

    monkeypatch.setattr(detection_shard_stage, "get_session", session_scope)

    def publish(session, **kwargs):
        observed["session"] = session
        observed["result"] = kwargs["result"]

    monkeypatch.setattr(
        detection_shard_stage,
        "publish_detection_shard_result",
        publish,
    )

    detection_shard_stage.run_detection_shard_subtask(context, control)

    assert observed["plan"] == plan
    assert observed["index"] == 1
    assert observed["result"].shard_index == 1
    assert control.checks >= 1
    assert not any(tmp_path.iterdir())


def test_finalizer_requires_receipts_and_delegates_only_validated_fan_in(
    monkeypatch,
):
    plan = _plan()
    context = _context(plan)
    control = FakeControl()
    monkeypatch.setattr(
        detection_shard_stage,
        "artifact_selective_restore_enabled",
        lambda: True,
    )

    @contextmanager
    def session_scope(**context):
        assert context == {"organization_id": "acme-survey"}
        yield object()

    receipts = (object(), object())
    monkeypatch.setattr(detection_shard_stage, "get_session", session_scope)
    monkeypatch.setattr(
        detection_shard_stage,
        "complete_detection_shard_receipts",
        lambda *_args, **_kwargs: receipts,
    )
    monkeypatch.setattr(
        detection_shard_stage,
        "restore_detection_shard_results",
        lambda *_args, **_kwargs: [_result(plan, 0), _result(plan, 1)],
    )
    expected = StageExecutionResult(
        kind="detection_workspace",
        uri="s3://drone-ai/result/manifest.json",
        checksum_sha256="c" * 64,
    )
    observed = {}

    def finalize(_context, _control, *, aggregate, plan):
        observed["aggregate"] = aggregate
        observed["plan"] = plan
        return expected

    monkeypatch.setattr(detection_shard_stage, "run_detection_stage", finalize)

    result = detection_shard_stage.run_detection_finalizer(context, control)

    assert result is expected
    assert observed["plan"] == plan
    assert observed["aggregate"].shard_count == 2
    assert observed["aggregate"].tile_count == plan.tile_count


def test_indexed_subtask_rejects_job_count_drift(monkeypatch):
    plan = _plan()
    monkeypatch.setattr(
        detection_shard_stage,
        "artifact_selective_restore_enabled",
        lambda: True,
    )
    monkeypatch.setenv("DRONEAI_DETECTION_SHARD_COUNT", "3")

    with pytest.raises(ValueError, match="shard count"):
        detection_shard_stage.run_detection_shard_subtask(
            _context(plan),
            FakeControl(),
        )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("monolithic", "one-shot:run_detection_stage"),
        ("shard", "subtask:run_detection_shard_subtask"),
        ("finalizer", "one-shot:run_detection_finalizer"),
    ],
)
def test_stage_executor_routes_explicit_detection_modes(monkeypatch, mode, expected):
    calls = []

    def one_shot(_stage, handler):
        calls.append(f"one-shot:{handler.__name__}")

    def subtask(_stage, handler):
        calls.append(f"subtask:{handler.__name__}")

    monkeypatch.setattr(detection_stage_executor, "execute_one_shot_stage", one_shot)
    monkeypatch.setattr(detection_stage_executor, "execute_stage_subtask", subtask)
    monkeypatch.setenv("DRONEAI_DETECTION_EXECUTION_MODE", mode)

    assert detection_stage_executor.main() == 0
    assert calls == [expected]


def test_stage_executor_rejects_unknown_detection_mode(monkeypatch):
    monkeypatch.setenv("DRONEAI_DETECTION_EXECUTION_MODE", "surprise")

    with pytest.raises(ValueError, match="Unsupported detection execution mode"):
        detection_stage_executor.main()
