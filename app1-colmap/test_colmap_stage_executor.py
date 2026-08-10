import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from colmap_worker import stage_executor
from colmap_worker.stages import gaussian as gaussian_stage
from gaussian_ortho import phase_artifacts
from gaussian_ortho import raster_product
from shared.stage_execution import StageArtifactInput, StageExecutionContext
from shared.stage_workspace import PublishedWorkspace, RestoredWorkspace


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


gaussian_workflow = importlib.import_module(
    "gaussian_ortho.generate_gaussian_orthophoto"
)


def _context(
    stage="reconstruction",
    *,
    input_kind=None,
) -> StageExecutionContext:
    inputs = (
        (
            StageArtifactInput(
                artifact_id="artifact-1",
                kind=input_kind,
                uri="s3://drone-ai/upstream/manifest.json",
                checksum_sha256="b" * 64,
                size_bytes=123,
                metadata={"manifest_key": "upstream/manifest.json"},
            ),
        )
        if input_kind is not None
        else ()
    )
    return StageExecutionContext(
        run_id="a" * 32,
        mission_id=1,
        vol_id="quarry-001",
        owner_subject="operator-a",
        stage=stage,
        attempt=0,
        mission_attempt=3,
        parameters={},
        mission_parameters={
            "input_dataset": "datasets/quarry-001",
            "pipeline": "modern",
        },
        inputs=inputs,
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
    assert result.provenance["workspace_transfer"]["publish"]["logical_bytes"] == 123
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


def _mock_workspace_transfer(monkeypatch, calls):
    def restore(manifest_key, destination, checksum, cancellation_check):
        calls.append("restore")
        assert manifest_key == "upstream/manifest.json"
        assert checksum == "b" * 64
        cancellation_check()
        Path(destination, ".droneai").mkdir(parents=True, exist_ok=True)
        return RestoredWorkspace(
            size_bytes=123,
            file_count=2,
            downloaded_bytes=200,
            reused_bytes=0,
            download_seconds=0.25,
            manifest_size_bytes=77,
        )

    def publish(workspace, prefix, cancellation_check):
        calls.append("publish")
        cancellation_check()
        return PublishedWorkspace(
            manifest_key=f"{prefix}/manifest.json",
            uri=f"s3://drone-ai/{prefix}/manifest.json",
            checksum_sha256="c" * 64,
            size_bytes=456,
            file_count=4,
        )

    monkeypatch.setattr(stage_executor, "restore_workspace_measured", restore)
    monkeypatch.setattr(stage_executor, "publish_workspace", publish)


def test_gaussian_training_adapter_publishes_unfiltered_model(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))
    cancellation = FakeCancellationState()
    monkeypatch.setattr(stage_executor.runtime, "cancellation_state", cancellation)
    calls = []
    _mock_workspace_transfer(monkeypatch, calls)
    state = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(stage_executor, "load_reconstruction_state", lambda _path: state)
    checkpoint = tmp_path / "trainer" / "final.ply"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"unfiltered")
    config = SimpleNamespace(dronegs_profile_id="normal-v1")
    monkeypatch.setattr(
        gaussian_stage,
        "prepare_gaussian_product_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            config=config,
            trainer_backend="dronegs",
        ),
    )
    phase = SimpleNamespace(
        backend_name="dronegs",
        trainer_binary_sha256="d" * 64,
        training_state=SimpleNamespace(
            final_ply=str(checkpoint),
            merged_model=SimpleNamespace(num_gaussians=1_500_000),
        ),
    )
    monkeypatch.setattr(
        gaussian_workflow,
        "execute_gaussian_training_phase",
        lambda *_args, **_kwargs: phase,
    )
    written = {}

    def write_artifact(workspace, artifact_config, artifact_phase, *, model_path):
        calls.append("write")
        assert artifact_config is config
        assert artifact_phase is phase
        assert Path(model_path).read_bytes() == b"unfiltered"
        written["model"] = Path(model_path)
        return Path(workspace, ".droneai", "gaussian-training-state.json")

    monkeypatch.setattr(phase_artifacts, "write_training_artifact", write_artifact)

    result = stage_executor.run_gaussian_training_stage(
        _context("gaussian_training", input_kind="reconstruction_workspace"),
        FakeControl(),
    )

    assert calls == ["restore", "write", "publish"]
    assert result.kind == "gaussian_training_workspace"
    assert result.metadata["gaussian_count"] == 1_500_000
    assert result.provenance["trainer_binary_sha256"] == "d" * 64
    assert result.provenance["workspace_transfer"]["restore"] == {
        "logical_bytes": 123,
        "file_count": 2,
        "transferred_bytes": 200,
        "reused_bytes": 0,
        "manifest_bytes": 77,
        "duration_seconds": 0.25,
    }
    assert not written["model"].exists()
    assert cancellation.cleared == 1


