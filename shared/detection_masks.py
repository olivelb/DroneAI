"""Complete pixel-edge mask geometry, including holes and disjoint islands."""
from __future__ import annotations

import math
from typing import Any
import numpy as np
from numpy.typing import NDArray

Polygons = list[list[list[list[float]]]]


def mask_polygons(mask: NDArray[Any]) -> Polygons:
    from rasterio.features import shapes
    binary = (np.asarray(mask) > 0).astype(np.uint8)
    if binary.ndim != 2:
        raise ValueError("Segmentation mask must be two-dimensional")
    # Exact pixel edges: no simplification that could erase a one-pixel object.
    return [geometry["coordinates"] for geometry, value in shapes(binary, mask=binary.astype(bool)) if value == 1]


def normalize_polygons(raw: object, *, offset_x: float = 0, offset_y: float = 0) -> Polygons:
    if not isinstance(raw, list):
        raise ValueError("Mask polygons must be a list")
    polygons: Polygons = []
    for polygon in raw:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("Mask polygon must contain an exterior ring")
        rings = []
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Mask rings must be closed and contain at least four points")
            points = []
            for point in ring:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("Mask coordinates must be pairs")
                x, y = float(point[0]), float(point[1])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("Mask coordinates must be finite")
                points.append([x + offset_x, y + offset_y])
            rings.append(points)
        polygons.append(rings)
    return polygons


def geographic_mask_geometry(polygons: Polygons, transform: Any, transformer: Any) -> dict[str, Any]:
    coordinates = [
        [[[float(value) for value in transformer.transform(*(transform * tuple(point)))]
          for point in ring] for ring in polygon]
        for polygon in polygons
    ]
    return {"type": "Polygon", "coordinates": coordinates[0]} if len(coordinates) == 1 else {
        "type": "MultiPolygon", "coordinates": coordinates,
    }
