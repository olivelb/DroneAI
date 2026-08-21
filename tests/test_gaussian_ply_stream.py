from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pytest


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

partition = importlib.import_module("gaussian_ortho.partition")
ply_stream = importlib.import_module("gaussian_ortho.ply_stream")


def _write_ply(path: Path, rows: list[tuple[float, float, float, float]]) -> None:
    dtype = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("value", "<f4")])
    data = np.array(rows, dtype=dtype)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(rows)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float value\n"
        "end_header\n"
    ).encode("ascii")
    with path.open("wb") as handle:
        handle.write(header)
        data.tofile(handle)


def _bounds(x_min: float, x_max: float, column: int, *, include_max: bool) -> object:
    return partition.CellBounds(
        core_x_min=x_min,
        core_x_max=x_max,
        core_y_min=0.0,
        core_y_max=1.0,
        buffer_x_min=x_min - 0.5,
        buffer_x_max=x_max + 0.5,
        buffer_y_min=-0.5,
        buffer_y_max=1.5,
        row=0,
        col=column,
        include_core_x_max=include_max,
        include_core_y_max=True,
    )


def test_partition_core_merge_is_atomic_unique_and_schema_preserving(tmp_path):
    left = tmp_path / "left.ply"
    right = tmp_path / "right.ply"
    output = tmp_path / "unified.ply"
    _write_ply(
        left,
        [
            (0.25, 0.5, 0.0, 10.0),
            (0.75, 0.5, 0.0, 20.0),
            (1.25, 0.5, 0.0, 999.0),
        ],
    )
    _write_ply(
        right,
        [
            (0.75, 0.5, 0.0, 998.0),
            (1.25, 0.5, 0.0, 30.0),
            (1.75, 0.5, 0.0, 40.0),
        ],
    )
    partitions = (
        ply_stream.PartitionCorePly(_bounds(0.0, 1.0, 0, include_max=False), left),
        ply_stream.PartitionCorePly(_bounds(1.0, 2.0, 1, include_max=True), right),
    )

    result = ply_stream.merge_partition_cores_to_ply(
        partitions,
        output,
        expected_vertex_count=4,
        chunk_records=2,
    )

    layout = ply_stream.read_binary_ply_layout(output)
    assert result.vertex_count == layout.vertex_count == 4
    assert layout.property_names == ("x", "y", "z", "value")
    with output.open("rb") as handle:
        handle.seek(layout.header_size)
        records = np.fromfile(handle, dtype=layout.dtype, count=layout.vertex_count)
    assert records["value"].tolist() == [10.0, 20.0, 30.0, 40.0]
    assert not output.with_name(output.name + ".tmp").exists()

    plyfile = pytest.importorskip("plyfile")
    portable = plyfile.PlyData.read(output)
    assert portable["vertex"].count == 4
    assert portable["vertex"].data["value"].tolist() == [10.0, 20.0, 30.0, 40.0]


def test_partition_core_merge_rejects_count_drift_without_publishing(tmp_path):
    source = tmp_path / "source.ply"
    output = tmp_path / "unified.ply"
    _write_ply(source, [(0.25, 0.5, 0.0, 10.0)])

    with pytest.raises(RuntimeError, match="core count drifted"):
        ply_stream.merge_partition_cores_to_ply(
            (
                ply_stream.PartitionCorePly(
                    _bounds(0.0, 1.0, 0, include_max=True),
                    source,
                ),
            ),
            output,
            expected_vertex_count=2,
        )

    assert not output.exists()
    assert not output.with_name(output.name + ".tmp").exists()
