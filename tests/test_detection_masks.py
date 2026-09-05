import numpy as np
import pytest
from affine import Affine
from pyproj import Transformer
from shared.detection_masks import mask_polygons, normalize_polygons, geographic_mask_geometry


def test_mask_preserves_holes_islands_and_one_pixel_features():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[1:9, 1:9] = 1
    mask[3:7, 3:7] = 0
    mask[12:19, 12] = 1
    polygons = normalize_polygons(mask_polygons(mask), offset_x=20, offset_y=30)
    geometry = geographic_mask_geometry(polygons, Affine.identity(), Transformer.from_crs(4326, 4326))
    assert geometry["type"] == "MultiPolygon"
    polygons = geometry["coordinates"]
    def area(ring):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))) / 2
    assert sum(area(p[0]) - sum(area(hole) for hole in p[1:]) for p in polygons) == int(mask.sum())
    assert len(polygons) == 2
    assert sum(len(p) - 1 for p in polygons) == 1


def test_empty_mask_and_invalid_geometry():
    assert mask_polygons(np.zeros((5, 5))) == []
    with pytest.raises(ValueError, match="closed"):
        normalize_polygons([[[[0, 0], [1, 0], [1, 1]]]])
