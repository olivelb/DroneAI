from pathlib import Path
from types import SimpleNamespace

import pytest

from colmap_worker import stage_executor
from shared.stage_execution import StageExecutionContext
from shared.stage_workspace import PublishedWorkspace


class FakeControl:
    def __init__(self):
        self.checks = 0

    def raise_if_cancelled(self):
        self.checks += 1


class FakeCancellationState:
    def __init__(self):
        self.started = []
        self.cleared = 0

    def start_mission(self, vol_id, attempt):
        self.started.append((vol_id, attempt))

    def clear(self):
        self.cleared += 1


def _context() -> StageExecutionContext:
    return StageExecutionContext(
        run_id="a" * 32,
        mission_id=1,
        vol_id="quarry-001",
        owner_subject="operator-a",
        stage="reconstruction",
        attempt=0,
        mission_attempt=3,
        parameters={},
        mission_parameters={
            "input_dataset": "datasets/quarry-001",
            "pipeline": "modern",
        },
        inputs=(),
    )


def test_reconstruction_adapter_runs_aligned_pipeline_and_publishes_workspace(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))
    cancellation = FakeCancellationState()
    monkeypatch.setattr(stage_executor.runtime, "cancellation_state", cancellation)
    monkeypatch.setattr(stage_executor.runtime, "ensure_not_cancelled", lambda: None)
    calls = []

    def prepare(workspace, input_dataset, vol_id, mission_parameters):
        calls.append("prepare")
        assert input_dataset == "datasets/quarry-001"
        assert mission_parameters["pipeline"] == "modern"
        Path(workspace, "dense", "sparse").mkdir(parents=True)
        Path(workspace, "dense", "sparse", "cameras.bin").write_bytes(b"model")
        return SimpleNamespace(feature_type="ALIKED_N32", matcher_type="LIGHTGLUE")

    def reconstruct(preparation, workspace, vol_id):
        calls.append("reconstruct")
        return SimpleNamespace(utm_crs="EPSG:2154")

    def refine(preparation, reconstruction, workspace, vol_id):
        calls.append("rtk")
        return SimpleNamespace(
            active_sparse_model_path=str(Path(workspace, "dense", "sparse"))
        )

    def align(preparation, reconstruction, rtk, workspace, vol_id):
        calls.append("align")
        path = Path(workspace, "alignment_transform.json")
        path.write_text("{}", encoding="utf-8")
        return SimpleNamespace(alignment_transform_path=str(path))

    published_prefix = {}

    def publish(workspace, prefix, cancellation_check):
        calls.append("publish")
        cancellation_check()
        published_prefix["value"] = prefix
        assert Path(workspace, "dense", "sparse", "cameras.bin").is_file()
        return PublishedWorkspace(
            manifest_key=f"{prefix}/manifest.json",
            uri=f"s3://drone-ai/{prefix}/manifest.json",
            checksum_sha256="c" * 64,
            size_bytes=123,
            file_count=2,
        )

    monkeypatch.setattr(stage_executor, "prepare_colmap_pipeline_run", prepare)
    monkeypatch.setattr(stage_executor, "reconstruct_colmap_sparse", reconstruct)
    monkeypatch.setattr(stage_executor, "refine_colmap_rtk", refine)
    monkeypatch.setattr(stage_executor, "undistort_and_align_colmap", align)
    monkeypatch.setattr(stage_executor, "publish_workspace", publish)
    monkeypatch.setattr(
        stage_executor,
        "write_reconstruction_state",
        lambda *_args: Path(".droneai/reconstruction-state.json"),
    )
    monkeypatch.setattr(
        stage_executor,
        "inspect_sparse_quality",
        lambda _path: {"registered_images": 80},
    )
    control = FakeControl()

    result = stage_executor.run_reconstruction_stage(_context(), control)

    assert calls == ["prepare", "reconstruct", "rtk", "align", "publish"]
    assert published_prefix["value"].endswith(
        f"/{'a' * 32}/reconstruction-workspace"
    )
    assert result.kind == "reconstruction_workspace"
    assert result.quality_metrics == {"registered_images": 80}
    assert result.metadata["active_sparse_model"] == "dense/sparse"
    assert result.metadata["alignment_transform"] == "alignment_transform.json"
    assert result.metadata["state_file"] == ".droneai/reconstruction-state.json"
    assert cancellation.started == [("quarry-001", 3)]
    assert cancellation.cleared == 1
    assert control.checks >= 3
    assert not (tmp_path / "work" / ("a" * 32)).exists()


def test_reconstruction_adapter_cleans_workspace_after_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))
    cancellation = FakeCancellationState()
    monkeypatch.setattr(stage_executor.runtime, "cancellation_state", cancellation)
    monkeypatch.setattr(stage_executor.runtime, "ensure_not_cancelled", lambda: None)

    def fail_prepare(workspace, *_args):
        Path(workspace, "partial.bin").write_bytes(b"partial")
        raise RuntimeError("preparation failed")

    monkeypatch.setattr(stage_executor, "prepare_colmap_pipeline_run", fail_prepare)

    with pytest.raises(RuntimeError, match="preparation failed"):
        stage_executor.run_reconstruction_stage(_context(), FakeControl())

    assert cancellation.cleared == 1
    assert not (tmp_path / "work" / ("a" * 32)).exists()
