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
from colmap_worker.stages import publication as publication_stage


def _verified_context(tmp_path: Path, *, gcp_enabled: bool = False):
    ortho_file = tmp_path / "orthomosaic.tif"
    height_file = tmp_path / "height.tif"
    final_ply = tmp_path / "final.ply"
    checkpoint_dir = tmp_path / "checkpoints" / "full"
    checkpoint_dir.mkdir(parents=True)
    for artifact in (ortho_file, height_file, final_ply):
        artifact.write_bytes(b"artifact")
    coverage_report = tmp_path / "gaussian_coverage_report.json"
    coverage_report.write_text('{"accepted": true}', encoding="utf-8")
    (checkpoint_dir / "trainer_run.json").write_text("{}", encoding="utf-8")
    (checkpoint_dir / "canary_result.json").write_text("{}", encoding="utf-8")
    preparation = SimpleNamespace(
        params={"gcp_adjustment_enabled": gcp_enabled},
        facade_mode=False,
        facade_selection_report_path=str(tmp_path / "facade-selection.json"),
    )
    rtk_state = SimpleNamespace(report_path=str(tmp_path / "rtk-report.json"))
    alignment_state = SimpleNamespace(alignment_transform_path=None)
    gaussian_state = SimpleNamespace(
        ortho_file=str(ortho_file),
        result={
            "height_file": str(height_file),
            "final_ply": str(final_ply),
            "checkpoint_dir": str(tmp_path / "checkpoints"),
            "gaussian_coverage_report": str(coverage_report),
        },
    )
    return preparation, rtk_state, alignment_state, gaussian_state


def test_product_verification_accepts_complete_non_gcp_assets(tmp_path):
    context = _verified_context(tmp_path)

    with patch.object(publication_stage, "convert_to_cog") as convert:
        assets = publication_stage._verify_product_assets(*context, str(tmp_path))

    assert assets.gcp_enabled is False
    assert assets.gcp_sparse_files == ()
    assert len(assets.trainer_manifests) == 1
    assert len(assets.qualification_manifests) == 1
    assert convert.call_count == 2


def test_aerial_product_requires_spatial_coverage_report(tmp_path):
    context = _verified_context(tmp_path)
    context[3].result["gaussian_coverage_report"] = None

    with patch.object(publication_stage, "convert_to_cog"):
        with pytest.raises(FileNotFoundError, match="spatial coverage report"):
            publication_stage._verify_product_assets(*context, str(tmp_path))


def test_gcp_verification_requires_transform_report_and_sparse_model(tmp_path):
    preparation, rtk_state, alignment_state, gaussian_state = _verified_context(
        tmp_path,
        gcp_enabled=True,
    )

    with patch.object(publication_stage, "convert_to_cog"):
        with pytest.raises(FileNotFoundError, match="alignment transform"):
            publication_stage._verify_product_assets(
                preparation,
                rtk_state,
                alignment_state,
                gaussian_state,
                str(tmp_path),
            )

    transform = tmp_path / "alignment_transform.json"
    transform.write_text("{}", encoding="utf-8")
    alignment_state.alignment_transform_path = str(transform)
    (tmp_path / "gcp_alignment_report.json").write_text("{}", encoding="utf-8")
    sparse_geo = tmp_path / "sparse_geo"
    sparse_geo.mkdir()
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        (sparse_geo / name).write_bytes(b"model")

    with patch.object(publication_stage, "convert_to_cog"):
        assets = publication_stage._verify_product_assets(
            preparation,
            rtk_state,
            alignment_state,
            gaussian_state,
            str(tmp_path),
        )

    assert assets.gcp_enabled is True
    assert tuple(Path(path).name for path in assets.gcp_sparse_files) == (
        "cameras.bin",
        "images.bin",
        "points3D.bin",
    )


