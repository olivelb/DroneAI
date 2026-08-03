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
from colmap_worker import sparse_mapping


def _preparation(tmp_path: Path, engine: str) -> SimpleNamespace:
    return SimpleNamespace(
        params={
            "alignment_engine": engine,
            "mapping_timeout_seconds": "60",
            "minimum_registration_ratio": "0.8",
            "maximum_mean_reprojection_error_px": "2.0",
            "minimum_median_track_length": "3.0",
            "global_mapper_max_tracks": "10000",
            "global_mapper_ba_iterations": "2",
            "global_mapper_ceres_iterations": "25",
            "global_mapper_skip_retriangulation": True,
            "global_mapper_random_seed": "42",
            "global_mapper_ba_min_track_length": "3",
            "global_mapper_tri_complete_max_reproj_error": "15.0",
            "global_mapper_tri_merge_max_reproj_error": "8.0",
            "global_mapper_tri_min_angle": "1.5",
        },
        db_path=str(tmp_path / "database.db"),
        sparse_path=str(tmp_path / "sparse"),
        clean_images_dir=str(tmp_path / "images"),
        ba_gpu_index="0",
        facade_mode=False,
        images=[Path(f"image-{index}.jpg") for index in range(10)],
    )


def test_sparse_mapping_rejects_unknown_engine(tmp_path):
    preparation = _preparation(tmp_path, "unknown")

    with pytest.raises(ValueError, match="Unsupported alignment engine"):
        sparse_mapping.run_sparse_mapping(
            preparation,
            "vol-unknown-engine",
            gravity_available=False,
            match_counts={"two_view_geometries": 20},
        )


def test_sparse_mapping_rejects_incompatible_caspar_camera(tmp_path):
    preparation = _preparation(tmp_path, "caspar")

    with patch.object(
        sparse_mapping,
        "caspar_compatibility",
        return_value=(False, {"OPENCV"}),
    ):
        with pytest.raises(RuntimeError, match="PINHOLE and SIMPLE_RADIAL"):
            sparse_mapping.run_sparse_mapping(
                preparation,
                "vol-incompatible-caspar",
                gravity_available=False,
                match_counts={"two_view_geometries": 20},
            )


def test_sparse_mapping_accepts_primary_engine_quality(tmp_path):
    preparation = _preparation(tmp_path, "glomap")
    accepted_quality = {
        "registered_images": 9,
        "points3D": 500,
        "mean_reprojection_error_px": 1.2,
        "median_track_length": 4.0,
    }

    with (
        patch.object(sparse_mapping, "build_mapping_command", return_value=["mapper"]) as build,
        patch.object(sparse_mapping, "run_command") as run_command,
        patch.object(
            sparse_mapping,
            "inspect_sparse_quality",
            return_value=accepted_quality,
        ),
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        sparse_mapping.run_sparse_mapping(
            preparation,
            "vol-primary-accepted",
            gravity_available=True,
            match_counts={"two_view_geometries": 20},
        )

    assert build.call_args.args[0] == "glomap"
    assert build.call_args.kwargs["global_use_gravity"] is True
    assert run_command.call_count == 1
    assert run_command.call_args.args[0] == ["mapper"]
