import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
for module_path in (APP_DIR, ROOT_DIR):
    if str(module_path) not in sys.path:
        sys.path.append(str(module_path))

from colmap_worker import runtime as worker_runtime
from colmap_worker.stages import rtk as rtk_stage


def _preparation(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    sparse_path = tmp_path / "sparse"
    base_model = sparse_path / "0"
    base_model.mkdir(parents=True)
    (base_model / "cameras.bin").write_bytes(b"visual")
    clean_images = tmp_path / "images"
    clean_images.mkdir()
    dense_path = tmp_path / "dense"
    dense_path.mkdir()
    params = {
        "rtk_refinement_enabled": True,
        "rtk_minimum_point_ratio": "0.90",
        "rtk_maximum_reprojection_degradation_px": "0.10",
        "rtk_maximum_track_length_loss_ratio": "0.25",
        "rtk_maximum_focal_length_change_ratio": "0.02",
        "rtk_refinement_timeout_seconds": "120",
        "rtk_refinement_iterations": "25",
        "rtk_refinement_loss_scale": "7.82",
    }
    params.update(overrides)
    return SimpleNamespace(
        params=params,
        clean_images_dir=str(clean_images),
        db_path=str(tmp_path / "database.db"),
        dense_path=str(dense_path),
        sparse_path=str(sparse_path),
        ba_gpu_index="0",
    )


def _run(
    preparation: SimpleNamespace,
    tmp_path: Path,
    *,
    ortho_only_ready: bool = False,
):
    reconstruction = SimpleNamespace(ortho_only_ready=ortho_only_ready)
    return rtk_stage.refine_colmap_rtk(
        preparation,
        reconstruction,
        str(tmp_path),
        "vol-rtk",
    )


def test_disabled_rtk_refinement_keeps_visual_baseline(tmp_path):
    preparation = _preparation(tmp_path, rtk_refinement_enabled=False)

    with patch.object(rtk_stage, "run_command") as run_command:
        state = _run(preparation, tmp_path, ortho_only_ready=True)

    assert state.active_sparse_model_path == os.path.join(preparation.sparse_path, "0")
    assert state.ortho_only_ready is True
    assert state.report_path == str(tmp_path / "rtk_prior_report.json")
    run_command.assert_not_called()


def test_cached_accepted_candidate_is_rechecked_and_promoted(tmp_path):
    preparation = _preparation(tmp_path)
    candidate = tmp_path / "sparse_rtk"
    candidate.mkdir()
    (candidate / "cameras.bin").write_bytes(b"rtk")
    report_path = tmp_path / "rtk_prior_report.json"
    report_path.write_text(
        json.dumps({"selected_model": "visual_baseline", "source": "dji_mrk"}),
        encoding="utf-8",
    )
    quality_gate = {"accepted": True, "checks": []}

    with (
        patch.object(rtk_stage, "inspect_sparse_quality", return_value={"registered_images": 10}) as inspect,
        patch.object(rtk_stage, "assess_rtk_refinement_quality", return_value=quality_gate) as assess,
        patch.object(rtk_stage, "remove_rtk_dependent_artifacts") as invalidate,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        state = _run(preparation, tmp_path, ortho_only_ready=True)

    assert state.active_sparse_model_path == str(candidate)
    assert state.ortho_only_ready is False
    assert inspect.call_count == 2
    assess.assert_called_once()
    invalidate.assert_called_once_with(str(tmp_path), preparation.dense_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["selected_model"] == "rtk_candidate"
    assert report["quality_gate"] == quality_gate


def test_cached_rejected_candidate_rolls_back_and_invalidates_products(tmp_path):
    preparation = _preparation(tmp_path)
    candidate = tmp_path / "sparse_rtk"
    candidate.mkdir()
    (candidate / "cameras.bin").write_bytes(b"rtk")
    report_path = tmp_path / "rtk_prior_report.json"
    report_path.write_text(
        json.dumps({"selected_model": "rtk_candidate"}),
        encoding="utf-8",
    )
    quality_gate = {"accepted": False, "checks": [{"name": "reprojection", "passed": False}]}

    with (
        patch.object(rtk_stage, "inspect_sparse_quality", return_value={}),
        patch.object(rtk_stage, "assess_rtk_refinement_quality", return_value=quality_gate),
        patch.object(rtk_stage, "remove_rtk_dependent_artifacts") as invalidate,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        state = _run(preparation, tmp_path, ortho_only_ready=True)

    assert state.active_sparse_model_path == os.path.join(preparation.sparse_path, "0")
    assert state.ortho_only_ready is False
    invalidate.assert_called_once_with(str(tmp_path), preparation.dense_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "rejected-quality-gate"
    assert report["selected_model"] == "visual_baseline"


def test_fresh_candidate_runs_pose_prior_mapper_and_records_acceptance(tmp_path):
    preparation = _preparation(tmp_path)
    candidate = tmp_path / "sparse_rtk"
    quality_gate = {"accepted": True, "checks": []}

    def write_candidate(*_args, **_kwargs):
        candidate.mkdir(exist_ok=True)
        (candidate / "cameras.bin").write_bytes(b"rtk")

    with (
        patch.object(rtk_stage, "load_rtk_records", return_value=[object()] * 3),
        patch.object(
            rtk_stage,
            "inject_database_pose_priors",
            return_value={"updated_pose_priors": 3, "sources": ["dji_mrk"]},
        ),
        patch.object(rtk_stage, "run_command", side_effect=write_candidate) as run_command,
        patch.object(rtk_stage, "inspect_sparse_quality", return_value={}),
        patch.object(rtk_stage, "assess_rtk_refinement_quality", return_value=quality_gate),
        patch.object(rtk_stage, "remove_rtk_dependent_artifacts") as invalidate,
        patch.object(worker_runtime, "report_mission_progress"),
        patch.object(rtk_stage.time, "monotonic", side_effect=[100.0, 102.5]),
    ):
        state = _run(preparation, tmp_path)

    command = run_command.call_args.args[0]
    assert command[:2] == ["colmap", "pose_prior_mapper"]
    assert command[command.index("--Mapper.ba_gpu_index") + 1] == "0"
    assert run_command.call_args.kwargs["timeout_seconds"] == 120.0
    assert state.active_sparse_model_path == str(candidate)
    invalidate.assert_called_once_with(str(tmp_path), preparation.dense_path)
    report = json.loads((tmp_path / "rtk_prior_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["elapsed_seconds"] == 2.5
    assert report["selected_model"] == "rtk_candidate"


def test_failed_refinement_removes_candidate_and_writes_fallback_report(tmp_path):
    preparation = _preparation(tmp_path)
    candidate = tmp_path / "sparse_rtk"

    with (
        patch.object(rtk_stage, "load_rtk_records", return_value=[object()] * 3),
        patch.object(
            rtk_stage,
            "inject_database_pose_priors",
            return_value={"updated_pose_priors": 3},
        ),
        patch.object(rtk_stage, "run_command", side_effect=TimeoutError("budget exhausted")),
        patch.object(worker_runtime, "report_mission_progress") as progress,
    ):
        state = _run(preparation, tmp_path)

    assert state.active_sparse_model_path == os.path.join(preparation.sparse_path, "0")
    assert not candidate.exists()
    report = json.loads((tmp_path / "rtk_prior_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "skipped-or-fallback"
    assert report["selected_model"] == "visual_baseline"
    assert "budget exhausted" in report["reason"]
    assert progress.call_args.kwargs["details"]["event"] == "rtk_refinement_fallback"
