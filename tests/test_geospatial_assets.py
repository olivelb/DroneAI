import json
from pathlib import Path

import numpy as np
import rasterio
import pytest
from PIL import Image
from rasterio.transform import from_bounds

from shared.geospatial_assets import (
    _rgba_image,
    convert_to_cog,
    detections_feature_collection,
    metadata_path,
    preview_path,
    render_cog_tile,
)


def _write_test_raster(path: Path) -> None:
    data = np.zeros((3, 1024, 1024), dtype=np.uint8)
    data[0] = np.arange(1024, dtype=np.uint16)[None, :] % 256
    data[1] = np.arange(1024, dtype=np.uint16)[:, None] % 256
    data[2] = 127
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=1024,
        height=1024,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_bounds(-1000, -1000, 1000, 1000, 1024, 1024),
    ) as destination:
        destination.write(data)


def test_convert_to_cog_creates_tiles_overviews_metadata_and_preview(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    _write_test_raster(raster)

    metadata = convert_to_cog(raster, preview_maximum_size=512)

    with rasterio.open(raster) as source:
        assert source.profile["tiled"]
        assert source.overviews(1)
    assert metadata["format"] == "COG"
    assert metadata["bounds"]["wgs84"]
    assert metadata_path(raster).is_file()
    assert preview_path(raster).is_file()
    with Image.open(preview_path(raster)) as image:
        assert max(image.size) <= 512


def test_convert_to_cog_accepts_single_tile_raster_without_overviews(tmp_path):
    raster = tmp_path / "small-orthomosaic.tif"
    data = np.zeros((3, 352, 500), dtype=np.uint8)
    with rasterio.open(
        raster,
        "w",
        driver="GTiff",
        width=500,
        height=352,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_bounds(-100, -100, 100, 100, 500, 352),
    ) as destination:
        destination.write(data)

    metadata = convert_to_cog(raster, block_size=512)

    with rasterio.open(raster) as source:
        assert source.profile["tiled"]
        assert source.block_shapes == [(512, 512)] * 3
        assert source.overviews(1) == []
    assert metadata["overviews"] == []
    assert metadata_path(raster).is_file()
    assert preview_path(raster).is_file()


def test_float_cog_metadata_is_strict_json_when_nodata_is_nan(tmp_path):
    raster = tmp_path / "height.tif"
    data = np.zeros((1, 64, 64), dtype=np.float32)
    with rasterio.open(
        raster,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=1,
        dtype="float32",
        nodata=np.nan,
        crs="EPSG:3857",
        transform=from_bounds(-10, -10, 10, 10, 64, 64),
    ) as destination:
        destination.write(data)

    metadata = convert_to_cog(raster)
    metadata_text = metadata_path(raster).read_text(encoding="utf-8")

    assert metadata["nodata"] is None
    assert metadata["display_ranges"] == [[0.0, 1.0]]
    assert "NaN" not in metadata_text
    assert json.loads(metadata_text)["nodata"] is None


def test_render_cog_tile_reads_a_bounded_web_mercator_tile(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    _write_test_raster(raster)
    convert_to_cog(raster)

    tile = render_cog_tile(raster, z=0, x=0, y=0)

    with Image.open(tile) as image:
        assert image.size == (256, 256)
        assert image.mode == "RGBA"


def test_render_cog_tile_supports_explicit_rgb_bands_and_global_ranges(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    _write_test_raster(raster)

    tile = render_cog_tile(
        raster,
        z=0,
        x=0,
        y=0,
        band_indexes=[3, 2, 1],
        display_ranges=[[0, 255], [0, 255], [0, 255]],
    )

    with Image.open(tile) as image:
        assert image.size == (256, 256)
        assert image.mode == "RGBA"


def test_render_cog_tile_rejects_duplicate_or_missing_bands(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    _write_test_raster(raster)

    with pytest.raises(ValueError, match="one grayscale or three unique"):
        render_cog_tile(raster, z=0, x=0, y=0, band_indexes=[1, 1, 2])
    with pytest.raises(ValueError, match="outside the available band range"):
        render_cog_tile(raster, z=0, x=0, y=0, band_indexes=[4])


def test_depth_tiles_share_one_global_display_range():
    first = np.ma.array([[[0.0, 50.0]]], mask=False)
    second = np.ma.array([[[50.0, 100.0]]], mask=False)

    first_image = np.asarray(
        _rgba_image(first, colormap="depth", display_ranges=[[0.0, 100.0]])
    )
    second_image = np.asarray(
        _rgba_image(second, colormap="depth", display_ranges=[[0.0, 100.0]])
    )

    assert np.array_equal(first_image[0, 1], second_image[0, 0])


def test_detection_segments_are_published_as_wgs84_vectors():
    collection = detections_feature_collection(
        [
            {
                "id": 7,
                "tile_index": 2,
                "class_name": "vehicle",
                "confidence": 0.91,
                "segment": [[0, 0], [10, 0], [10, 10], [0, 10]],
            }
        ],
        geotransform=[500000, 0.1, 0, 4800000, 0, -0.1],
        source_crs="EPSG:32631",
        vol_id="mission-1",
    )

    feature = collection["features"][0]
    ring = feature["geometry"]["coordinates"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert ring[0] == ring[-1]
    assert feature["properties"]["class_name"] == "vehicle"
