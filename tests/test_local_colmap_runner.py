import json

import pytest

from tools.run_local_colmap import (
    WORKSPACE_MARKER,
    ensure_workspace,
    select_records,
    stage_images,
    write_colmap_references,
)
from shared.geo_alignment import estimate_sim3


def _records(count: int) -> list[dict]:
    return [
        {
            "file": f"DJI_{index:04d}.JPG",
            "size_bytes": index + 1,
            "readable": True,
            "gps": {
                "latitude": 43.0 + index / 10_000,
                "longitude": 1.0 + index / 10_000,
                "altitude_m": 100.0,
            },
        }
        for index in range(count)
    ]


def test_select_records_supports_contiguous_and_uniform_strategies():
    records = _records(10)

    contiguous = select_records(records, maximum=3, start_index=2, strategy="contiguous")
    uniform = select_records(records, maximum=3, start_index=0, strategy="uniform")

    assert [record["file"] for record in contiguous] == [
        "DJI_0002.JPG",
        "DJI_0003.JPG",
        "DJI_0004.JPG",
    ]
    assert [record["file"] for record in uniform] == [
        "DJI_0000.JPG",
        "DJI_0004.JPG",
        "DJI_0009.JPG",
    ]


def test_workspace_refuses_unmarked_nonempty_directory(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "user-file.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="not empty"):
        ensure_workspace(dataset, workspace)

    assert (workspace / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_workspace_accepts_known_preflight_outputs(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "dataset_preflight.json").write_text("{}", encoding="utf-8")
    (workspace / "flight_path.geojson").write_text("{}", encoding="utf-8")

    ensure_workspace(dataset, workspace)

    assert (workspace / WORKSPACE_MARKER).is_file()


def test_stage_images_only_writes_to_marked_workspace(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    records = _records(2)
    for record in records:
        (dataset / record["file"]).write_bytes(b"x" * record["size_bytes"])
    workspace = tmp_path / "workspace"
    ensure_workspace(dataset, workspace)

    changed = stage_images(dataset, workspace, records)
    unchanged = stage_images(dataset, workspace, records)

    assert changed is True
    assert unchanged is False
    assert (workspace / WORKSPACE_MARKER).is_file()
    assert (workspace / "images" / "DJI_0001.JPG").read_bytes() == b"xx"
    assert json.loads((workspace / "selection.json").read_text(encoding="utf-8"))[0][
        "file"
    ] == "DJI_0000.JPG"


def test_reference_file_uses_recommended_projected_crs(tmp_path):
    records = _records(3)

    references = write_colmap_references(records, tmp_path, "EPSG:32631")

    assert len(references) == 3
    assert (tmp_path / "geo_data.txt.crs").read_text(encoding="utf-8") == "EPSG:32631\n"
    assert len((tmp_path / "geo_data.txt").read_text(encoding="utf-8").splitlines()) == 3


def test_alignment_transform_schema_is_accepted_by_gaussian_loader():
    transform = estimate_sim3(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[10, 20, 30], [12, 20, 30], [10, 22, 30]],
    )

    assert set(("R", "scale", "t")) <= transform.keys()
    assert transform["fit"]["correspondences"] == 3
