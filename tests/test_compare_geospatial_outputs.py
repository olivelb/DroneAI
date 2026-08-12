from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from tools.compare_geospatial_outputs import (
    _phase_correlation,
    compare_checkpoints,
    compare_dem,
    compare_ortho,
)


def _write_raster(
    path: Path,
    values: np.ndarray,
    *,
    transform: rasterio.Affine,
    nodata: float | None = None,
    crs: str = "EPSG:32631",
) -> None:
    bands = values[np.newaxis, :, :] if values.ndim == 2 else values
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=bands.shape[2],
        height=bands.shape[1],
        count=bands.shape[0],
        dtype=bands.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dataset:
        dataset.write(bands)


def test_compare_dem_uses_shared_grid_and_reports_bias(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate-dem.tif"
    reference = tmp_path / "reference-dem.tif"
    preview = tmp_path / "dem-difference.png"
    _write_raster(
        candidate,
        np.full((20, 20), 11.5, dtype=np.float32),
        transform=from_origin(0, 20, 1, 1),
        nodata=np.nan,
    )
    _write_raster(
        reference,
        np.full((10, 10), 10.0, dtype=np.float32),
        transform=from_origin(0, 20, 2, 2),
        nodata=-32767,
    )

    result = compare_dem(
        candidate,
        reference,
        max_dimension=64,
        preview_path=preview,
    )

    assert result["comparison_grid"] == {"width": 64, "height": 64}
    assert result["vertical_bias_m"] == 1.5
    assert result["bias_corrected_rmse_m"] == 0.0
    assert preview.is_file()


def test_compare_ortho_honours_reference_alpha(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate-ortho.tif"
    reference = tmp_path / "reference-ortho.tif"
    preview = tmp_path / "orthomosaic-comparison.png"
    candidate_rgb = np.full((3, 8, 8), 110, dtype=np.uint8)
    reference_rgba = np.full((4, 8, 8), 100, dtype=np.uint8)
    reference_rgba[3] = 255
    reference_rgba[3, :2, :] = 0
    transform = from_origin(0, 8, 1, 1)
    _write_raster(candidate, candidate_rgb, transform=transform)
    _write_raster(reference, reference_rgba, transform=transform)

    result = compare_ortho(
        candidate,
        reference,
        max_dimension=32,
        preview_path=preview,
    )

    assert result["rgb_mae"] == 10.0
    assert result["rgb_signed_bias_candidate_minus_reference"] == {
        "red": 10.0,
        "green": 10.0,
        "blue": 10.0,
    }
    assert result["candidate_coverage_of_reference"] == 1.0
    assert preview.is_file()


def test_compare_checkpoints_uses_roles_and_reports_plane_diagnostic(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate-dem.tif"
    reference = tmp_path / "reference-dem.tif"
    gcp_list = tmp_path / "gcp_list.txt"
    accuracy = tmp_path / "gcp_accuracy.csv"
    transform = from_origin(0, 4, 1, 1)
    _write_raster(
        candidate,
        np.full((4, 4), 11.0, dtype=np.float32),
        transform=transform,
        crs="EPSG:4326",
    )
    _write_raster(
        reference,
        np.full((4, 4), 10.0, dtype=np.float32),
        transform=transform,
        crs="EPSG:4326",
    )
    gcp_list.write_text(
        "EPSG:4326\n"
        "1.5 1.5 9.0 1 1 image-a.jpg 1\n"
        "2.5 1.5 9.0 1 1 image-a.jpg 2\n"
        "1.5 2.5 9.0 1 1 image-a.jpg 3\n"
        "2.5 2.5 9.0 1 1 image-a.jpg 4\n",
        encoding="utf-8",
    )
    accuracy.write_text(
        "point_id,horizontal_accuracy_m,vertical_accuracy_m,image_accuracy_px,role\n"
        "1,0.1,0.1,0.5,checkpoint\n"
        "2,0.1,0.1,0.5,checkpoint\n"
        "3,0.1,0.1,0.5,checkpoint\n"
        "4,0.1,0.1,0.5,adjustment\n",
        encoding="utf-8",
    )

    result = compare_checkpoints(candidate, reference, gcp_list, accuracy)

    assert result["checkpoint_count"] == 3
    assert result["candidate_minus_survey_m"]["rmse"] == 2.0
    assert result["reference_minus_survey_m"]["rmse"] == 1.0
    assert result["candidate_minus_reference_m"]["rmse"] == 1.0
    assert result["candidate_error_plane_diagnostic"][
        "plane_corrected_rmse_m"
    ] == 0.0


def test_phase_correlation_reports_candidate_translation() -> None:
    rng = np.random.default_rng(7)
    reference_gray = rng.normal(size=(64, 64)).astype(np.float32)
    reference = np.repeat(reference_gray[:, :, np.newaxis], 3, axis=2)
    candidate = np.roll(reference, shift=(3, -4), axis=(0, 1))

    result = _phase_correlation(
        reference,
        candidate,
        np.ones((64, 64), dtype=bool),
        pixel_size_m=(0.5, 0.5),
    )

    shift = result["grayscale"]["candidate_relative_to_reference_px"]
    assert shift[0] == pytest.approx(-4, abs=0.2)
    assert shift[1] == pytest.approx(3, abs=0.2)
