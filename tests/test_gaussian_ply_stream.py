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


def _write_opacity_ply(
    path: Path,
    rows: list[tuple[float, float, float]],
    *,
    alpha: float = 0.8,
    opacity_sh: float = 2.0,
) -> None:
    dtype = np.dtype(
        [
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("opacity", "<f4"),
            ("opacity_sh_0", "<f4"),
        ]
    )
    data = np.zeros(len(rows), dtype=dtype)
    data["x"], data["y"], data["z"] = np.asarray(rows).T
    data["opacity"] = np.log(alpha / (1.0 - alpha))
    data["opacity_sh_0"] = opacity_sh
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(rows)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float opacity\n"
        "property float opacity_sh_0\n"
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
    assert ply_stream.CORE_MERGE_COMMENT in layout.comments
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


def test_partition_buffer_merge_preserves_support_with_optical_depth_feathering(
    tmp_path,
):
    left = tmp_path / "left.ply"
    right = tmp_path / "right.ply"
    output = tmp_path / "seam-safe.ply"
    shared = [(0.75, 0.5, 0.0), (1.0, 0.5, 0.0), (1.25, 0.5, 0.0)]
    _write_opacity_ply(left, [(-0.25, 0.5, 0.0), *shared])
    _write_opacity_ply(right, [*shared, (2.25, 0.5, 0.0)])
    partitions = (
        ply_stream.PartitionCorePly(_bounds(0.0, 1.0, 0, include_max=False), left),
        ply_stream.PartitionCorePly(_bounds(1.0, 2.0, 1, include_max=True), right),
    )

    result = ply_stream.merge_partition_buffers_to_ply(
        partitions,
        output,
        chunk_records=2,
    )

    layout = ply_stream.read_binary_ply_layout(output)
    assert layout.vertex_count == 6
    assert result.source_vertex_count == 8
    assert result.algorithm == ply_stream.FEATHERED_MERGE_COMMENT
    assert ply_stream.FEATHERED_MERGE_COMMENT in layout.comments
    with output.open("rb") as handle:
        handle.seek(layout.header_size)
        records = np.fromfile(handle, dtype=layout.dtype, count=layout.vertex_count)
    for x in (0.75, 1.0, 1.25):
        seam_records = records[np.isclose(records["x"], x)]
        assert seam_records.shape[0] == 2
        alpha = 1.0 / (1.0 + np.exp(-seam_records["opacity"]))
        composite_alpha = 1.0 - np.prod(1.0 - alpha)
        assert composite_alpha == pytest.approx(0.8, abs=1.0e-6)
        assert seam_records["opacity_sh_0"].sum() == pytest.approx(2.0)


def test_partition_buffer_merge_rejects_non_gaussian_ply(tmp_path):
    source = tmp_path / "source.ply"
    output = tmp_path / "seam-safe.ply"
    _write_ply(source, [(0.25, 0.5, 0.0, 10.0)])

    with pytest.raises(ValueError, match="requires Gaussian opacity"):
        ply_stream.merge_partition_buffers_to_ply(
            (
                ply_stream.PartitionCorePly(
                    _bounds(0.0, 1.0, 0, include_max=True),
                    source,
                ),
            ),
            output,
        )

    assert not output.exists()


def test_partition_opacity_weights_form_partition_of_unity_at_2d_junctions():
    bounds = tuple(
        partition.CellBounds(
            core_x_min=float(column),
            core_x_max=float(column + 1),
            core_y_min=float(row),
            core_y_max=float(row + 1),
            buffer_x_min=float(column) - 0.4,
            buffer_x_max=float(column + 1) + 0.4,
            buffer_y_min=float(row) - 0.4,
            buffer_y_max=float(row + 1) + 0.4,
            row=row,
            col=column,
            include_core_x_max=column == 1,
            include_core_y_max=row == 1,
        )
        for row in range(2)
        for column in range(2)
    )
    records = np.zeros(
        6,
        dtype=np.dtype(
            [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("opacity", "<f4")]
        ),
    )
    records["x"] = [0.25, 1.0, 1.0, 0.9, 1.75, 2.5]
    records["y"] = [0.25, 0.5, 1.0, 1.1, 1.75, 2.5]

    owner_weights = []
    domains = []
    for owner in bounds:
        weights, domain = ply_stream.partition_opacity_weights(
            records,
            owner,
            bounds,
        )
        owner_weights.append(weights)
        domains.append(domain)

    weight_sum = np.sum(owner_weights, axis=0)
    assert np.allclose(weight_sum[:5], 1.0)
    assert weight_sum[5] == 0.0
    assert all(domain.tolist() == [True, True, True, True, True, False] for domain in domains)


def test_partition_buffer_merge_removes_temporary_file_after_failure(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source.ply"
    output = tmp_path / "seam-safe.ply"
    _write_opacity_ply(source, [(0.25, 0.5, 0.0)])

    def fail_copy(*_args, **_kwargs):
        raise OSError("injected write failure")

    monkeypatch.setattr(ply_stream, "_copy_feathered_records", fail_copy)
    with pytest.raises(OSError, match="injected write failure"):
        ply_stream.merge_partition_buffers_to_ply(
            (
                ply_stream.PartitionCorePly(
                    _bounds(0.0, 1.0, 0, include_max=True),
                    source,
                ),
            ),
            output,
        )

    assert not output.exists()
    assert not output.with_name(output.name + ".tmp").exists()
