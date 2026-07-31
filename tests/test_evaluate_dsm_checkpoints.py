import json

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")

from rasterio.transform import from_origin

from tools.evaluate_dsm_checkpoints import evaluate_dsm


def _write_checkpoint_report(path, crs="EPSG:32633"):
    path.write_text(
        json.dumps(
            {
                "model_crs": crs,
                "points": [
                    {
                        "point_id": "1",
                        "status": "ok",
                        "surveyed_xyz": [100.5, 199.5, 9.75],
                    },
                    {
                        "point_id": "2",
                        "status": "ok",
                        "surveyed_xyz": [101.5, 198.5, 12.5],
                    },
                    {
                        "point_id": "3",
                        "status": "ok",
                        "surveyed_xyz": [110.0, 190.0, 0.0],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_evaluate_dsm_samples_surveyed_xy_and_reports_nodata(tmp_path):
    raster_path = tmp_path / "height.tif"
    report_path = tmp_path / "gcp.json"
    _write_checkpoint_report(report_path)
    values = np.array(
        [
            [10.0, 11.0, 12.0],
            [13.0, np.nan, 15.0],
            [16.0, 17.0, 18.0],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        crs="EPSG:32633",
        transform=from_origin(100.0, 200.0, 1.0, 1.0),
        nodata=np.nan,
    ) as dataset:
        dataset.write(values, 1)

    report = evaluate_dsm(raster_path, report_path)

    assert report["successful_checkpoint_count"] == 1
    assert report["pixel_size_m"] == [1.0, 1.0]
    assert report["points"][0]["signed_error_m"] == pytest.approx(0.25)
    assert report["points"][1]["status"] == "nodata"
    assert report["points"][2]["status"] == "outside-raster"
    assert report["absolute_vertical_error_m"]["rmse"] == pytest.approx(0.25)


def test_evaluate_dsm_rejects_crs_mismatch(tmp_path):
    raster_path = tmp_path / "height.tif"
    report_path = tmp_path / "gcp.json"
    _write_checkpoint_report(report_path, crs="EPSG:2154")
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=1,
        height=1,
        count=1,
        dtype="float32",
        crs="EPSG:32633",
        transform=from_origin(100.0, 200.0, 1.0, 1.0),
    ) as dataset:
        dataset.write(np.array([[10.0]], dtype=np.float32), 1)

    with pytest.raises(ValueError, match="CRS mismatch"):
        evaluate_dsm(raster_path, report_path)
