import sqlite3
import sys
from pathlib import Path

import pytest


APP1_DIR = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_DIR) not in sys.path:
    sys.path.insert(0, str(APP1_DIR))

from alignment_support import (  # noqa: E402
    build_gps_pair_graph,
    build_mapping_command,
    camera_models_in_database,
    caspar_compatibility,
    choose_auto_fallback,
    parse_colmap_reference_file,
    write_pair_list,
)


def _positioned(count: int, parent: str = "flight") -> list[dict]:
    return [
        {
            "name": f"{parent}/DJI_{index:04d}.JPG",
            "xyz": (float(index) * 5.0, 0.0, 100.0),
        }
        for index in range(count)
    ]


def test_pair_graph_is_bounded_unique_and_keeps_temporal_edges():
    pairs, stats = build_gps_pair_graph(
        _positioned(20),
        max_neighbors=4,
        min_neighbors=2,
        temporal_neighbors=2,
    )

    assert len(pairs) == len(set(pairs))
    assert all(first < second for first, second in pairs)
    assert ("flight/DJI_0000.JPG", "flight/DJI_0002.JPG") in pairs
    assert stats["positioned_images"] == 20
    assert stats["minimum_degree"] >= 2
    assert stats["pair_count"] < 20 * 10


def test_pair_graph_bridges_two_overlapping_flights_by_gps():
    records = _positioned(5, "flight_a") + [
        {
            "name": f"flight_b/DJI_{index:04d}.JPG",
            "xyz": (float(index) * 5.0 + 0.5, 1.0, 101.0),
        }
        for index in range(5)
    ]

    pairs, _ = build_gps_pair_graph(
        records,
        max_neighbors=3,
        min_neighbors=2,
        temporal_neighbors=1,
    )

    assert any(first.split("/")[0] != second.split("/")[0] for first, second in pairs)


def test_reference_parser_accepts_spaces_in_image_names(tmp_path):
    references = tmp_path / "geo_data.txt"
    references.write_text(
        "flight one/DJI 0001.JPG 10.0 20.0 30.0\n",
        encoding="utf-8",
    )

    records = parse_colmap_reference_file(references)

    assert records == [
        {"name": "flight one/DJI 0001.JPG", "xyz": (10.0, 20.0, 30.0)}
    ]


def test_pair_writer_is_deterministic(tmp_path):
    path = tmp_path / "pairs.txt"

    count = write_pair_list(path, [("b.jpg", "c.jpg"), ("a.jpg", "b.jpg")])

    assert count == 2
    assert path.read_text(encoding="utf-8") == "a.jpg b.jpg\nb.jpg c.jpg\n"


@pytest.mark.parametrize(
    ("model_id", "model_name", "compatible"),
    [(1, "PINHOLE", True), (2, "SIMPLE_RADIAL", True), (4, "OPENCV", False)],
)
def test_caspar_camera_compatibility(tmp_path, model_id, model_name, compatible):
    database = tmp_path / "database.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE cameras(camera_id INTEGER, model INTEGER)")
        connection.execute("INSERT INTO cameras VALUES(1, ?)", (model_id,))

    assert camera_models_in_database(database) == {model_name}
    assert caspar_compatibility(database) == (compatible, {model_name})


def test_mapping_commands_use_glomap_and_caspar_backends():
    glomap = build_mapping_command(
        "glomap",
        database_path="database.db",
        image_path="images",
        output_path="sparse",
        gpu_index=0,
    )
    caspar = build_mapping_command(
        "caspar",
        database_path="database.db",
        image_path="images",
        output_path="sparse",
        gpu_index=0,
    )

    assert glomap[1] == "global_mapper"
    assert "--GlobalMapper.keep_max_num_tracks" in glomap
    assert glomap[glomap.index("--GlobalMapper.ba_num_iterations") + 1] == "1"
    assert glomap[glomap.index("--GlobalMapper.skip_retriangulation") + 1] == "1"
    assert caspar[1] == "mapper"
    assert caspar[caspar.index("--Mapper.ba_local_backend") + 1] == "CASPAR"
    assert "--Mapper.ba_use_gpu" not in caspar


def test_auto_fallback_uses_caspar_only_for_supported_models():
    assert choose_auto_fallback({"SIMPLE_RADIAL"}) == "caspar"
    assert choose_auto_fallback({"PINHOLE"}) == "caspar"
    assert choose_auto_fallback({"OPENCV"}) == "ceres"
    assert choose_auto_fallback(set()) == "ceres"