def test_gaussian_filtering_adapter_never_overwrites_training_model(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))
    cancellation = FakeCancellationState()
    monkeypatch.setattr(stage_executor.runtime, "cancellation_state", cancellation)
    calls = []
    _mock_workspace_transfer(monkeypatch, calls)
    state = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(stage_executor, "load_reconstruction_state", lambda _path: state)
    config = SimpleNamespace(
        sh_degree=3,
        fagk=True,
        dronegs_profile_id="normal-v1",
    )
    monkeypatch.setattr(
        gaussian_stage,
        "prepare_gaussian_product_run",
        lambda *_args, **_kwargs: SimpleNamespace(config=config),
    )

    training_path = tmp_path / "work" / ("a" * 32) / "training.ply"
    artifact = phase_artifacts.GaussianTrainingArtifact(
        model_path=training_path,
        config_sha256="e" * 64,
        backend_name="dronegs",
        trainer_binary_sha256="d" * 64,
        gaussian_count=1_500_000,
        facade_subset_result=None,
    )

    def read_artifact(workspace, artifact_config):
        calls.append("read")
        training_path.write_bytes(b"unfiltered")
        return artifact

    monkeypatch.setattr(phase_artifacts, "read_training_artifact", read_artifact)
    fake_model_module = ModuleType("gaussian_ortho.gaussian_model")

    class FakeModel:
        def __init__(self, **_kwargs):
            self.num_gaussians = 1_500_000

        def load_ply(self, path):
            assert Path(path).read_bytes() == b"unfiltered"

    fake_model_module.GaussianModel = FakeModel
    monkeypatch.setitem(sys.modules, "gaussian_ortho.gaussian_model", fake_model_module)
    scene = SimpleNamespace()
    monkeypatch.setattr(gaussian_workflow, "prepare_gaussian_scene", lambda _config: scene)

    def hydrate(hydrated_artifact, hydrated_scene, model):
        calls.append("hydrate")
        assert hydrated_scene is scene
        assert hydrated_artifact.model_path != training_path
        assert hydrated_artifact.model_path.read_bytes() == b"unfiltered"
        return SimpleNamespace(training_state=SimpleNamespace(merged_model=model))

    monkeypatch.setattr(phase_artifacts, "hydrate_training_phase", hydrate)
    filtering = SimpleNamespace(input_gaussians=1_500_000, output_gaussians=1_200_000)
    monkeypatch.setattr(
        gaussian_workflow,
        "execute_gaussian_filtering_phase",
        lambda *_args, **_kwargs: filtering,
    )

    def write_filtering(
        workspace,
        artifact_config,
        training_phase,
        filtering_phase,
        *,
        model_path,
    ):
        calls.append("write")
        assert filtering_phase is filtering
        assert Path(model_path) != training_path
        assert training_path.read_bytes() == b"unfiltered"
        return Path(workspace, ".droneai", "gaussian-filtering-state.json")

    monkeypatch.setattr(phase_artifacts, "write_filtering_artifact", write_filtering)

    result = stage_executor.run_gaussian_filtering_stage(
        _context(
            "gaussian_filtering",
            input_kind="gaussian_training_workspace",
        ),
        FakeControl(),
    )

    assert calls == ["restore", "read", "hydrate", "write", "publish"]
    assert result.kind == "gaussian_filtering_workspace"
    assert result.quality_metrics["retained_ratio"] == 0.8
    assert result.provenance["workspace_transfer"]["publish"]["logical_bytes"] == 456
    assert cancellation.cleared == 1


