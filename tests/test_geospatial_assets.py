from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds

from shared.geospatial_assets import (
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


def test_render_cog_tile_reads_a_bounded_web_mercator_tile(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    _write_test_raster(raster)
    convert_to_cog(raster)

    tile = render_cog_tile(raster, z=0, x=0, y=0)

    with Image.open(tile) as image:
        assert image.size == (256, 256)
        assert image.mode == "RGBA"


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
