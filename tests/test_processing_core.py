import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

APP3_ROOT = Path(__file__).resolve().parents[1] / "app3-processing"
sys.path.insert(0, str(APP3_ROOT))

import processing_core  # noqa: E402
from shared import detection_geometry  # noqa: E402
from processing_core import (  # noqa: E402
    build_tile_starts,
    dedupe_mission_detections,
    detections_to_geojson,
    render_annotated_orthomosaic,
    write_orthomosaic_tiles,
)


def _write_test_raster(path, width=1200, height=900):
    data = np.zeros((3, height, width), dtype=np.uint8)
    data[0] = 80
    data[1] = 120
    data[2] = 160
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=3,
        dtype="uint8",
        crs="EPSG:32631",
        transform=from_origin(500000, 4800000, 0.1, 0.1),
    ) as dst:
        dst.write(data)


def _detection(center_x, center_y, confidence, offset=0):
    return {
        "global_pixel_x": center_x,
        "global_pixel_y": center_y,
        "confidence": confidence,
        "class_id": 0,
        "class_name": "small-vehicle",
        "segment": [
            [center_x - 5 + offset, center_y - 3],
            [center_x + 5 + offset, center_y - 3],
            [center_x + 5 + offset, center_y + 3],
            [center_x - 5 + offset, center_y + 3],
        ],
    }


def test_tile_starts_cover_the_last_image_edge():
    starts = build_tile_starts(1200, 512, 128)

    assert starts == [0, 384, 688]
    assert starts[-1] + 512 == 1200


def test_overlapping_detections_are_deduplicated():
    detections = [
        _detection(100, 100, 0.80),
        _detection(102, 100, 0.70, offset=1),
        _detection(300, 300, 0.90),
    ]

    deduped = dedupe_mission_detections(detections)

    assert len(deduped) == 2
    assert {item["confidence"] for item in deduped} == {0.8, 0.9}


def test_spatial_dedupe_avoids_comparing_unrelated_detections(monkeypatch):
    detections = [_detection(index * 100, index * 100, 0.8) for index in range(500)]
    comparison_count = 0
    original = detection_geometry.are_duplicate_detections

    def counted_comparison(*args, **kwargs):
        nonlocal comparison_count
        comparison_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        detection_geometry,
        "are_duplicate_detections",
        counted_comparison,
    )

    deduped = processing_core.dedupe_mission_detections(detections)

    assert len(deduped) == len(detections)
    assert comparison_count < len(detections)


def test_spatial_dedupe_preserves_large_polygon_containment():
    containing = {
        **_detection(5000, 5000, 0.9),
        "segment": [
            [0, 0],
            [10000, 0],
            [10000, 10000],
            [0, 10000],
        ],
    }
    contained = _detection(9000, 9000, 0.8)

    deduped = dedupe_mission_detections([contained, containing])

    assert deduped == [containing]


def test_tiling_geojson_and_render_preserve_geospatial_metadata(tmp_path):
    source = tmp_path / "source.tif"
    _write_test_raster(source)

    tiles, metadata = write_orthomosaic_tiles(
        source,
        tmp_path / "tiles",
        tile_size=512,
        overlap=128,
    )
    assert len(tiles) == 6
    assert metadata["tiles_written"] == 6
    assert all(Path(tile["path"]).is_file() for tile in tiles)

    detection = _detection(100, 100, 0.8)
    with rasterio.open(source) as dataset:
        geojson = detections_to_geojson(
            [detection],
            dataset.transform,
            dataset.crs.to_string(),
        )
    ring = geojson["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert len(ring) == 5

    output = tmp_path / "annotated.tif"
    render_annotated_orthomosaic(source, output, [detection])
    with rasterio.open(output) as rendered:
        assert rendered.crs.to_string() == "EPSG:32631"
        assert rendered.transform == from_origin(500000, 4800000, 0.1, 0.1)
        assert rendered.count == 3

    json.dumps(geojson)