def test_rasterization_adapter_qualifies_filtered_model_without_refiltering(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))
    cancellation = FakeCancellationState()
    monkeypatch.setattr(stage_executor.runtime, "cancellation_state", cancellation)
    calls = []
    _mock_workspace_transfer(monkeypatch, calls)
    state = (SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(stage_executor, "load_reconstruction_state", lambda _path: state)
    config = SimpleNamespace(
        sh_degree=3,
        fagk=True,
        dronegs_profile_id="normal-v1",
    )
    monkeypatch.setattr(
        gaussian_stage,
        "prepare_gaussian_product_run",
        lambda *_args, **_kwargs: SimpleNamespace(config=config),
    )
    model_path = tmp_path / "work" / ("a" * 32) / "filtered.ply"
    scene_summary = SimpleNamespace(scale_source="geographic-sim3")
    artifact = SimpleNamespace(
        model_path=model_path,
        scene_summary=scene_summary,
    )

    def read_artifact(workspace, artifact_config):
        calls.append("read")
        model_path.write_bytes(b"filtered")
        return artifact

    monkeypatch.setattr(phase_artifacts, "read_filtering_artifact", read_artifact)
    fake_model_module = ModuleType("gaussian_ortho.gaussian_model")

    class FakeModel:
        def __init__(self, **_kwargs):
            pass

        def load_ply(self, path):
            calls.append("load")
            assert Path(path).read_bytes() == b"filtered"

    fake_model_module.GaussianModel = FakeModel
    monkeypatch.setitem(sys.modules, "gaussian_ortho.gaussian_model", fake_model_module)
    filtering_phase = SimpleNamespace(output_gaussians=1_200_000)
    monkeypatch.setattr(
        phase_artifacts,
        "hydrate_filtering_phase",
        lambda hydrated_artifact, _model: (
            calls.append("hydrate") or filtering_phase
        ),
    )
    rasterization_phase = SimpleNamespace(width=800, height=600)
    monkeypatch.setattr(
        gaussian_workflow,
        "execute_gaussian_rasterization_phase",
        lambda raster_config, filtered: (
            calls.append("render") or rasterization_phase
        ),
    )

    def finalize(
        raster_config,
        filtered,
        rasterized,
        summary,
        *,
        final_ply,
        cupy_version,
    ):
        calls.append("finalize")
        assert filtered is filtering_phase
        assert rasterized is rasterization_phase
        assert summary is scene_summary
        assert final_ply == str(model_path)
        assert cupy_version == "test-cupy"
        workspace = model_path.parent
        ortho = workspace / "orthomosaic.tif"
        height = workspace / "orthomosaic.height.tif"
        ortho.write_bytes(b"ortho")
        height.write_bytes(b"height")
        return {
            "ortho_file": str(ortho),
            "height_file": str(height),
            "gaussian_coverage_report": None,
            "coordinate_system": "EPSG:32631",
            "raster_extent": [0.0, 0.0, 20.0, 15.0],
            "width": 800,
            "height": 600,
            "n_gaussians": 1_200_000,
            "gaussian_coverage": {"accepted": True},
            "renderer_contract": "cupy-ortho-v2-sh-frame",
            "cupy_version": cupy_version,
        }

    monkeypatch.setattr(raster_product, "finalize_gaussian_raster_product", finalize)
    fake_cupy = ModuleType("cupy")
    fake_cupy.__version__ = "test-cupy"
    monkeypatch.setitem(sys.modules, "cupy", fake_cupy)

    result = stage_executor.run_rasterization_stage(
        _context("rasterization", input_kind="gaussian_filtering_workspace"),
        FakeControl(),
    )

    assert calls == [
        "restore",
        "read",
        "load",
        "hydrate",
        "render",
        "finalize",
        "publish",
    ]
    assert result.kind == "raster_product_workspace"
    assert result.metadata["ortho_file"] == "orthomosaic.tif"
    assert result.quality_metrics["gaussian_count"] == 1_200_000
    assert result.provenance["renderer_contract"] == "cupy-ortho-v2-sh-frame"
    assert result.provenance["workspace_transfer"]["restore"]["file_count"] == 2
    assert cancellation.cleared == 1
