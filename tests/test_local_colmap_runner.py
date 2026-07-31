import json
import sqlite3
import struct
import sys

import pytest

from shared.geo_alignment import estimate_sim3
from shared.rtk_refinement import inject_database_pose_priors
from tools.run_local_colmap import (
    WORKSPACE_MARKER,
    ensure_workspace,
    parse_args,
    select_records,
    sparse_model_identity,
    sparse_model_path,
    stage_images,
    write_colmap_references,
)


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


def _create_pose_prior_database(database_path, image_count: int) -> None:
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE images (
            image_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            camera_id INTEGER NOT NULL
        );
        CREATE TABLE frame_data (
            frame_id INTEGER NOT NULL,
            data_id INTEGER NOT NULL,
            sensor_id INTEGER NOT NULL,
            sensor_type INTEGER NOT NULL
        );
        CREATE TABLE pose_priors (
            pose_prior_id INTEGER PRIMARY KEY,
            corr_data_id INTEGER NOT NULL,
            corr_sensor_id INTEGER NOT NULL,
            corr_sensor_type INTEGER NOT NULL,
            position BLOB,
            position_covariance BLOB,
            gravity BLOB,
            coordinate_system INTEGER NOT NULL
        );
        """
    )
    for image_id in range(1, image_count + 1):
        connection.execute(
            "INSERT INTO images VALUES (?, ?, 1)",
            (image_id, f"DJI_{image_id - 1:04d}.JPG"),
        )
        connection.execute(
            "INSERT INTO frame_data VALUES (?, ?, 1, 0)",
            (image_id, image_id),
        )
        connection.execute(
            "INSERT INTO pose_priors VALUES (?, ?, 1, 0, ?, ?, NULL, 0)",
            (
                image_id,
                image_id,
                struct.pack("<3d", 0.0, 0.0, 0.0),
                struct.pack("<9d", *([float("nan")] * 9)),
            ),
        )
    connection.commit()
    connection.close()


def _rtk_records(count: int) -> list[dict]:
    records = _records(count)
    for record in records:
        record["gps"].update(
            {
                "source": "dji_mrk",
                "position_std_m": {
                    "east_m": 0.02,
                    "north_m": 0.03,
                    "vertical_m": 0.04,
                },
            }
        )
    return records


def test_default_cli_profile_matches_planimetric_survey_defaults(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_local_colmap.py", "/data", "/work"],
    )

    args = parse_args()

    assert args.matcher == "gps"
    assert args.engine == "auto"
    assert args.feature_type == "SIFT"
    assert args.matcher_type == "SIFT_BRUTEFORCE"
    assert args.camera_model == "SIMPLE_RADIAL"
    assert args.feature_max_image_size == 2400
    assert args.feature_max_num_features == 4096
    assert args.sift_first_octave == -1
    assert args.guided_matching is False
    assert args.global_ba_iterations == 2
    assert args.global_ceres_iterations == 50
    assert args.global_random_seed == 42
    assert args.global_ba_min_track_length == 3
    assert args.global_tri_complete_max_reproj_error == 15.0
    assert args.global_tri_merge_max_reproj_error == 15.0
    assert args.global_tri_min_angle == 1.0
    assert args.global_retriangulation is True
    assert args.mapping_timeout_seconds == 2400
    assert args.rtk_refinement_loss_scale == 7.82


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
    assert json.loads((workspace / "selection.json").read_text(encoding="utf-8"))[0]["file"] == "DJI_0000.JPG"


def test_stage_images_can_symlink_read_only_source_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    records = _records(1)
    source = dataset / records[0]["file"]
    source.write_bytes(b"source")
    workspace = tmp_path / "workspace"
    ensure_workspace(dataset, workspace)

    stage_images(dataset, workspace, records, staging_mode="symlink")

    staged = workspace / "images" / records[0]["file"]
    assert staged.is_symlink()
    assert staged.read_bytes() == b"source"


def test_sparse_model_path_selects_most_registered_images(tmp_path):
    sparse_root = tmp_path / "sparse"
    for name, image_count in (("0", 3), ("1", 25), ("2", 8)):
        model = sparse_root / name
        model.mkdir(parents=True)
        (model / "cameras.bin").write_bytes(b"camera")
        (model / "images.bin").write_bytes(struct.pack("<Q", image_count))

    selected = sparse_model_path(tmp_path)

    assert selected == sparse_root / "1"


def test_sparse_model_identity_changes_with_model_content(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    for filename in ("cameras.bin", "images.bin", "points3D.bin"):
        (model / filename).write_bytes(filename.encode("ascii"))

    first = sparse_model_identity(model)
    (model / "points3D.bin").write_bytes(b"updated")

    assert sparse_model_identity(model) != first


def test_reference_file_uses_recommended_projected_crs(tmp_path):
    records = _records(3)

    references = write_colmap_references(records, tmp_path, "EPSG:32631")

    assert len(references) == 3
    assert (tmp_path / "geo_data.txt.crs").read_text(encoding="utf-8") == "EPSG:32631\n"
    metadata = json.loads((tmp_path / "geo_data.txt.crs.json").read_text(encoding="utf-8"))
    assert metadata["projected_crs"] == "EPSG:32631"
    assert metadata["vertical"]["reference"] == "unknown"
    assert metadata["vertical"]["orthometric_conversion_applied"] is False
    assert len((tmp_path / "geo_data.txt").read_text(encoding="utf-8").splitlines()) == 3


def test_inject_database_pose_priors_uses_mrk_position_and_enu_covariance(tmp_path):
    database_path = tmp_path / "database.db"
    _create_pose_prior_database(database_path, 3)
    records = _rtk_records(3)

    report = inject_database_pose_priors(database_path, records)

    connection = sqlite3.connect(database_path)
    position, covariance, coordinate_system = connection.execute(
        """
        SELECT position, position_covariance, coordinate_system
        FROM pose_priors WHERE pose_prior_id = 1
        """
    ).fetchone()
    connection.close()
    assert struct.unpack("<3d", position) == (43.0, 1.0, 100.0)
    assert struct.unpack("<9d", covariance) == pytest.approx((0.0004, 0, 0, 0, 0.0009, 0, 0, 0, 0.0016))
    assert coordinate_system == 0
    assert report["updated_pose_priors"] == 3
    assert report["covariance_coordinate_system"] == "local_cartesian_enu"


def test_inject_database_pose_priors_rejects_partial_mrk_coverage(tmp_path):
    database_path = tmp_path / "database.db"
    _create_pose_prior_database(database_path, 10)
    records = _rtk_records(3)

    with pytest.raises(RuntimeError, match="3/10"):
        inject_database_pose_priors(database_path, records)


def test_alignment_transform_schema_is_accepted_by_gaussian_loader():
    transform = estimate_sim3(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[10, 20, 30], [12, 20, 30], [10, 22, 30]],
    )

    assert {"R", "scale", "t"} <= transform.keys()
    assert transform["fit"]["correspondences"] == 3
