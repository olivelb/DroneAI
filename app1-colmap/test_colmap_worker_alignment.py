import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
for module_path in (APP_DIR, ROOT_DIR):
    if str(module_path) not in sys.path:
        sys.path.append(str(module_path))

from colmap_worker import runtime as worker_runtime
from colmap_worker.stages import alignment as alignment_stage


def _gcp_preparation(gcp_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        params={
            "gcp_horizontal_accuracy_m": "0.02",
            "gcp_vertical_accuracy_m": "0.03",
            "gcp_image_accuracy_px": "1.0",
            "gcp_robust_loss_scale": "2.5",
            "gcp_require_checkpoints": True,
            "gcp_min_checkpoint_count": "2",
            "gcp_max_checkpoint_horizontal_rmse_m": "0.10",
            "gcp_max_checkpoint_vertical_rmse_m": "0.20",
            "gcp_max_checkpoint_normalized_error_sigma": "5.0",
            "gcp_min_adjustment_baseline_m": "5.0",
        },
        gcp_path=gcp_path,
        gcp_accuracy_path=None,
    )


def test_undistortion_reuses_ready_gaussian_workspace():
    preparation = SimpleNamespace(dense_path="/workspace/dense")
    rtk_state = SimpleNamespace(
        ortho_only_ready=True,
        active_sparse_model_path="/workspace/sparse/0",
    )

    with (
        patch.object(alignment_stage, "run_command") as run_command,
        patch.object(worker_runtime, "report_mission_progress") as report,
    ):
        alignment_stage._undistort_images(preparation, rtk_state, "vol-ready")

    run_command.assert_not_called()
    report.assert_called_once_with(
        "vol-ready",
        "UNDISTORT",
        75,
        log="Undistorted images found. Skipping undistortion.",
    )