def test_optional_recovery_uploads_are_counted_but_remain_best_effort(tmp_path):
    geo_data = tmp_path / "geo_data.txt"
    database = tmp_path / "database.db"
    sparse_path = tmp_path / "sparse"
    dense_path = tmp_path / "dense"
    checkpoint_path = tmp_path / "checkpoints"
    sparse_geo = tmp_path / "sparse_geo"
    for directory in (sparse_path / "0", dense_path, checkpoint_path, sparse_geo):
        directory.mkdir(parents=True)
    for path in (geo_data, database, Path(f"{geo_data}.crs"), Path(f"{geo_data}.crs.json")):
        path.write_bytes(b"asset")

    with (
        patch.object(publication_stage.storage, "upload_file"),
        patch.object(
            publication_stage.storage,
            "upload_directory",
            side_effect=[2, 3, 4, 5],
        ) as upload_directory,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        count, complete = publication_stage._upload_optional_recovery_artifacts(
            geo_data_file=str(geo_data),
            mission_s3_prefix="missions/vol",
            vol_id="vol",
            db_path=str(database),
            sparse_path=str(sparse_path),
            workspace_dir=str(tmp_path),
            gcp_enabled=False,
            dense_path=str(dense_path),
            durable_checkpoint_dir=str(checkpoint_path),
            upload_count=5,
        )

    assert count == 23
    assert complete is True
    assert upload_directory.call_count == 4

    with (
        patch.object(publication_stage.storage, "upload_file", side_effect=OSError("offline")),
        patch.object(worker_runtime, "report_mission_progress") as progress,
    ):
        count, complete = publication_stage._upload_optional_recovery_artifacts(
            geo_data_file=str(geo_data),
            mission_s3_prefix="missions/vol",
            vol_id="vol",
            db_path=str(database),
            sparse_path=str(sparse_path),
            workspace_dir=str(tmp_path),
            gcp_enabled=False,
            dense_path=str(dense_path),
            durable_checkpoint_dir=str(checkpoint_path),
            upload_count=7,
        )

    assert (count, complete) == (7, False)
    assert "optional recovery/debug artifact" in progress.call_args.kwargs["log"]


def test_publish_products_uploads_required_assets_before_optional_recovery(tmp_path):
    checkpoint_root = tmp_path / "checkpoints"
    trainer = checkpoint_root / "full" / "trainer_run.json"
    qualification = checkpoint_root / "full" / "canary_result.json"
    trainer.parent.mkdir(parents=True)
    assets = publication_stage.VerifiedProductAssets(
        height_tif=str(tmp_path / "height.tif"),
        final_ply=str(tmp_path / "final.ply"),
        trainer_manifests=(trainer,),
        qualification_manifests=(qualification,),
        required_reports={"rtk_prior_report": None},
        gcp_enabled=False,
        gcp_sparse_files=(),
    )
    preparation = SimpleNamespace(
        facade_mode=False,
        mission_s3_prefix="missions/vol",
        db_path=str(tmp_path / "database.db"),
        sparse_path=str(tmp_path / "sparse"),
        geo_data_file=str(tmp_path / "geo_data.txt"),
        dense_path=str(tmp_path / "dense"),
    )
    reconstruction = SimpleNamespace()
    rtk_state = SimpleNamespace()
    alignment_state = SimpleNamespace()
    gaussian_state = SimpleNamespace(
        ortho_file=str(tmp_path / "orthomosaic.tif"),
        result={"checkpoint_dir": str(checkpoint_root)},
        durable_checkpoint_dir=str(checkpoint_root),
    )

    with (
        patch.object(publication_stage, "_verify_product_assets", return_value=assets),
        patch.object(
            publication_stage,
            "_write_verified_product_manifest",
            return_value=str(tmp_path / "product_manifest.json"),
        ),
        patch.object(publication_stage, "metadata_path", side_effect=lambda path: f"{path}.json"),
        patch.object(publication_stage, "preview_path", side_effect=lambda path: f"{path}.webp"),
        patch.object(publication_stage.storage, "upload_verified_file") as verified_upload,
        patch.object(
            publication_stage,
            "_upload_optional_recovery_artifacts",
            return_value=(10, True),
        ) as optional_upload,
        patch.object(worker_runtime, "report_mission_progress"),
    ):
        state = publication_stage.publish_colmap_products(
            preparation,
            reconstruction,
            rtk_state,
            alignment_state,
            gaussian_state,
            str(tmp_path),
            "vol",
        )

    assert state.ortho_s3_key == "missions/vol/orthomosaic.tif"
    assert state.gaussian_upload_complete is True
    assert verified_upload.call_count == 10
    optional_upload.assert_called_once()


@pytest.mark.parametrize("facade_mode", [False, True])
def test_completion_routes_only_aerial_products_to_detection(tmp_path, facade_mode):
    preparation = SimpleNamespace(facade_mode=facade_mode)
    publication_state = SimpleNamespace(
        ortho_s3_key="missions/vol/orthomosaic.tif",
        gaussian_upload_complete=False,
    )
    gaussian_state = SimpleNamespace(
        durable_checkpoint_dir="",
        checkpoint_s3_prefix="missions/vol/checkpoints",
    )
    producer = object()

    with (
        patch.object(publication_stage, "cleanup_pipeline_workspace") as cleanup,
        patch.object(worker_runtime, "report_mission_progress") as progress,
        patch.object(worker_runtime, "require_producer", return_value=producer),
        patch.object(publication_stage, "publish_next_stage_message") as publish_next,
    ):
        publication_stage.complete_colmap_pipeline(
            preparation,
            publication_state,
            gaussian_state,
            str(tmp_path),
            "vol",
            {"ai_backend": "yolo"},
        )

    cleanup.assert_called_once_with(str(tmp_path), "vol")
    done_call = next(call for call in progress.call_args_list if call.args[1] == "DONE")
    assert done_call.kwargs["status"] == "success"
    if facade_mode:
        assert done_call.kwargs["details"]["terminal"] is True
        publish_next.assert_not_called()
    else:
        publish_next.assert_called_once()


def test_completion_stops_after_raster_publication_when_detection_is_not_selected(
    tmp_path,
):
    preparation = SimpleNamespace(facade_mode=False)
    publication_state = SimpleNamespace(
        ortho_s3_key="missions/vol/orthomosaic.tif",
        gaussian_upload_complete=False,
    )
    gaussian_state = SimpleNamespace(
        durable_checkpoint_dir="",
        checkpoint_s3_prefix="missions/vol/checkpoints",
    )

    with (
        patch.object(publication_stage, "cleanup_pipeline_workspace"),
        patch.object(worker_runtime, "report_mission_progress") as progress,
        patch.object(publication_stage, "publish_next_stage_message") as publish_next,
    ):
        publication_stage.complete_colmap_pipeline(
            preparation,
            publication_state,
            gaussian_state,
            str(tmp_path),
            "vol",
            {
                "phases": [
                    "reconstruction",
                    "gaussian_training",
                    "gaussian_filtering",
                    "rasterization",
                ]
            },
        )

    publish_next.assert_not_called()
    terminal = progress.call_args_list[-1]
    assert terminal.kwargs["details"]["event"] == "selected_pipeline_complete"


def test_workspace_cleanup_reports_verified_success(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifact.bin").write_bytes(b"temporary")

    with patch.object(worker_runtime, "report_mission_progress") as progress:
        cleaned = publication_stage.cleanup_pipeline_workspace(
            str(workspace),
            "vol",
        )

    assert cleaned is True
    assert not workspace.exists()
    assert progress.call_args.kwargs["details"]["event"] == "workspace_cleanup_succeeded"


def test_workspace_cleanup_failure_is_observable_and_non_fatal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with (
        patch.object(
            publication_stage.shutil,
            "rmtree",
            side_effect=PermissionError("workspace busy"),
        ),
        patch.object(worker_runtime, "report_mission_progress") as progress,
    ):
        cleaned = publication_stage.cleanup_pipeline_workspace(
            str(workspace),
            "vol",
        )

    assert cleaned is False
    assert workspace.exists()
    assert progress.call_args.kwargs["details"] == {
        "event": "workspace_cleanup_failed",
        "workspace_dir": str(workspace),
        "final_pass": False,
        "error": "PermissionError: workspace busy",
    }


def test_final_cleanup_uses_logs_without_overwriting_terminal_status(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with (
        patch.object(
            publication_stage.shutil,
            "rmtree",
            side_effect=PermissionError("workspace busy"),
        ),
        patch.object(worker_runtime, "report_mission_progress") as progress,
        patch.object(publication_stage.logger, "warning") as warning,
    ):
        cleaned = publication_stage.cleanup_pipeline_workspace(
            str(workspace),
            "vol",
            final_pass=True,
        )

    assert cleaned is False
    progress.assert_not_called()
    warning.assert_called_once()
