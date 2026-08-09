from pathlib import Path

import pytest

from colmap_worker.contracts import (
    PipelineAlignmentState,
    PipelinePreparation,
    PipelineReconstruction,
)
from colmap_worker.stage_state import (
    STATE_RELATIVE_PATH,
    load_reconstruction_state,
    write_reconstruction_state,
)


def _preparation(workspace: Path) -> PipelinePreparation:
    image = workspace / "clean_images" / "image-001.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    return PipelinePreparation(
        params={"gs_iterations": "15000"},
        facade_mode=False,
        orthophoto_mode="map",
        mission_s3_prefix="missions/example",
        clean_images_dir=str(workspace / "clean_images"),
        db_path=str(workspace / "database.db"),
        sparse_path=str(workspace / "sparse"),
        geo_data_file=str(workspace / "geo_data.txt"),
        dense_path=str(workspace / "dense"),
        gcp_path=str(workspace / "gcp_list.txt"),
        gcp_accuracy_path=None,
        facade_selection_report_path=str(workspace / "facade_selection_report.json"),
        feature_type="ALIKED_N32",
        matcher_type="LIGHTGLUE",
        feature_family="ALIKED",
        resolved_matcher_type="ALIKED_LIGHTGLUE",
        feature_gpu_index="0",
        ba_gpu_index="0",
        projected_crs_mode="auto-local",
        requested_projected_crs="",
        image_reader_camera_model="SIMPLE_RADIAL",
        image_reader_camera_params=None,
        images=[image],
    )


def test_reconstruction_state_round_trip_rebases_all_workspace_paths(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    preparation = _preparation(source)
    transform = source / "alignment_transform.json"
    transform.write_text("{}", encoding="utf-8")
    reconstruction = PipelineReconstruction(
        utm_crs="EPSG:2154",
        alignment_transform_path=str(transform),
        ortho_only_ready=False,
    )
    alignment = PipelineAlignmentState(alignment_transform_path=str(transform))

    state_path = write_reconstruction_state(
        source,
        preparation,
        reconstruction,
        alignment,
    )
    assert state_path == source / STATE_RELATIVE_PATH

    restored = tmp_path / "restored"
    restored.mkdir()
    for path in source.rglob("*"):
        if path.is_file():
            target = restored / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    loaded_preparation, loaded_reconstruction, loaded_alignment = (
        load_reconstruction_state(restored)
    )

    assert loaded_preparation.params == {"gs_iterations": "15000"}
    assert loaded_preparation.clean_images_dir == str(restored / "clean_images")
    assert loaded_preparation.images == [restored / "clean_images" / "image-001.jpg"]
    assert loaded_reconstruction.utm_crs == "EPSG:2154"
    assert loaded_alignment.alignment_transform_path == str(
        restored / "alignment_transform.json"
    )


def test_reconstruction_state_rejects_paths_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    preparation = _preparation(workspace)
    outside = tmp_path / "outside.json"

    with pytest.raises(ValueError):
        write_reconstruction_state(
            workspace,
            preparation,
            PipelineReconstruction(
                utm_crs=None,
                alignment_transform_path=str(outside),
                ortho_only_ready=False,
            ),
            PipelineAlignmentState(alignment_transform_path=None),
        )