def test_undistortion_runs_colmap_when_cache_is_missing(tmp_path):
    dense_path = tmp_path / "dense"
    dense_path.mkdir()
    preparation = SimpleNamespace(
        dense_path=str(dense_path),
        clean_images_dir=str(tmp_path / "clean_images"),
        params={"mvs_max_image_size": "2400", "mvs_num_threads": "8"},
    )
    rtk_state = SimpleNamespace(
        ortho_only_ready=False,
        active_sparse_model_path=str(tmp_path / "sparse" / "0"),
    )

    with (
        patch.object(alignment_stage, "run_command") as run_command,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        alignment_stage._undistort_images(preparation, rtk_state, "vol-undistort")

    command = run_command.call_args.args[0]
    assert command[:2] == ["colmap", "image_undistorter"]
    assert command[command.index("--input_path") + 1] == rtk_state.active_sparse_model_path
    assert command[command.index("--max_image_size") + 1] == "2400"


def test_stale_gcp_alignment_is_removed_before_gnss_alignment(tmp_path):
    transform_path = tmp_path / "alignment_transform.json"
    transform_path.write_text(
        json.dumps({"fit": {"source": "covariance_weighted_gcp"}}),
        encoding="utf-8",
    )
    (tmp_path / "gcp_alignment_report.json").write_text("{}", encoding="utf-8")
    sparse_geo_path = tmp_path / "sparse_geo"
    sparse_geo_path.mkdir()
    (sparse_geo_path / "cameras.bin").write_bytes(b"model")

    result = alignment_stage._remove_stale_gcp_alignment(
        str(tmp_path),
        str(transform_path),
        str(sparse_geo_path),
    )

    assert result is None
    assert not transform_path.exists()
    assert not (tmp_path / "gcp_alignment_report.json").exists()
    assert not sparse_geo_path.exists()


def test_reference_alignment_without_positions_returns_none(tmp_path):
    preparation = SimpleNamespace(
        geo_data_file=str(tmp_path / "missing_geo_data.txt"),
        params={"alignment_max_error": "3.0"},
    )
    rtk_state = SimpleNamespace(active_sparse_model_path=str(tmp_path / "sparse" / "0"))

    with patch.object(alignment_stage, "run_command") as run_command:
        result = alignment_stage._run_reference_alignment(
            preparation,
            rtk_state,
            str(tmp_path),
            "vol-no-positions",
            None,
        )

    assert result is None
    run_command.assert_not_called()


def test_reference_alignment_reuses_compatible_cached_transform(tmp_path):
    geo_data_file = tmp_path / "geo_data.txt"
    geo_data_file.write_text("image.jpg 1 2 3\n", encoding="utf-8")
    transform_path = tmp_path / "alignment_transform.json"
    transform_path.write_text(
        json.dumps({"fit": {"source": "gnss_model_aligner"}}),
        encoding="utf-8",
    )
    sparse_geo_path = tmp_path / "sparse_geo"
    sparse_geo_path.mkdir()
    (sparse_geo_path / "cameras.bin").write_bytes(b"model")
    preparation = SimpleNamespace(
        geo_data_file=str(geo_data_file),
        params={"alignment_max_error": "3.0"},
    )
    rtk_state = SimpleNamespace(active_sparse_model_path=str(tmp_path / "sparse" / "0"))

    with patch.object(alignment_stage, "run_command") as run_command:
        result = alignment_stage._run_reference_alignment(
            preparation,
            rtk_state,
            str(tmp_path),
            "vol-cached-transform",
            str(transform_path),
        )

    assert result == str(transform_path)
    run_command.assert_not_called()


def test_reference_alignment_creates_transform_from_aligned_model(tmp_path):
    geo_data_file = tmp_path / "geo_data.txt"
    geo_data_file.write_text("image.jpg 1 2 3\n", encoding="utf-8")
    preparation = SimpleNamespace(
        geo_data_file=str(geo_data_file),
        params={"alignment_max_error": "3.0"},
    )
    active_sparse_model = str(tmp_path / "sparse" / "0")
    rtk_state = SimpleNamespace(active_sparse_model_path=active_sparse_model)
    transform = {
        "scale": 1.01,
        "fit": {"correspondences": 12, "rmse": 0.08},
    }

    with (
        patch.object(alignment_stage, "run_command") as run_command,
        patch(
            "shared.geo_alignment.compute_reconstruction_alignment",
            return_value=transform,
        ) as compute,
        patch("shared.geo_alignment.write_alignment_transform") as write_transform,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        result = alignment_stage._run_reference_alignment(
            preparation,
            rtk_state,
            str(tmp_path),
            "vol-new-transform",
            None,
        )

    sparse_geo_path = os.path.join(tmp_path, "sparse_geo")
    transform_path = os.path.join(tmp_path, "alignment_transform.json")
    assert run_command.call_args.args[0][:2] == ["colmap", "model_aligner"]
    compute.assert_called_once_with(active_sparse_model, sparse_geo_path)
    write_transform.assert_called_once_with(transform_path, transform)
    assert result == transform_path


def test_rejected_gcp_alignment_writes_report_without_promoting(tmp_path):
    preparation = _gcp_preparation(str(tmp_path / "gcp_list.txt"))
    reconstruction = SimpleNamespace(utm_crs="EPSG:32631")
    rtk_state = SimpleNamespace(active_sparse_model_path=str(tmp_path / "sparse" / "0"))
    report = {
        "quality_gate": {
            "accepted": False,
            "checks": [{"name": "checkpoint_count", "passed": False}],
        }
    }

    with (
        patch.object(
            alignment_stage,
            "build_weighted_gcp_alignment",
            return_value=({}, report),
        ),
        patch.object(alignment_stage, "atomic_write_json") as write_report,
        patch.object(alignment_stage, "write_transformed_reconstruction") as promote,
    ):
        with pytest.raises(RuntimeError, match="checkpoint_count"):
            alignment_stage._run_weighted_gcp_alignment(
                preparation,
                reconstruction,
                rtk_state,
                str(tmp_path),
                "vol-rejected-gcp",
            )

    write_report.assert_called_once_with(
        os.path.join(tmp_path, "gcp_alignment_report.json"),
        report,
    )
    promote.assert_not_called()


def test_accepted_gcp_alignment_promotes_transform_and_sparse_model(tmp_path):
    preparation = _gcp_preparation(str(tmp_path / "gcp_list.txt"))
    reconstruction = SimpleNamespace(utm_crs="EPSG:32631")
    active_sparse_model = str(tmp_path / "sparse" / "0")
    rtk_state = SimpleNamespace(active_sparse_model_path=active_sparse_model)
    transform = {
        "scale": 1.0,
        "fit": {"rmse": 0.04, "weighted_rmse": 1.2},
    }
    report = {
        "adjustment_points": 5,
        "checkpoint_points": 2,
        "quality_gate": {"accepted": True, "checks": [], "status": "accepted-verified"},
    }

    with (
        patch.object(
            alignment_stage,
            "build_weighted_gcp_alignment",
            return_value=(transform, report),
        ),
        patch.object(alignment_stage, "atomic_write_json"),
        patch("shared.geo_alignment.write_alignment_transform") as write_transform,
        patch.object(alignment_stage, "write_transformed_reconstruction") as promote,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        result = alignment_stage._run_weighted_gcp_alignment(
            preparation,
            reconstruction,
            rtk_state,
            str(tmp_path),
            "vol-accepted-gcp",
        )

    transform_path = os.path.join(tmp_path, "alignment_transform.json")
    assert result == transform_path
    write_transform.assert_called_once_with(transform_path, transform)
    promote.assert_called_once_with(
        active_sparse_model,
        os.path.join(tmp_path, "sparse_geo"),
        transform,
    )
