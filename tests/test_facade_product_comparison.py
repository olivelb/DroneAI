from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from tools.compare_facade_products import (
    compare_depth_arrays,
    open_binary_ply_vertices,
    rasterize_reference_ply,
)


def _write_ply(
    path: Path,
    points: NDArray[np.float64],
    colours: NDArray[np.uint8],
) -> None:
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(points)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "end_header\n"
    ).encode("ascii")
    dtype = np.dtype(
        [
            ("x", "<f4"),
            ("y", "<f4"),
            ("z", "<f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
        ]
    )
    records: np.ndarray = np.empty(len(points), dtype=dtype)
    for index, name in enumerate(("x", "y", "z")):
        records[name] = points[:, index]
    for index, name in enumerate(("red", "green", "blue")):
        records[name] = colours[:, index]
    with path.open("wb") as stream:
        stream.write(header)
        records.tofile(stream)


def test_binary_ply_reference_is_memory_mapped_and_rasterized(
    tmp_path: Path,
) -> None:
    x: NDArray[np.float64]
    z: NDArray[np.float64]
    x, z = np.meshgrid(np.linspace(0, 2, 40), np.linspace(0, 1, 25))
    y = 4.0 + 0.03 * np.sin(x * 3)
    points = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
    colours = np.column_stack(
        (
            np.full(len(points), 120),
            np.linspace(20, 220, len(points)),
            np.full(len(points), 80),
        )
    ).astype(np.uint8)
    path = tmp_path / "reference.ply"
    _write_ply(path, points, colours)

    opened = open_binary_ply_vertices(path)
    raster = rasterize_reference_ply(
        path,
        requested_resolution=0.05,
        maximum_pixels=100_000,
        chunk_size=120,
    )

    assert opened.count == len(points)
    assert isinstance(opened.records, np.memmap)
    assert raster.point_count >= int(len(points) * 0.95)
    assert raster.colour.shape[:2] == raster.depth.shape
    assert np.count_nonzero(raster.valid) > 400
    assert raster.resolution == pytest.approx(0.05)


def test_depth_comparison_recovers_orientation_and_plane_offset() -> None:
    reference = np.linspace(-0.4, 0.6, 400, dtype=np.float32).reshape(20, 20)
    candidate = -(reference - 3.25)
    common = np.ones_like(reference, dtype=bool)

    result = compare_depth_arrays(candidate, reference, common)

    assert result["candidate_depth_sign"] == -1
    assert result["fitted_plane_offset_m"] == pytest.approx(3.25, abs=1e-6)
    assert result["median_absolute_error_m"] == pytest.approx(0.0, abs=1e-6)
    assert result["p95_absolute_error_m"] == pytest.approx(0.0, abs=1e-6)


def test_depth_comparison_rejects_tiny_overlap() -> None:
    values: NDArray[np.float32] = np.zeros((20, 20), dtype=np.float32)
    common = np.zeros_like(values, dtype=bool)
    common[:2, :2] = True

    with pytest.raises(ValueError, match="too little common coverage"):
        compare_depth_arrays(values, values, common)
